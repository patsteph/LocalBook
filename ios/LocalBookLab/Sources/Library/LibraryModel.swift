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
    private(set) var sourceScoreIsRelevance = false
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
    var reranker: RerankerEngine?
    private(set) var rerankerLine = "Reranker: not loaded"
    private(set) var recipe: AnswerRecipe?
    private var personalCache: [String: PersonalContext] = [:]
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
        personalCache.removeAll()
        guard let store else { return }
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
        let nb = scopeToNotebook
        Task.detached { await store.warmUp(notebook: nb) }          // build that notebook's vector block before the first question
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
        await prepareReranker()
    }

    /// Load the Core ML reranker (the Mac's own model) and check it against scores the Mac produced.
    func prepareReranker() async {
        guard reranker == nil else { return }
        guard RerankerEngine.resourceAvailable() else {
            rerankerLine = "Reranker: not in this build — results are ordered by vectors and keywords only"
            return
        }
        do {
            rerankerLine = "Reranker: loading…"
            let r = try await RerankerEngine()
            let c = try await r.check()
            reranker = r
            rerankerLine = "Reranker (the Mac's MiniLM-L-12): token match \(c.encodingMatches)/\(c.encodingTotal) · "
                + "score agreement with the Mac \(String(format: "%.2f", c.rankCorrelation)) · same top 5 \(Int(c.top5Overlap * 100))% · "
                + "\(Int(c.millisecondsPerBatch)) ms per 12 passages"
        } catch { rerankerLine = "Reranker failed: \(error.localizedDescription)" }
    }

    struct Retrieval: Sendable {
        let hits: [LibraryHit]            // the passages the answer may cite; score = the Mac's confidence
        let reranked: Bool
        let candidates: Int               // how many passages the reranker was given
        let scanned: Int
        let embedSeconds: Double
        let seconds: Double
        let nothingRelevant: Bool         // the Mac would answer "I don't have enough relevant information"
        var pool: [Ranked] = []           // the reranked, boosted passages before the keep/drop rules (what a retry starts from)
    }
    struct Ranked: Sendable { let hit: LibraryHit; let rerank: Double }

    /// Retrieval the way the Mac does it (rag_engine.query): expand the question with the Mac's rule-based tables,
    /// fuse the nearest vectors and BM25 into `hybridK` candidates, rerank them with the cross-encoder (against the
    /// ORIGINAL question), keep `rerankKeep`, turn each into a citation with the Mac's confidence and drop the weak
    /// ones. Without the reranker, the fused order is used.
    func retrieve(question: String, notebook: String?) async throws -> Retrieval {
        guard let store, let engine, let tokenizer else { throw LibraryError.badResponse("search model not ready") }
        let t0 = Date()
        let expanded = recipe?.expand(question) ?? question
        let text = queryTemplate.replacingOccurrences(of: "{text}", with: expanded)
        let vector = try await engine.embed(tokenIds: TokenRepair.spaceFallback(tokenizer.encode(text: text)))
        let embedSeconds = Date().timeIntervalSince(t0)

        var params = LibraryStore.SearchParams()
        let keep = recipe?.retrieval.rerankKeep ?? 5
        if let r = recipe?.retrieval {
            params = .init(vectorCandidates: r.vectorCandidates, bm25Candidates: r.bm25Candidates, hybridK: r.hybridK,
                           rrfK: Double(r.rrfK), missingRank: Double(r.rrfMissingRank), k1: r.bm25.k1, b: r.bm25.b, epsilon: r.bm25.epsilon)
        }
        let found = try await store.search(query: vector, queryText: expanded, params: params, notebook: notebook)
        var ranked: [(hit: LibraryHit, rerank: Double)] = found.hits.map { ($0, 0) }
        var didRerank = false
        if let reranker, found.hits.count > 1 {
            let scores = try await reranker.scores(query: question, passages: found.hits.map(\.text))
            ranked = zip(found.hits, scores).map { ($0, Double($1)) }.sorted { $0.1 > $1.1 }
            didRerank = true
        }
        var top = Array(ranked.prefix(keep))
        // The Mac then re-orders those passages by entity and source-type boosts (rag_engine steps 3c/3d).
        if didRerank, let r = recipe {
            let scored = top.enumerated().map { i, item in
                AnswerRecipe.Scored(sourceId: item.hit.sourceId, text: item.hit.text, sourceType: item.hit.sourceType,
                                    filename: item.hit.filename, rerank: item.rerank, score: item.rerank, index: i)
            }
            let personal = await personal(for: notebook)
            top = r.boosted(scored, question: question, personal: personal).map { top[$0.index] }
        }
        let pool = top.map { Ranked(hit: $0.hit, rerank: $0.rerank) }
        let (cited, nothing) = cite(pool, didRerank: didRerank)
        return Retrieval(hits: cited, reranked: didRerank, candidates: found.hits.count, scanned: found.scanned,
                         embedSeconds: embedSeconds, seconds: Date().timeIntervalSince(t0), nothingRelevant: nothing, pool: pool)
    }

    /// Confidence and the keep/drop rules are the Mac's (rag_context.build_citations_and_context).
    func cite(_ top: [Ranked], didRerank: Bool) -> (hits: [LibraryHit], nothing: Bool) {
        var cited = top.map { item -> LibraryHit in
            let conf = recipe?.confidence(rerank: didRerank ? item.rerank : 0, rrfNormalised: Double(item.hit.rrf)) ?? Double(item.hit.rrf)
            return LibraryHit(notebookId: item.hit.notebookId, notebookTitle: item.hit.notebookTitle, sourceId: item.hit.sourceId,
                              chunkIndex: item.hit.chunkIndex, filename: item.hit.filename, text: item.hit.text,
                              score: Float(conf), rrf: item.hit.rrf, sourceType: item.hit.sourceType)
        }
        var nothing = false
        if let rules = recipe?.citations {
            let best = cited.map { Double($0.score) }.max() ?? 0
            let strong = cited.filter { Double($0.score) >= rules.minConfidence }
            if best < rules.veryLowConfidence { cited = []; nothing = true }
            else if strong.isEmpty { cited = Array(cited.prefix(rules.fallbackKeep)) }
            else { cited = strong }
        }
        return (cited, nothing)
    }

    /// The Mac's second look (rag_search.corrective_retrieval): search again with rewritten queries, add the new
    /// passages to the first set (capped), rerank the lot and keep the best. Nil when it would find nothing new.
    func correctiveRetrieve(question: String, notebook: String?, first: [Ranked]) async throws -> Retrieval? {
        guard let store, let engine, let tokenizer, let reranker, let r = recipe, let c = r.corrective else { return nil }
        let rt = r.retrieval
        let t0 = Date()
        let variants = r.queryVariants(question)
        guard variants.count > 1 else { return nil }
        func key(_ h: LibraryHit) -> String { "\(h.sourceId)/\(h.chunkIndex)" }
        var all = first.map(\.hit)
        var seen = Set(all.map(key))
        var params = LibraryStore.SearchParams(vectorCandidates: c.vectorCandidates, bm25Candidates: c.bm25Candidates, hybridK: c.hybridK,
                                               rrfK: Double(rt.rrfK), missingRank: Double(rt.rrfMissingRank), k1: rt.bm25.k1, b: rt.bm25.b, epsilon: rt.bm25.epsilon)
        params.hybridK = c.hybridK
        var scanned = 0
        for v in variants.dropFirst() {
            let text = queryTemplate.replacingOccurrences(of: "{text}", with: v)
            let vector = try await engine.embed(tokenIds: TokenRepair.spaceFallback(tokenizer.encode(text: text)))
            let found = try await store.search(query: vector, queryText: v, params: params, notebook: notebook)
            scanned = max(scanned, found.scanned)
            for h in found.hits where !seen.contains(key(h)) { all.append(h); seen.insert(key(h)) }
        }
        all = Array(all.prefix(c.resultCap))
        guard all.count > first.count else { return nil }
        let scores = try await reranker.scores(query: question, passages: all.map(\.text))
        let top = zip(all, scores).map { Ranked(hit: $0, rerank: Double($1)) }.sorted { $0.rerank > $1.rerank }.prefix(c.rerankKeep)
        let (cited, nothing) = cite(Array(top), didRerank: true)
        return Retrieval(hits: cited, reranked: true, candidates: all.count, scanned: scanned, embedSeconds: 0,
                         seconds: Date().timeIntervalSince(t0), nothingRelevant: nothing, pool: Array(top))
    }

    /// The Mac's profile, core memory, entities and relationships for a notebook, as last synced.
    func personal(for notebook: String?) async -> PersonalContext? {
        guard let notebook, let store else { return nil }
        if let c = personalCache[notebook] { return c }
        guard let json = await store.meta("personal:" + notebook), let data = json.data(using: .utf8),
              let p = try? JSONDecoder().decode(PersonalContext.self, from: data) else { return nil }
        personalCache[notebook] = p
        return p
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
        guard store != nil, engine != nil, tokenizer != nil else { throw LibraryError.badResponse("search model not ready") }
        let t0 = Date()
        var result = try await retrieve(question: question, notebook: notebook)
        let personal = await personal(for: notebook)
        var gen = try await generateOnce(question: question, result: result, personal: personal, withEntityContext: true)
        // The Mac's second look: a weak answer retriggers retrieval with rewritten queries and answers again.
        if let r = recipe, !r.answerLooksGood(question: question, answer: gen.text, type: r.classify(question)),
           let second = try await correctiveRetrieve(question: question, notebook: notebook, first: result.pool) {
            result = second
            gen = try await generateOnce(question: question, result: result, personal: personal, withEntityContext: false)
        }
        return LocalAnswer(sources: result.nothingRelevant ? [] : result.hits, answer: gen.text, seconds: Date().timeIntervalSince(t0),
                           firstWordSeconds: gen.firstWord, tokensPerSecond: gen.tokensPerSecond)
    }

    /// One answer for one retrieval (UI-free). Nothing relevant means the Mac's refusal, without asking the model.
    private func generateOnce(question: String, result: Retrieval, personal: PersonalContext?, withEntityContext: Bool)
        async throws -> (text: String, firstWord: Double?, tokensPerSecond: Double?) {
        if result.nothingRelevant || result.hits.isEmpty {
            return (recipe?.citations.noAnswer ?? "I don't have enough relevant information in your documents to answer this question accurately.", nil, nil)
        }
        if gemma == nil {
            Memory.cacheLimit = 64 * 1024 * 1024
            gemma = try await #huggingFaceLoadModelContainer(configuration: LLMRegistry.gemma4_e4b_it_4bit)
        }
        guard let gemma else { throw LibraryError.badResponse("answer model not loaded") }
        let setup = generationSetup(question: question, hits: result.hits, personal: personal, withEntityContext: withEntityContext)
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
        return (setup.clean(answer, info?.stopReason == .length), first, info?.tokensPerSecond)
    }

    /// What the phone gives its model: the Mac's own prompts, decode settings and passage framing when the
    /// recipe has been synced, otherwise the short built-in fallback.
    func generationSetup(question: String, hits: [LibraryHit], personal: PersonalContext? = nil, withEntityContext: Bool = true) -> (instructions: String, prompt: String, params: GenerateParameters, clean: (String, Bool) -> String) {
        if let r = recipe {
            return (r.systemPrompt(for: question, personal: personal),
                    r.userPrompt(question: question, passages: hits.map(\.text), suffix: withEntityContext ? r.contextSuffix(question: question, personal: personal) : ""),
                    r.generateParameters,
                    { text, cutOff in
                        let fixed = r.fixCitations(r.clean(text), count: hits.count)
                        return cutOff ? r.trimUnfinished(fixed) : fixed
                    })
        }
        let sources = hits.enumerated().map { i, h in "[\(i + 1)] (\(h.filename))\n\(String(h.text.prefix(1_100)))" }.joined(separator: "\n\n")
        return (Self.localInstructions, "Passages:\n\n\(sources)\n\nQuestion: \(question)", GenerateParameters(maxTokens: 400), { text, _ in text })
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
        return try await store.closestNotebook(to: v)
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
                sourceScoreIsRelevance = false
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
        sourceScoreIsConfidence = true
        followUps = []
        guard store != nil, engine != nil, tokenizer != nil, !answering,
              !query.trimmingCharacters(in: .whitespaces).isEmpty, let scope = scopeToNotebook else { return }
        answering = true
        answerText = ""; answerSources = []
        defer { answering = false }
        do {
            answerStatus = "Finding passages (offline mode: vectors + keywords)…"
            let t0 = Date()
            var result = try await retrieve(question: query, notebook: scope)
            hits = result.hits
            sourceScoreIsRelevance = false
            answerSources = result.hits
            let findSeconds = Date().timeIntervalSince(t0)
            var withContext = true
            var corrected = false
            if result.nothingRelevant || result.hits.isEmpty {          // as the Mac: no relevant passages, no guessing…
                answerStatus = "Nothing relevant yet — looking again with rewritten queries…"
                if let second = try await correctiveRetrieve(question: query, notebook: scope, first: result.pool),     // …but it looks again first
                   !second.nothingRelevant, !second.hits.isEmpty {
                    result = second; corrected = true; withContext = false
                    hits = second.hits; answerSources = second.hits
                } else {
                    answerText = recipe?.citations.noAnswer ?? "I don't have enough relevant information in your documents to answer this question accurately."
                    answerStatus = "Passages in \(Int(findSeconds * 1000)) ms · nothing relevant enough"
                    return
                }
            }

            if gemma == nil {
                answerStatus = "Loading the answer model (4 GB)…"
                Memory.cacheLimit = 64 * 1024 * 1024
                gemma = try await #huggingFaceLoadModelContainer(configuration: LLMRegistry.gemma4_e4b_it_4bit)
            }
            guard let gemma else { return }
            let personal = await personal(for: scope)
            var current = result
            var line = ""
            while true {
                let setup = generationSetup(question: query, hits: current.hits, personal: personal, withEntityContext: withContext)
                let prompt = setup.prompt
                let session = ChatSession(gemma, instructions: setup.instructions, generateParameters: setup.params)
                answerStatus = corrected ? "Looking again, then answering…" : "Answering…"
                answerText = ""
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
                if !answerText.isEmpty { answerText = setup.clean(answerText, info?.stopReason == .length) }
                let cancelled = task.isCancelled
                line = "Passages in \(Int(findSeconds * 1000)) ms"
                if let firstWord { line += " · first word \(String(format: "%.1f", firstWord)) s" }
                if let info { line += " · \(String(format: "%.0f", info.tokensPerSecond)) tok/s · \(info.generationTokenCount) tokens" }
                line += " · memory \(Int(MemoryProbe.footprintMB())) MB"
                if cancelled { line += " · stopped"; break }
                // The Mac's second look: a weak answer retriggers retrieval with rewritten queries and one more answer.
                guard !corrected, let r = recipe, !r.answerLooksGood(question: query, answer: answerText, type: r.classify(query)) else { break }
                corrected = true
                answerStatus = "Looking again with rewritten queries…"
                guard let second = try await correctiveRetrieve(question: query, notebook: scope, first: current.pool) else { break }
                current = second
                withContext = false
                hits = second.hits
                answerSources = second.hits
                if second.nothingRelevant || second.hits.isEmpty {
                    answerText = r.citations.noAnswer
                    line += " · nothing relevant after a second look"
                    break
                }
            }
            if corrected { line += " · after a second look" }
            answerStatus = line
        } catch {
            answerStatus = "Could not answer: \(error.localizedDescription)"
        }
    }

    func search() async {
        guard let store, let engine, let tokenizer, !query.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        do {
            let result = try await retrieve(question: query, notebook: scopeToNotebook)
            let embedSeconds = result.embedSeconds
            hits = result.hits
            sourceScoreIsConfidence = true
            sourceScoreIsRelevance = false
            timing = "Question embedded in \(Int(embedSeconds * 1000)) ms · scanned \(result.scanned) chunks in \(Int(result.seconds * 1000)) ms"
        } catch { searchState = .failed(error.localizedDescription) }
    }
}
