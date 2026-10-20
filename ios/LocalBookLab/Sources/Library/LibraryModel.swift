import Foundation
import HuggingFace
import MLX
import MLXHuggingFace
import MLXLLM
import MLXLMCommon
import Observation
import Tokenizers
import UIKit

/// Phase 1, item 2: the phone's copy of the Mac's library — sync it, then search it with the phone's
/// own embedder. Searching the Mac's vectors with the phone's embedder is exactly what test 1 proved.
@MainActor
@Observable
final class LibraryModel {
    enum SyncState: Equatable { case idle, syncing(String), failed(String) }
    enum SearchState: Equatable { case notReady, preparing(String), ready, failed(String) }

    private(set) var syncState: SyncState = .idle
    private(set) var searchState: SearchState = .notReady
    private(set) var notebooks: [LibraryNotebookInfo] = []
    private(set) var chunkCount = 0
    private(set) var lastSyncLine = "Never synced"
    private(set) var lastSummary: LibrarySync.Summary?
    private(set) var hits: [LibraryHit] = []
    private(set) var timing = ""
    private(set) var answerText = ""
    private(set) var answerSources: [LibraryHit] = []
    private(set) var answerStatus = ""
    private(set) var followUps: [String] = []
    private(set) var sourceScoreIsConfidence = false
    var askMode: AskMode = .mac
    enum AskMode: String, CaseIterable, Identifiable {
        case mac = "Ask the Mac", phone = "On this phone"
        var id: String { rawValue }
    }

    struct MacAnswer: Decodable {
        struct Citation: Decodable {
            let number: Int?; let sourceId: String; let filename: String?; let chunkIndex: Int?
            let text: String; let confidence: Double?
        }
        let answer: String; let citations: [Citation]; let followUpQuestions: [String]
        let lowConfidence: Bool; let cacheHit: Bool
    }
    private(set) var answering = false
    private var generation: Task<Void, Never>?
    var gemma: ModelContainer?
    var query = ""
    var scopeToNotebook: String?

    var store: LibraryStore?
    var engine: EmbeddingGemma2Engine?
    var tokenizer: (any Tokenizers.Tokenizer)?
    var queryTemplate = "task: search result | query: {text}"
    private(set) var recipe: AnswerRecipe?
    private(set) var recipeLine = "No answer recipe yet — sync first"

    func open() async {
        guard store == nil else { return }
        do {
            let s = try LibraryStore()
            try await s.prepare()
            store = s
            await refresh()
        } catch { syncState = .failed(error.localizedDescription) }
    }

    func refresh() async {
        guard let store else { return }
        Task.detached { await store.warmUp() }              // build the in-memory vector block before the first question
        notebooks = await store.notebooks()
        chunkCount = await store.chunkCount()
        if let t = await store.meta("last_sync") { lastSyncLine = "Last synced \(t)" }
        if let q = await store.meta("query_template") { queryTemplate = q }
        if let json = await store.meta("recipe"), let data = json.data(using: .utf8) {
            let d = JSONDecoder(); d.keyDecodingStrategy = .convertFromSnakeCase
            recipe = try? d.decode(AnswerRecipe.self, from: data)
            if let r = recipe {
                let problems = r.check()
                recipeLine = problems.isEmpty ? "Answers follow the Mac's recipe \(r.recipeVersion) (all \(r.conformance.classification.count + r.conformance.cleaning.count) checks pass)"
                                              : "Mac recipe \(r.recipeVersion): \(problems.count) check(s) FAIL — \(problems[0])"
            }
        }
        // The app searches one notebook at a time (only the curator sees across all of them).
        if scopeToNotebook == nil || !notebooks.contains(where: { $0.id == scopeToNotebook }) {
            scopeToNotebook = notebooks.max(by: { $0.chunks < $1.chunks })?.id
        }
    }

    func sync() async {
        guard let store else { return }
        syncState = .syncing("Starting…")
        do {
            let summary = try await LibrarySync.run(store: store) { line in
                Task { @MainActor in if case .syncing = self.syncState { self.syncState = .syncing(line) } }
            }
            lastSummary = summary
            await refresh()
            syncState = .idle
        } catch {
            syncState = .failed(error.localizedDescription)
        }
    }

    /// Download/compile the search model if needed and load the tokenizer.
    func prepareSearch() async {
        guard engine == nil else { return }
        do {
            searchState = .preparing("Search model…")
            let location = try await EmbeddingModelStore.prepare { _ in }
            let e = try EmbeddingGemma2Engine(location: location)
            searchState = .preparing("Warming up (first time only)…")
            _ = try await e.warmUp()
            searchState = .preparing("Tokenizer…")
            tokenizer = try await AutoTokenizer.from(pretrained: LibrarySync.expectedModel)
            engine = e
            searchState = .ready
        } catch { searchState = .failed(error.localizedDescription) }
    }

    func stopAnswer() { generation?.cancel() }

    // MARK: UI-free building blocks (also used by the answer-parity test)

    struct LocalAnswer: Sendable {
        let sources: [LibraryHit]
        let answer: String
        let seconds: Double
        let firstWordSeconds: Double?
        let tokensPerSecond: Double?
    }

    /// The Mac's reference answer for one question, via the signed phone link (phone-only on the Mac).
    func macAnswer(question: String, notebook: String) async throws -> (answer: MacAnswer, seconds: Double) {
        guard let mac = MacLink.paired else { throw LibraryError.notPaired }
        let t0 = Date()
        let data = try await MacLink.signedCall(mac, path: "/phone/ask",
                                                json: ["notebook_id": notebook, "question": question], timeout: 240)
        let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
        return (try decoder.decode(MacAnswer.self, from: data), Date().timeIntervalSince(t0))
    }

    /// The phone's own retrieval (vectors + keywords) and Gemma answer for one question. Needs the search
    /// model ready; loads Gemma on first use.
    func localAnswer(question: String, notebook: String, topK: Int = 5, maxTokens: Int = 400) async throws -> LocalAnswer {
        guard let store, let engine, let tokenizer else { throw LibraryError.badResponse("search model not ready") }
        let t0 = Date()
        let text = queryTemplate.replacingOccurrences(of: "{text}", with: question)
        let vector = try await engine.embed(tokenIds: TokenRepair.spaceFallback(tokenizer.encode(text: text)))
        let result = try await store.search(query: vector, queryText: question, topK: topK, notebook: notebook)
        if gemma == nil {
            Memory.cacheLimit = 64 * 1024 * 1024
            gemma = try await #huggingFaceLoadModelContainer(configuration: LLMRegistry.gemma4_e4b_it_4bit)
        }
        guard let gemma else { throw LibraryError.badResponse("answer model not loaded") }
        let setup = generationSetup(question: question, hits: result.hits)
        let session = ChatSession(gemma, instructions: setup.instructions, generateParameters: setup.params)
        var answer = "", first: Double?, info: GenerateCompletionInfo?
        let started = Date()
        for try await item in session.streamDetails(to: setup.prompt) {
            switch item {
            case .chunk(let c): if first == nil { first = Date().timeIntervalSince(started) }; answer += c
            case .info(let i): info = i
            default: break
            }
        }
        return LocalAnswer(sources: result.hits, answer: setup.clean(answer), seconds: Date().timeIntervalSince(t0),
                           firstWordSeconds: first, tokensPerSecond: info?.tokensPerSecond)
    }

    /// What the phone gives its model: the Mac's own prompts, decode settings and passage framing when the
    /// recipe has been synced, otherwise the short built-in fallback.
    func generationSetup(question: String, hits: [LibraryHit]) -> (instructions: String, prompt: String, params: GenerateParameters, clean: (String) -> String) {
        if let r = recipe {
            return (r.systemPrompt(for: question), r.userPrompt(question: question, passages: hits.map(\.text)),
                    r.generateParameters, { r.clean($0) })
        }
        let sources = hits.enumerated().map { i, h in "[\(i + 1)] (\(h.filename))\n\(String(h.text.prefix(1_100)))" }.joined(separator: "\n\n")
        return (Self.localInstructions, "Passages:\n\n\(sources)\n\nQuestion: \(question)", GenerateParameters(maxTokens: 400), { $0 })
    }

    static let localInstructions = "You answer questions using ONLY the numbered passages from the user's own notes. "
        + "If they do not contain the answer, say so plainly. Be concise. Cite the passages you used like [1] or [2]. "
        + "Write plain text: no LaTeX or dollar-sign math (write arrows as ->), and no tables."

    /// A unit vector for `text` as a document (for comparing two answers with the phone's own embedder).
    func documentVector(_ text: String) async throws -> [Float] {
        guard let engine, let tokenizer else { throw LibraryError.badResponse("search model not ready") }
        let ids = TokenRepair.spaceFallback(tokenizer.encode(text: "title: none | text: " + String(text.prefix(1_500))))
        return try await engine.embed(tokenIds: Array(ids.prefix(EmbeddingGemma2Engine.maxTokens)))
    }

    /// The notebook whose best chunk matches the question most closely (across everything on the phone).
    func bestNotebook(for question: String) async throws -> String? {
        guard let store, let engine, let tokenizer else { throw LibraryError.badResponse("search model not ready") }
        let text = queryTemplate.replacingOccurrences(of: "{text}", with: question)
        let v = try await engine.embed(tokenIds: TokenRepair.spaceFallback(tokenizer.encode(text: text)))
        return try await store.search(query: v, queryText: nil, topK: 1, notebook: nil).hits.first?.notebookId
    }

    /// Dispatch on the chosen mode.
    func ask() async {
        switch askMode {
        case .mac: await askMac()
        case .phone: await askOnPhone()
        }
    }

    /// The Mac's own pipeline (query analysis, hybrid search, re-rank, its answer model) answers; the phone
    /// only shows it. The question stays phone-only: the Mac keeps no metrics row, cache entry or memory of it.
    private func askMac() async {
        guard let mac = MacLink.paired else { answerStatus = "Pair with a Mac first (row 3)."; return }
        guard !answering, !query.trimmingCharacters(in: .whitespaces).isEmpty, let scope = scopeToNotebook else { return }
        answering = true
        answerText = ""; answerSources = []; followUps = []; hits = []
        defer { answering = false }
        let title = notebooks.first { $0.id == scope }?.title ?? scope
        answerStatus = "Asking the Mac…"
        let started = Date()
        let question = query
        let task = Task { @MainActor in
            do {
                let data = try await MacLink.signedCall(mac, path: "/phone/ask",
                                                        json: ["notebook_id": scope, "question": question], timeout: 240)
                let decoder = JSONDecoder(); decoder.keyDecodingStrategy = .convertFromSnakeCase
                let r = try decoder.decode(MacAnswer.self, from: data)
                answerText = r.answer
                sourceScoreIsConfidence = true
                answerSources = r.citations.map {
                    LibraryHit(notebookId: scope, notebookTitle: title, sourceId: $0.sourceId, chunkIndex: $0.chunkIndex ?? 0,
                               filename: $0.filename ?? "Unknown", text: $0.text, score: Float($0.confidence ?? 0))
                }
                hits = answerSources
                followUps = r.followUpQuestions
                var line = "Answered by the Mac in \(String(format: "%.1f", Date().timeIntervalSince(started))) s · \(r.citations.count) sources"
                if r.cacheHit { line += " · from its cache" }
                if r.lowConfidence { line += " · low confidence" }
                answerStatus = line
            } catch is CancellationError {
                answerStatus = "Stopped"
            } catch {
                answerStatus = "Could not get an answer from the Mac: \(error.localizedDescription). Is it running? You can switch to “On this phone”."
            }
        }
        generation = Task { await task.value }
        await task.value
    }

    /// Free the answer model's memory (4+ GB) when the screen goes away.
    func unloadAnswerModel() {
        generation?.cancel()
        gemma = nil
    }

    /// Search the chosen notebook, then have the phone's own Gemma answer from those passages. Generation
    /// is cancelled the moment the app stops being active: iOS refuses GPU work in the background and
    /// a stuck generation freezes the app (measured in Phase 0).
    private func askOnPhone() async {
        sourceScoreIsConfidence = false
        followUps = []
        guard let store, let engine, let tokenizer, !answering,
              !query.trimmingCharacters(in: .whitespaces).isEmpty, let scope = scopeToNotebook else { return }
        answering = true
        answerText = ""; answerSources = []
        defer { answering = false }
        do {
            answerStatus = "Finding passages (offline mode: vectors + keywords)…"
            let t0 = Date()
            let text = queryTemplate.replacingOccurrences(of: "{text}", with: query)
            let vector = try await engine.embed(tokenIds: TokenRepair.spaceFallback(tokenizer.encode(text: text)))
            let result = try await store.search(query: vector, queryText: query, topK: 5, notebook: scope)
            hits = result.hits
            answerSources = result.hits
            let findSeconds = Date().timeIntervalSince(t0)

            if gemma == nil {
                answerStatus = "Loading the answer model (4 GB)…"
                Memory.cacheLimit = 64 * 1024 * 1024
                gemma = try await #huggingFaceLoadModelContainer(configuration: LLMRegistry.gemma4_e4b_it_4bit)
            }
            guard let gemma else { return }
            let setup = generationSetup(question: query, hits: result.hits)
            let prompt = setup.prompt
            let session = ChatSession(gemma, instructions: setup.instructions, generateParameters: setup.params)

            answerStatus = "Answering…"
            let started = Date()
            var firstWord: Double?
            var info: GenerateCompletionInfo?
            let task = Task { @MainActor in
                for try await item in session.streamDetails(to: prompt) {
                    switch item {
                    case .chunk(let s):
                        if firstWord == nil { firstWord = Date().timeIntervalSince(started) }
                        answerText += s
                    case .info(let i): info = i
                    default: break
                    }
                }
            }
            generation = Task { _ = try? await task.value }
            let observer = NotificationCenter.default.addObserver(
                forName: UIApplication.willResignActiveNotification, object: nil, queue: .main) { _ in
                MainActor.assumeIsolated { task.cancel() }
            }
            do { try await task.value } catch {}
            NotificationCenter.default.removeObserver(observer)
            if !answerText.isEmpty { answerText = setup.clean(answerText) }
            let cancelled = task.isCancelled
            var line = "Passages in \(Int(findSeconds * 1000)) ms"
            if let firstWord { line += " · first word \(String(format: "%.1f", firstWord)) s" }
            if let info { line += " · \(String(format: "%.0f", info.tokensPerSecond)) tok/s · \(info.generationTokenCount) tokens" }
            line += " · memory \(Int(MemoryProbe.footprintMB())) MB"
            if cancelled { line += " · stopped" }
            answerStatus = line
        } catch {
            answerStatus = "Could not answer: \(error.localizedDescription)"
        }
    }

    func search() async {
        guard let store, let engine, let tokenizer, !query.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        do {
            let t0 = Date()
            let text = queryTemplate.replacingOccurrences(of: "{text}", with: query)
            let ids = TokenRepair.spaceFallback(tokenizer.encode(text: text))
            let vector = try await engine.embed(tokenIds: ids)
            let embedSeconds = Date().timeIntervalSince(t0)
            let result = try await store.search(query: vector, queryText: query, topK: 5, notebook: scopeToNotebook)
            hits = result.hits
            timing = "Question embedded in \(Int(embedSeconds * 1000)) ms · scanned \(result.scanned) chunks in \(Int(result.seconds * 1000)) ms"
        } catch { searchState = .failed(error.localizedDescription) }
    }
}
