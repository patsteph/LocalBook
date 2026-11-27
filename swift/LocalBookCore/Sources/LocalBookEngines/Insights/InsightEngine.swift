import Foundation
import LocalBookCore
import MLXLMCommon
import Observation

/// Makes insights from one notebook with the answer model: the timeline, the knowledge graph, the contradictions, a
/// comparison of two sources, the perspectives on a topic, the topics. Reads the notebook in windows of passages
/// (the whole notebook for the first three, so nothing is missed; search for the perspectives), asks for JSON, and
/// merges. Foreground only, like Studio: the GPU is not available to a locked device.
@MainActor @Observable
public final class InsightEngine {
    public private(set) var working = false
    public private(set) var status = ""
    public private(set) var progress: Double = 0
    public private(set) var error: String?
    private var task: Task<Void, Error>?
    private var stopped = false

    public init() {}

    public func stop() { stopped = true; task?.cancel() }

    /// How many passages one window holds, and how many windows a whole-notebook read may take (a long notebook is
    /// sampled evenly past that, so a timeline of a 900-passage notebook takes minutes, not an hour).
    static let window = 8
    static let maxWindows = 24

    // MARK: entry points

    public func makeTimeline(in notebook: LibraryNotebookInfo, library: LibraryModel) async -> Insight? {
        await run(in: notebook, library: library, what: "timeline") { gemma, store in
            let windows = Self.windows(try await store.chunkTexts(notebook: notebook.id))
            var parts: [(events: [TimelineInsight.Event], timeSpan: String?, context: String?)] = []
            for (i, w) in windows.enumerated() {
                self.status = "Reading for dates… part \(i + 1) of \(windows.count)"
                guard let out = await self.generate(gemma, instructions: InsightPrompts.timelineInstructions, prompt: InsightPrompts.timelinePrompt(passages: w.map(\.text)), tokens: 900) else { return nil }
                parts.append(InsightPrompts.timelineEvents(from: out, sources: w.map { ($0.sourceId, $0.filename) }))
                self.progress = Double(i + 1) / Double(windows.count)
            }
            let timeline = InsightPrompts.mergeTimeline(parts)
            guard !timeline.events.isEmpty else { self.error = "No dates were found in this notebook."; return nil }
            return Insight(notebookId: notebook.id, kind: .timeline, title: "Timeline · \(notebook.title)", body: Insight.encode(timeline))
        }
    }

    public func makeGraph(in notebook: LibraryNotebookInfo, library: LibraryModel) async -> Insight? {
        await run(in: notebook, library: library, what: "graph") { gemma, store in
            let windows = Self.windows(try await store.chunkTexts(notebook: notebook.id))
            var found: [(mentions: [InsightPrompts.Mention], passageSources: [String])] = []
            var passagesByKeyPair: [String: String] = [:]
            for (i, w) in windows.enumerated() {
                self.status = "Finding people, places and things… part \(i + 1) of \(windows.count)"
                guard let out = await self.generate(gemma, instructions: InsightPrompts.entityInstructions, prompt: InsightPrompts.entityPrompt(passages: w.map(\.text)), tokens: 1_000) else { return nil }
                let mentions = InsightPrompts.entities(from: out)
                found.append((mentions, w.map(\.sourceId)))
                // Remember one passage for each pair that shares one, for the relation prompt.
                var byPassage: [Int: [String]] = [:]
                for m in mentions { for p in m.passages where p >= 1 && p <= w.count { byPassage[p, default: []].append(m.name) } }
                for (p, names) in byPassage {
                    let list = Array(Set(names)).sorted()
                    for a in list.indices { for b in list.indices where b > a {
                        let k = [InsightPrompts.key(list[a]), InsightPrompts.key(list[b])].sorted().joined(separator: "|")
                        if passagesByKeyPair[k] == nil { passagesByKeyPair[k] = "\(list[a]) — \(list[b])\n" + w[p - 1].text }
                    } }
                }
                self.progress = 0.8 * Double(i + 1) / Double(windows.count)
            }
            var graph = InsightPrompts.buildGraph(windows: found)
            guard !graph.nodes.isEmpty else { self.error = "No named people, places or things were found."; return nil }
            // Name the strongest relations (up to 30 pairs, in batches of ten).
            let top = graph.edges.prefix(30)
            var relations: [(a: String, b: String, relation: String)] = []
            let names = Dictionary(uniqueKeysWithValues: graph.nodes.map { ($0.id, $0.name) })
            for (i, batch) in stride(from: 0, to: top.count, by: 10).map({ Array(top[$0..<min($0 + 10, top.count)]) }).enumerated() {
                self.status = "Naming the connections… \(i + 1) of \((top.count + 9) / 10)"
                let pairs = batch.compactMap { e -> (a: String, b: String, passage: String)? in
                    guard let a = names[e.a], let b = names[e.b] else { return nil }
                    let text = passagesByKeyPair[e.a + "|" + e.b].map { String($0.split(separator: "\n", maxSplits: 1).last ?? "") } ?? ""
                    return (a, b, text)
                }
                guard !pairs.isEmpty else { continue }
                guard let out = await self.generate(gemma, instructions: InsightPrompts.relationInstructions, prompt: InsightPrompts.relationPrompt(pairs: pairs), tokens: 500) else { return nil }
                relations += InsightPrompts.relations(from: out)
            }
            graph = InsightPrompts.buildGraph(windows: found, relations: relations)
            return Insight(notebookId: notebook.id, kind: .graph, title: "Knowledge graph · \(notebook.title)", body: Insight.encode(graph))
        }
    }

    public func makeContradictions(in notebook: LibraryNotebookInfo, library: LibraryModel) async -> Insight? {
        await run(in: notebook, library: library, what: "contradictions") { gemma, store in
            let windows = Self.windows(try await store.chunkTexts(notebook: notebook.id))
            var claims: [InsightPrompts.Claim] = []
            for (i, w) in windows.enumerated() {
                self.status = "Listing the claims… part \(i + 1) of \(windows.count)"
                guard let out = await self.generate(gemma, instructions: InsightPrompts.claimInstructions, prompt: InsightPrompts.claimPrompt(passages: w.map(\.text)), tokens: 900) else { return nil }
                claims += InsightPrompts.claims(from: out, sources: w.map { ($0.sourceId, $0.filename) })
                self.progress = 0.5 * Double(i + 1) / Double(windows.count)
            }
            claims = Array(claims.prefix(160))
            guard claims.count >= 2 else { self.error = "Too few checkable claims to compare."; return nil }
            // Candidate pairs: claims from different sources that say similar things, by the search model's vectors.
            self.status = "Finding claims that talk about the same thing…"
            var vectors: [[Float]] = []
            for c in claims { vectors.append((try? await library.documentVector(c.text)) ?? []) }
            var pairs: [(Int, Int, Float)] = []
            for i in claims.indices { for j in claims.indices where j > i && claims[i].sourceId != claims[j].sourceId {
                guard !vectors[i].isEmpty, !vectors[j].isEmpty else { continue }
                var s: Float = 0; for d in 0..<min(vectors[i].count, vectors[j].count) { s += vectors[i][d] * vectors[j][d] }
                if s > 0.72 { pairs.append((i, j, s)) }
            } }
            pairs.sort { $0.2 > $1.2 }
            let checks = Array(pairs.prefix(24))
            guard !checks.isEmpty else {
                return Insight(notebookId: notebook.id, kind: .contradictions, title: "Contradictions · \(notebook.title)",
                               body: Insight.encode(ContradictionsInsight(items: [], claimsChecked: claims.count, pairsChecked: 0)))
            }
            var items: [ContradictionsInsight.Item] = []
            for (n, (i, j, _)) in checks.enumerated() {
                self.status = "Checking pair \(n + 1) of \(checks.count)…"
                guard let out = await self.generate(gemma, instructions: InsightPrompts.contradictionInstructions, prompt: InsightPrompts.contradictionPrompt(claims[i], claims[j]), tokens: 220) else { return nil }
                if let item = InsightPrompts.contradiction(from: out, claims[i], claims[j]) { items.append(item) }
                self.progress = 0.5 + 0.5 * Double(n + 1) / Double(checks.count)
            }
            let order = ["high": 0, "medium": 1, "low": 2]
            items.sort { (order[$0.severity] ?? 1) < (order[$1.severity] ?? 1) }
            return Insight(notebookId: notebook.id, kind: .contradictions, title: "Contradictions · \(notebook.title)",
                           body: Insight.encode(ContradictionsInsight(items: items, claimsChecked: claims.count, pairsChecked: checks.count)))
        }
    }

    public func makeComparison(in notebook: LibraryNotebookInfo, sourceA: LibraryStore.SourceInfo, sourceB: LibraryStore.SourceInfo, focus: String?, library: LibraryModel) async -> Insight? {
        await run(in: notebook, library: library, what: "comparison") { gemma, store in
            self.status = "Reading both sources…"
            let a = await store.sourceText(notebook: notebook.id, sourceId: sourceA.id)
            let b = await store.sourceText(notebook: notebook.id, sourceId: sourceB.id)
            guard !a.isEmpty, !b.isEmpty else { self.error = "One of the sources has no text to compare."; return nil }
            self.status = "Comparing \(sourceA.name) with \(sourceB.name)…"
            let focus = focus?.trimmingCharacters(in: .whitespacesAndNewlines).nilIfEmpty
            guard let out = await self.generate(gemma, instructions: InsightPrompts.comparisonInstructions,
                                                prompt: InsightPrompts.comparisonPrompt(a: a, b: b, titleA: sourceA.name, titleB: sourceB.name, focus: focus), tokens: 1_200) else { return nil }
            guard let c = InsightPrompts.comparison(from: out, sourceA: sourceA.id, titleA: sourceA.name, sourceB: sourceB.id, titleB: sourceB.name, focus: focus) else {
                self.error = "The comparison didn't come out right. Try again."; return nil
            }
            return Insight(notebookId: notebook.id, kind: .comparison, title: "\(sourceA.name) vs \(sourceB.name)", body: Insight.encode(c))
        }
    }

    public func makePerspectives(in notebook: LibraryNotebookInfo, topic: String, library: LibraryModel) async -> Insight? {
        await run(in: notebook, library: library, what: "perspectives") { gemma, _ in
            let topic = topic.trimmingCharacters(in: .whitespacesAndNewlines)
            self.status = "Finding what each source says about “\(topic)”…"
            var bySource: [String: (filename: String, texts: [String])] = [:]
            for seed in [topic, "\(topic) evidence and figures", "\(topic) opinions and conclusions"] {
                guard let found = try? await library.retrieve(question: seed, notebook: notebook.id) else { continue }
                for h in found.hits {
                    var e = bySource[h.sourceId] ?? (h.filename, [])
                    if !e.texts.contains(h.text), e.texts.count < 4 { e.texts.append(h.text) }
                    bySource[h.sourceId] = e
                }
            }
            let sources = bySource.sorted { $0.value.texts.count > $1.value.texts.count || ($0.value.texts.count == $1.value.texts.count && $0.key < $1.key) }.prefix(8)
            guard !sources.isEmpty else { self.error = "Nothing in this notebook speaks to that topic."; return nil }
            var perspectives: [PerspectivesInsight.Perspective] = []
            for (i, (id, e)) in sources.enumerated() {
                self.status = "\(e.filename)… \(i + 1) of \(sources.count)"
                guard let out = await self.generate(gemma, instructions: InsightPrompts.perspectiveInstructions, prompt: InsightPrompts.perspectivePrompt(topic: topic, filename: e.filename, excerpts: e.texts), tokens: 400) else { return nil }
                if let p = InsightPrompts.perspective(from: out, sourceId: id, filename: e.filename) { perspectives.append(p) }
                self.progress = 0.8 * Double(i + 1) / Double(sources.count)
            }
            guard !perspectives.isEmpty else { self.error = "No source had a position on that topic."; return nil }
            self.status = "Weighing the sources together…"
            var consensus: [String] = [], contested: [String] = [], synthesis = ""
            if perspectives.count >= 2, let out = await self.generate(gemma, instructions: InsightPrompts.consensusInstructions, prompt: InsightPrompts.consensusPrompt(topic: topic, perspectives: perspectives), tokens: 600) {
                (consensus, contested, synthesis) = InsightPrompts.consensus(from: out)
            }
            return Insight(notebookId: notebook.id, kind: .perspectives, title: "Perspectives · \(topic)",
                           body: Insight.encode(PerspectivesInsight(topic: topic, perspectives: perspectives, consensus: consensus, contested: contested, synthesis: synthesis)))
        }
    }

    public func makeTopics(in notebook: LibraryNotebookInfo, library: LibraryModel) async -> Insight? {
        await run(in: notebook, library: library, what: "topics") { gemma, store in
            self.status = "Grouping the notebook's passages…"
            let rows = await store.chunkVectors(notebook: notebook.id)
            guard rows.count >= 4 else { self.error = "Too little in this notebook to find topics."; return nil }
            let items = rows.map { TextClusters.Item(id: $0.id, sourceId: $0.sourceId, text: $0.text, vector: $0.vector) }
            let clusters = await Task.detached(priority: .userInitiated) { TextClusters.kMeans(items) }.value
            let names = Dictionary(uniqueKeysWithValues: rows.map { ($0.sourceId, $0.filename) })
            var topics = clusters.map { c in
                let srcs = Array(Set(c.members.map { items[$0].sourceId })).sorted().compactMap { names[$0] }
                return TopicsInsight.Topic(name: c.keywords.prefix(3).map(\.capitalized).joined(separator: " · "), keywords: c.keywords, chunkCount: c.members.count, sources: srcs, sample: items[c.members[0]].text)
            }
            self.status = "Naming the topics…"
            self.progress = 0.6
            if let out = await self.generate(gemma, instructions: InsightPrompts.topicNameInstructions, prompt: InsightPrompts.topicNamePrompt(groups: topics.map { ($0.keywords, $0.sample) }), tokens: 300) {
                for (i, name) in InsightPrompts.topicNames(from: out, count: topics.count) { topics[i].name = name }
            }
            return Insight(notebookId: notebook.id, kind: .topics, title: "Topics · \(notebook.title)", body: Insight.encode(TopicsInsight(topics: topics, chunksClustered: rows.count)))
        }
    }

    /// The writing assistant: the user's text transformed, with the notebook's passages as reference when asked.
    public func write(_ text: String, task: WritingAssistant.Task, format: WritingAssistant.Format, maxWords: Int?, notebook: LibraryNotebookInfo?, library: LibraryModel) async -> String? {
        guard !working else { return nil }
        working = true; stopped = false; error = nil; progress = 0; status = "Writing…"
        defer { working = false; status = "" }
        guard let gemma = library.gemma else { error = "The answer model is still getting ready. Try again in a moment."; return nil }
        guard GPUGuard.canStart else { error = "Open the app and try again: this can't run while the screen is off."; return nil }
        var reference: [String] = []
        if let notebook, let found = try? await library.retrieve(question: String(text.prefix(300)), notebook: notebook.id) { reference = found.hits.prefix(6).map(\.text) }
        guard let out = await generate(gemma, instructions: WritingAssistant.instructions, prompt: WritingAssistant.prompt(text: text, task: task, format: format, maxWords: maxWords, reference: reference), tokens: max(400, (maxWords ?? 500) * 2)) else { return nil }
        let cleaned = WritingAssistant.clean(out)
        return cleaned.isEmpty ? nil : cleaned
    }

    // MARK: plumbing

    private func run(in notebook: LibraryNotebookInfo, library: LibraryModel, what: String, _ body: (ModelContainer, LibraryStore) async throws -> Insight?) async -> Insight? {
        guard !working else { return nil }
        working = true; stopped = false; error = nil; progress = 0
        defer { working = false; status = "" }
        guard let gemma = library.gemma else { error = "The answer model is still getting ready. Try again in a moment."; return nil }
        guard let store = library.store else { error = "The library is not open."; return nil }
        guard GPUGuard.canStart else { error = "Open the app and try again: this can't run while the screen is off."; return nil }
        let awake = KeepAwake.begin(reason: "Making a \(what)")
        defer { awake.end() }
        do {
            let made = try await body(gemma, store)
            if made == nil, error == nil, stopped { error = "Stopped." }
            if let made { InsightStore.save(made); EventLog.record("insight_made", notebook: made.notebookId, title: made.title, detail: made.kind.rawValue) }
            return made
        } catch { self.error = error.localizedDescription; return nil }
    }

    /// Passages in windows of `window`, sampled evenly when the notebook is longer than `maxWindows` windows allow.
    static func windows(_ rows: [(sourceId: String, filename: String, text: String)]) -> [[(sourceId: String, filename: String, text: String)]] {
        guard !rows.isEmpty else { return [] }
        var picked = rows
        let cap = window * maxWindows
        if rows.count > cap {
            let step = Double(rows.count) / Double(cap)
            picked = (0..<cap).map { rows[min(rows.count - 1, Int(Double($0) * step))] }
        }
        return stride(from: 0, to: picked.count, by: window).map { Array(picked[$0..<min($0 + window, picked.count)]) }
    }

    private func generate(_ gemma: ModelContainer, instructions: String, prompt: String, tokens: Int) async -> String? {
        if stopped { return nil }
        let session = ChatSession(gemma, instructions: instructions, generateParameters: GenerateParameters(maxTokens: tokens, temperature: 0.2))
        let box = TextBox()
        let work = Task { @MainActor in
            for try await item in session.streamDetails(to: prompt) {
                if case .chunk(let s) = item { box.append(s) }
            }
        }
        task = work
        let observer = GPUGuard.protect(work, label: "insight")
        do { try await work.value } catch {}
        NotificationCenter.default.removeObserver(observer)
        task = nil
        return work.isCancelled || stopped ? nil : box.text.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

private final class TextBox: @unchecked Sendable {
    private let lock = NSLock()
    private var buffer = ""
    func append(_ s: String) { lock.lock(); buffer += s; lock.unlock() }
    var text: String { lock.lock(); defer { lock.unlock() }; return buffer }
}

private extension String {
    var nilIfEmpty: String? { isEmpty ? nil : self }
}
