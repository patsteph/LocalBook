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
    enum ModelState: Equatable { case idle, loading, ready, failed(String) }
    private(set) var answerModelState: ModelState = .idle
    /// Experimental: read prompts in small chunks that stop when the screen locks (StoppableLM). was OFF by default — the first
    /// phone test of it ended in a hot, unstable phone, so it stays out until it has been proven in a controlled test.
    var useStoppablePrefill: Bool {
        get { UserDefaults.standard.object(forKey: "stoppablePrefill") as? Bool ?? true }          // on unless the person turned it off
        set { UserDefaults.standard.set(newValue, forKey: "stoppablePrefill") }
    }
    private(set) var notebooks: [LibraryNotebookInfo] = []
    private(set) var chunkCount = 0
    private(set) var lastSyncLine = "Never synced"
    private(set) var lastSummary: LibrarySync.Summary?
    private(set) var hits: [LibraryHit] = []
    private(set) var timing = ""
    private(set) var answerText = ""
    private(set) var answerSources: [LibraryHit] = []
    private(set) var answerStatus = ""
    private(set) var shownQuestion = ""
    private(set) var followUps: [String] = []
    /// The conversation so far (until the app closes): finished turns, oldest first. The turn being written is the
    /// live `shownQuestion` / `answerText` below them. Each turn remembers its notebook.
    private(set) var history: [AskTurn] = []
    private(set) var liveNotebookId: String?
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
    var scopeToNotebook: String? {
        didSet {
            // A turn belongs to the notebook it was asked in: it moves up into the conversation, tagged with that
            // notebook, so the new notebook starts with a clean answer area.
            guard oldValue != scopeToNotebook, !answering else { return }
            archiveLiveTurn()
        }
    }

    var store: LibraryStore?
    var flushingNotes = false
    var vision: VisionEncoder?
    enum VisionState: Equatable { case none, downloading(Double), ready, failed(String) }
    var visionState: VisionState = EmbeddingModelStore.visionInstalled ? .ready : .none
    var noteSendProblem: String?
    var engine: EmbeddingGemma2Engine?
    var tokenizer: (any Tokenizers.Tokenizer)?
    var queryTemplate = "task: search result | query: {text}"
    var reranker: RerankerEngine?
    private(set) var rerankerLine = "Reranker: not loaded"
    private(set) var recipe: AnswerRecipe?
    private var personalCache: [String: PersonalContext] = [:]
    private(set) var recipeLine = "No answer recipe yet — sync first"

    func open() async {
        GPUGate.installReopenOnActive()
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
        notebooks = await store.notebooks().filter { $0.digest != "partial" }     // not finished copying: hidden
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
            let last = UserDefaults.standard.string(forKey: "lastNotebook")
            scopeToNotebook = notebooks.first(where: { $0.id == last })?.id ?? notebooks.first?.id      // the desktop's order: first is the top one
        }
        let nb = scopeToNotebook
        Task.detached { await store.warmUp(notebook: nb) }          // build that notebook's vector block before the first question
    }

    func sync() async {
        guard let store else { return }
        syncState = .syncing("Starting…")
        do {
            let summary = try await LibrarySync.run(store: store, onNotebook: {
                Task { @MainActor in await self.refresh() }          // a finished notebook is usable while the rest copy
            }) { line in
                Task { @MainActor in if case .syncing = self.syncState { self.syncState = .syncing(line) } }
            }
            lastSummary = summary
            await refresh()
            syncState = .idle
            await flushNotes()
        } catch {
            syncState = .failed(error.localizedDescription)
        }
    }

    /// Load the search model, the tokenizer and the re-ranker at the same time (they use different parts of the
    /// phone, and the search model alone takes about 13 s), then open Ask.
    func prepareSearch() async {
        guard engine == nil else { return }
        let started = Date()
        searchState = .preparing("Search…")
        async let tokenizerLoad: any Tokenizers.Tokenizer = {
            let t = Date()
            let tok = try await Signpost.measure("tokenizer load") { try await AutoTokenizer.from(pretrained: LibrarySync.expectedModel) }
            Self.lap("tokenizer loaded", since: t)
            return tok
        }()
        async let rerankerLoad: Void = {
            let t = Date()
            await Signpost.measure("reranker load") { await self.prepareReranker() }
            Self.lap("reranker loaded", since: t)
        }()
        do {
            var t = Date()
            let location = try await EmbeddingModelStore.prepare { _ in }
            Self.lap("search files checked", since: t)
            let units: EmbeddingGemma2Engine.Units = UserDefaults.standard.string(forKey: "searchUnits") == "gpu" ? .gpu : .neuralEngine
            SoakTrace.log("startup: search runs on \(units.rawValue)")
            let e = try EmbeddingGemma2Engine(location: location, units: units)
            t = Date()
            _ = try await Signpost.measure("search model load") { try await e.warmUp() }
            Self.lap("search model loaded", since: t)
            tokenizer = try await tokenizerLoad
            await rerankerLoad
            engine = e
            searchState = .ready                        // a question waits for all three, or its passages would be ordered worse
            Self.lap("search ready", since: started)
        } catch {
            searchState = .failed(error.localizedDescription)
            _ = try? await tokenizerLoad
            await rerankerLoad
        }
    }

    nonisolated static func lap(_ what: String, since t: Date) { SoakTrace.log("startup: \(what) in \(String(format: "%.1f", Date().timeIntervalSince(t))) s") }

    /// Drop MLX's spare buffers when iOS says memory is tight (the answer model's weights stay).
    func trimMemory() { Memory.clearCache() }

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
            reranker = r
            rerankerLine = "Reranker: loaded, checking against the Mac…"
            Task {                                                    // the check is proof for the About screen, not needed to answer
                guard let c = try? await r.check() else { self.rerankerLine = "Reranker: loaded (check failed)"; return }
                self.rerankerLine = "Reranker (the Mac's MiniLM-L-12): token match \(c.encodingMatches)/\(c.encodingTotal) · "
                    + "score agreement with the Mac \(String(format: "%.2f", c.rankCorrelation)) · same top 5 \(Int(c.top5Overlap * 100))% · "
                    + "\(Int(c.millisecondsPerBatch)) ms per 12 passages"
            }
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

    /// Load the answer model as soon as the app opens, so the first question never waits for it. The wrapper that makes
    /// prompt reading stoppable is checked with a short real answer first; if it does not produce one, the plain model is used.
    func prepareAnswerModel() async {
        if gemma != nil { answerModelState = .ready; return }
        guard answerModelState != .loading else { return }
        answerModelState = .loading
        do {
            SoakTrace.log("answer model load begins (stoppable prefill: \(useStoppablePrefill ? "on" : "off"))")
            Memory.cacheLimit = 64 * 1024 * 1024
            let plain = try await #huggingFaceLoadModelContainer(configuration: LLMRegistry.gemma4_e4b_it_4bit)
            if useStoppablePrefill {
                let wrapped = await StoppableLM.wrap(plain)
                if await Self.producesAnAnswer(wrapped) { gemma = wrapped; SoakTrace.log("answer model ready (stoppable prompt reading verified)") }
                else { gemma = plain; SoakTrace.log("answer model ready — stoppable wrapper REJECTED; using the plain model") }
            } else {
                gemma = plain
                SoakTrace.log("answer model ready (plain)")
            }
            answerModelState = .ready
        } catch {
            answerModelState = .failed(error.localizedDescription)
            SoakTrace.log("answer model failed: \(error.localizedDescription)")
        }
    }

    /// Switch the experimental setting: drop the loaded model and load it again the new way.
    func reloadAnswerModel() async {
        stopAnswer()
        gemma = nil
        answerModelState = .idle
        Memory.clearCache()
        await prepareAnswerModel()
    }

    /// A tiny real generation: proves the model works and warms the GPU kernels so the first question is quick.
    private static func producesAnAnswer(_ container: ModelContainer) async -> Bool {
        let result = AnswerCheck()
        if GPUGate.isClosed { return true }                 // the screen is locking: don't start GPU work just to check
        Task.detached {
            if GPUGate.isClosed { result.finish(true); return }
            let session = ChatSession(container, generateParameters: GenerateParameters(maxTokens: 6, temperature: 0))
            let reply = (try? await session.respond(to: "Reply with the single word OK.")) ?? ""
            result.finish(!reply.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        for _ in 0..<90 {                                   // up to 90 s; never wait on a model that may be stuck
            if let done = result.value { return done }
            try? await Task.sleep(for: .seconds(1))
        }
        return false
    }

    /// The phone is getting hot: stop generating rather than push it. Called when the thermal state changes.
    func thermalCheck() {
        let state = ProcessInfo.processInfo.thermalState
        guard state == .serious || state == .critical else { return }
        SoakTrace.log("THERMAL \(state == .critical ? "critical" : "serious") — stopping the answer")
        if answering { stopAnswer(); answerStatus = "Stopped — the phone is hot. Let it cool, then ask again." }
    }

    func stopAnswer() { generation?.cancel() }

    /// Delete everything synced from the Mac (chunks, notebooks, recipe, profile data) and reset the screen state.
    func wipeLibrary() async {
        stopAnswer()
        try? await store?.wipe()
        try? await store?.removeNotebooks(except: [])
        personalCache.removeAll()
        recipe = nil
        try? await store?.setMeta("recipe", "")
        try? await store?.setMeta("last_sync", "")
        await refresh()
        answerText = ""; answerSources = []; answerStatus = ""; followUps = []; hits = []; shownQuestion = ""
        history = []; liveNotebookId = nil
    }

    /// Moves the question and answer on screen into the conversation and clears the live area.
    func archiveLiveTurn() {
        if !shownQuestion.isEmpty, let nb = liveNotebookId {
            history.append(AskTurn(notebookId: nb, notebookTitle: notebooks.first { $0.id == nb }?.title ?? "Notebook",
                                   question: shownQuestion, answer: answerText, sources: answerSources, status: answerStatus))
            if history.count > 500 { history.removeFirst(history.count - 500) }
        }
        answerText = ""; answerSources = []; answerStatus = ""; followUps = []; hits = []; shownQuestion = ""; liveNotebookId = nil
    }

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
            SoakTrace.log("answer model load begins")
            Memory.cacheLimit = 64 * 1024 * 1024
            gemma = try await #huggingFaceLoadModelContainer(configuration: LLMRegistry.gemma4_e4b_it_4bit)
            SoakTrace.log("answer model load ends")
        }
        guard let gemma else { throw LibraryError.badResponse("answer model not loaded") }
        SoakTrace.log("decode begins (\(result.hits.count) passages)")
        defer { SoakTrace.log("decode ends") }
        let passages = await withMeetingContext(result.hits)
        let setup = generationSetup(question: question, hits: passages, personal: personal, withEntityContext: withEntityContext)
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
            let length = AnswerLength.current
            return (r.systemPrompt(for: question, personal: personal, lengthWords: length.words),
                    r.userPrompt(question: question, passages: hits.map(\.text), suffix: withEntityContext ? r.contextSuffix(question: question, personal: personal) : ""),
                    r.parameters(maxTokens: length.maxTokens),
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

    // MARK: meeting context

    /// A meeting transcript is a conversation: a matched line means little without what came before and after it,
    /// and who said it. For passages from a meeting, the answer model is given the piece before and after as well.
    /// (The citation still shows only the matched passage.)
    func withMeetingContext(_ hits: [LibraryHit]) async -> [LibraryHit] {
        guard let store else { return hits }
        var meetings: [String: Bool] = [:]
        var out: [LibraryHit] = []
        for h in hits {
            let key = h.notebookId + "/" + h.sourceId
            if meetings[key] == nil {
                let head = await store.chunkText(notebook: h.notebookId, sourceId: h.sourceId, index: 0) ?? ""
                meetings[key] = Self.looksLikeMeeting(head)
            }
            guard meetings[key] == true else { out.append(h); continue }
            let before = await store.chunkText(notebook: h.notebookId, sourceId: h.sourceId, index: h.chunkIndex - 1)
            let after = await store.chunkText(notebook: h.notebookId, sourceId: h.sourceId, index: h.chunkIndex + 1)
            let text = [before, h.text, after].compactMap { $0 }.joined(separator: "\n\n")
            out.append(LibraryHit(notebookId: h.notebookId, notebookTitle: h.notebookTitle, sourceId: h.sourceId, chunkIndex: h.chunkIndex,
                                  filename: h.filename, text: text, score: h.score, rrf: h.rrf, sourceType: h.sourceType))
        }
        return out
    }

    /// Meeting notes open with a header, or are paragraphs that begin with a speaker's name and a colon.
    nonisolated static func looksLikeMeeting(_ text: String) -> Bool {
        if text.hasPrefix(MeetingSession.header) { return true }
        let turns = text.components(separatedBy: "\n\n").filter { p in
            guard let colon = p.firstIndex(of: ":") else { return false }
            let name = p[p.startIndex..<colon]
            return name.count >= 1 && name.count <= 40 && !name.contains(".") && !name.contains("\n") && name.first?.isUppercase == true
        }
        return turns.count >= 3
    }

    // MARK: staging models around heavy jobs

    private var modelsPaused = false

    /// Frees the answer, search and re-rank models (about 4 GB) for a long job that doesn't use them, such as recording
    /// and writing up a meeting. A phone that holds less is far less likely to be shut down by iOS, especially while locked.
    func pauseModels() {
        guard !modelsPaused else { return }
        modelsPaused = true
        generation?.cancel()
        gemma = nil; answerModelState = .idle
        engine = nil; tokenizer = nil; reranker = nil; vision = nil
        searchState = .notReady
        rerankerLine = "Reranker: not loaded"
        Memory.clearCache()
        SoakTrace.log("models paused for a long job")
    }

    /// Loads them again (search and answers in parallel, as at launch).
    func resumeModels() async {
        guard modelsPaused else { return }
        modelsPaused = false
        let t0 = Date()
        async let search: Void = { await self.prepareSearch(); await self.loadVisionIfInstalled() }()
        async let answers: Void = self.prepareAnswerModel()
        _ = await (search, answers)
        SoakTrace.log("models resumed after \(String(format: "%.1f", Date().timeIntervalSince(t0))) s")
    }

    /// Waits (up to two minutes) until search is ready, for work that needs it straight after a pause.
    func waitForSearch() async {
        for _ in 0..<240 {
            if case .ready = searchState { return }
            try? await Task.sleep(for: .milliseconds(500))
        }
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
        archiveLiveTurn()                                 // the last question and answer stay above, in the conversation
        let question = query.trimmingCharacters(in: .whitespaces)
        shownQuestion = question
        liveNotebookId = scope
        query = ""                                        // the field is empty again once the question is on its way
        defer { answering = false }
        do {
            answerStatus = "Searching notebook…"
            let t0 = Date()
            var result = try await Signpost.measure("retrieve passages") { try await retrieve(question: question, notebook: scope) }
            hits = result.hits
            sourceScoreIsRelevance = false
            answerSources = result.hits
            answerStatus = "Reading \(result.hits.count) passage\(result.hits.count == 1 ? "" : "s")…"
            let findSeconds = Date().timeIntervalSince(t0)
            var withContext = true
            var corrected = false
            if result.nothingRelevant || result.hits.isEmpty {          // as the Mac: no relevant passages, no guessing…
                answerStatus = "Nothing relevant yet — looking again with rewritten queries…"
                if let second = try await correctiveRetrieve(question: question, notebook: scope, first: result.pool),     // …but it looks again first
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
                answerStatus = "Getting the answer model ready…"
                Memory.cacheLimit = 64 * 1024 * 1024
                gemma = try await #huggingFaceLoadModelContainer(configuration: LLMRegistry.gemma4_e4b_it_4bit)
            }
            guard let gemma else { return }
            guard GPUGuard.canStart else { answerStatus = "Open the app and ask again — answers can't run while the screen is off."; return }
            let personal = await personal(for: scope)
            var current = result
            var line = ""
            while true {
                let setup = generationSetup(question: question, hits: current.hits, personal: personal, withEntityContext: withContext)
                let prompt = setup.prompt
                let session = ChatSession(gemma, instructions: setup.instructions, generateParameters: setup.params)
                answerStatus = corrected ? "Looking again, then writing…" : "Writing…"
                answerText = ""
                let started = Date()
                var firstWord: Double?
                var info: GenerateCompletionInfo?
                let task = Task { @MainActor in
                    for try await item in session.streamDetails(to: prompt) {
                        switch item {
                        case .chunk(let s):
                            if firstWord == nil { firstWord = Date().timeIntervalSince(started); SoakTrace.log("first token after \(String(format: "%.1f", firstWord ?? 0)) s") }
                            answerText += s
                        case .info(let i): info = i
                        default: break
                        }
                    }
                }
                generation = Task { _ = try? await task.value }
                let observer = GPUGuard.protect(task, label: "ask")
                UIApplication.shared.isIdleTimerDisabled = true          // don't let the screen auto-lock mid-answer
                defer { UIApplication.shared.isIdleTimerDisabled = false }
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
                guard !corrected, let r = recipe, !r.answerLooksGood(question: question, answer: answerText, type: r.classify(question)) else { break }
                corrected = true
                answerStatus = "Looking again with rewritten queries…"
                guard let second = try await correctiveRetrieve(question: question, notebook: scope, first: current.pool) else { break }
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
        guard store != nil, engine != nil, tokenizer != nil, !query.trimmingCharacters(in: .whitespaces).isEmpty else { return }
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

private final class AnswerCheck: @unchecked Sendable {
    private let lock = NSLock()
    private var outcome: Bool?
    func finish(_ ok: Bool) { lock.lock(); outcome = ok; lock.unlock() }
    var value: Bool? { lock.lock(); defer { lock.unlock() }; return outcome }
}

/// How long the phone's answers may be. The Mac's own answers run about 200-300 words; the phone's model is wordier, so
/// the length is asked for in the prompt and enough tokens are allowed for it to finish its last sentence.
enum AnswerLength: String, CaseIterable, Identifiable {
    case short, standard, detailed
    var id: String { rawValue }
    var label: String { switch self { case .short: "Short"; case .standard: "Standard"; case .detailed: "Detailed" } }
    var words: Int { switch self { case .short: 200; case .standard: 350; case .detailed: 500 } }
    var maxTokens: Int { Int(Double(words) * 1.9) + 60 }
    static var current: AnswerLength { AnswerLength(rawValue: UserDefaults.standard.string(forKey: "answerLength") ?? "") ?? .standard }
}


/// One finished question and answer in the running conversation on the Ask screen.
struct AskTurn: Identifiable {
    let id = UUID()
    let notebookId: String
    let notebookTitle: String
    let question: String
    let answer: String
    let sources: [LibraryHit]
    let status: String
}
