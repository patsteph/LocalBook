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

    private struct MacAnswer: Decodable {
        struct Citation: Decodable {
            let number: Int?; let sourceId: String; let filename: String?; let chunkIndex: Int?
            let text: String; let confidence: Double?
        }
        let answer: String; let citations: [Citation]; let followUpQuestions: [String]
        let lowConfidence: Bool; let cacheHit: Bool
    }
    private(set) var answering = false
    private var generation: Task<Void, Never>?
    private var gemma: ModelContainer?
    var query = ""
    var scopeToNotebook: String?

    private var store: LibraryStore?
    private var engine: EmbeddingGemma2Engine?
    private var tokenizer: (any Tokenizers.Tokenizer)?
    private var queryTemplate = "task: search result | query: {text}"

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
        notebooks = await store.notebooks()
        chunkCount = await store.chunkCount()
        if let t = await store.meta("last_sync") { lastSyncLine = "Last synced \(t)" }
        if let q = await store.meta("query_template") { queryTemplate = q }
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

    /// Dispatch on the chosen mode.
    func ask() async {
        switch askMode {
        case .mac: await askMac()
        case .phone: await askOnPhone()
        }
    }

    /// The Mac's own pipeline (query analysis, hybrid search, re-rank, its answer model) answers; the phone
    /// only shows it. Counts as a normal question on the Mac (its answer cache and memory see it).
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
            let sources = result.hits.enumerated().map { i, h in
                "[\(i + 1)] (\(h.filename))\n\(String(h.text.prefix(1_100)))"
            }.joined(separator: "\n\n")
            let instructions = "You answer questions using ONLY the numbered passages from the user's own notes. "
                + "If they do not contain the answer, say so plainly. Be concise. Cite the passages you used like [1] or [2]."
            let prompt = "Passages:\n\n\(sources)\n\nQuestion: \(query)"
            let session = ChatSession(gemma, instructions: instructions, generateParameters: GenerateParameters(maxTokens: 400))

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
