import Foundation
import LocalBookCore
import MLXLMCommon
import Observation

/// Runs the Canvas for one notebook: builds the map from the journey, the sources, Studio and Insights; embeds each
/// thread with the search model so threads gather into sub-topic cards; names new cards with the answer model; finds
/// candidate connections, gaps and tensions; answers a question scoped to a selection and puts the answer on the map.
/// The arithmetic lives in Core (`CanvasPopulate`, `CanvasCandidates`, `CanvasTension`, `CanvasRecall`); this class
/// does the reads, the models and the saves.
@MainActor @Observable
public final class CanvasEngine {
    public private(set) var layout = CanvasLayout()
    public private(set) var candidates: [CanvasCandidates.Candidate] = []
    public private(set) var gaps: [CanvasGaps.Gap] = []
    public private(set) var working = false
    public private(set) var status = ""
    public private(set) var error: String?
    public private(set) var notebookId: String?
    private var vectors: [String: [Float]] = [:]
    private var task: Task<Void, Error>?

    public init() {}

    // MARK: open and save

    public func open(notebook: String) {
        notebookId = notebook
        layout = CanvasStore.load(notebook: notebook)
        vectors = Self.loadVectors(notebook: notebook)
        candidates = []
        gaps = CanvasGaps.find(JourneyStore.turns(notebook: notebook))
    }

    public func save() {
        guard let notebookId else { return }
        CanvasStore.save(layout, notebook: notebookId)
        Self.saveVectors(vectors, notebook: notebookId)
    }

    public func move(_ id: String, x: Double, y: Double) { layout.move(id, x: x, y: y) }
    public func resize(_ id: String, width: Double, height: Double) {
        guard let i = layout.nodes.firstIndex(where: { $0.id == id }) else { return }
        layout.nodes[i].width = width; layout.nodes[i].height = height
    }
    public func setViewport(_ v: CanvasViewport) { layout.viewport = v }

    public func connect(_ a: String, _ b: String, state: String = "user", label: String = "") {
        guard a != b else { return }
        layout.upsert(CanvasEdge(source: a, target: b, state: state, label: label, createdAt: CanvasStore.stamp()))
        candidates.removeAll { Set([$0.a, $0.b]) == Set([a, b]) }
        save()
    }
    public func removeEdge(_ id: String) { layout.edges.removeAll { $0.id == id }; save() }
    public func removeNode(_ id: String) { layout.remove(node: id); vectors[id] = nil; save() }

    public func addNote(_ text: String, near: (x: Double, y: Double)) {
        let title = text.split(separator: "\n").first.map(String.init) ?? "Note"
        let node = CanvasNode(id: "note:" + UUID().uuidString, kind: CanvasNode.noteKind, refType: "note", title: String(title.prefix(60)),
                              snapshot: .init(payload: text), x: near.x, y: near.y, createdAt: CanvasStore.stamp(), updatedAt: CanvasStore.stamp())
        layout.nodes.append(node)
        save()
    }

    public func grade(_ nodeId: String, _ grade: CanvasRecall.Grade) {
        layout.recall[nodeId] = CanvasRecall.graded(layout.recall[nodeId], grade)
        save()
    }
    public var due: [CanvasNode] { CanvasRecall.due(layout) }

    /// The user's answer to "what were you exploring here?" on an orphan: kept on the node, and the node tries the
    /// cards again with the intent folded into its meaning.
    public func elicit(_ nodeId: String, intent: String, library: LibraryModel) async {
        guard let i = layout.nodes.firstIndex(where: { $0.id == nodeId }) else { return }
        layout.nodes[i].intent = intent
        if let v = try? await library.documentVector(intent + "\n" + layout.nodes[i].snapshot.payload) { vectors[nodeId] = v }
        assignCards(library: library, naming: nil)
        save()
    }

    public func tidy() { layout = CanvasPopulate.arrange(layout); save() }

    // MARK: populate

    /// Rebuilds the map from the notebook's capture; keeps what the user arranged.
    public func populate(notebook: LibraryNotebookInfo, library: LibraryModel) async {
        guard !working else { return }
        working = true; error = nil; status = "Reading the journey…"
        defer { working = false; status = "" }
        if notebookId != notebook.id { open(notebook: notebook.id) }
        guard let store = library.store else { error = "The library is not open."; return }
        let turns = JourneyStore.turns(notebook: notebook.id)
        let sources = await store.sources(notebook: notebook.id).map { CanvasPopulate.SourceRef(id: $0.id, title: $0.name, created: $0.created) }
        var artifacts = StudioStore.all(notebook: notebook.id).map {
            CanvasPopulate.ArtifactRef(id: $0.id, kind: $0.kind.rawValue, title: $0.title, preview: Self.preview(of: $0), created: $0.created, studioKind: $0.kind.rawValue)
        }
        artifacts += InsightStore.all(notebook: notebook.id).map {
            CanvasPopulate.ArtifactRef(id: $0.id, kind: "insight", title: $0.title, preview: Self.preview(of: $0), created: $0.created, insightKind: $0.kind.rawValue)
        }
        let firstTime = layout.nodes.isEmpty
        let built = CanvasPopulate.buildNodes(turns: turns, sources: sources, artifacts: artifacts)
        layout = CanvasPopulate.merge(layout, with: built)
        gaps = CanvasGaps.find(turns)

        // Embed the threads that have no vector yet.
        let need = layout.threads.filter { vectors[$0.id] == nil && $0.kind != CanvasNode.sourceKind }
        for (i, n) in need.enumerated() {
            status = "Placing \(i + 1) of \(need.count)…"
            if let v = try? await library.documentVector((n.intent.map { $0 + "\n" } ?? "") + n.snapshot.payload) { vectors[n.id] = v }
        }
        status = "Gathering threads into cards…"
        assignCards(library: library, naming: library.gemma)
        await nameNewCards(library: library)

        // Derived edges: tensions from the latest contradictions insight, provenance from what each thing used.
        layout.edges.removeAll { $0.state == "tension" || $0.state == "provenance" }
        if let c = InsightStore.all(notebook: notebook.id).first(where: { $0.kind == .contradictions })?.decode(ContradictionsInsight.self) {
            for e in CanvasTension.edges(from: c, nodes: layout.nodes) { layout.upsert(e) }
        }
        for e in CanvasProvenance.edges(for: layout) { layout.upsert(e) }
        if firstTime { layout = CanvasPopulate.arrange(layout) }
        else { placeNewMembers() }
        refreshCandidates(notebook: notebook.id)
        save()
    }

    /// Assigns threads to cards (accretive) and mints cards for new groups, titled by keywords until the model names them.
    private func assignCards(library: LibraryModel, naming: ModelContainer?) {
        let threads = layout.nodes.filter { $0.kind == CanvasNode.chatKind || $0.kind == CanvasNode.artifactKind }
        let input = threads.map { (id: $0.id, vector: vectors[$0.id] ?? [], topicId: $0.topicId) }
        let result = CanvasPopulate.assignTopics(nodes: input, topics: layout.topics)
        for (id, topic) in result.assigned { if let i = layout.nodes.firstIndex(where: { $0.id == id }) { layout.nodes[i].topicId = topic; layout.nodes[i].parentId = topic } }
        for group in result.newTopics {
            let texts = group.members.compactMap { id in layout.node(id)?.snapshot.payload }
            let words = TextClusters.keywords(for: texts, count: 3)
            let title = words.isEmpty ? "New topic" : words.map(\.capitalized).joined(separator: " · ")
            let topic = CanvasTopic(title: title, centroid: group.centroid, members: group.members.count, createdAt: CanvasStore.stamp())
            layout.topics.append(topic)
            layout.nodes.append(CanvasNode(id: topic.id, kind: CanvasNode.topicKind, refType: "topic", refId: topic.id, title: title,
                                           snapshot: .init(payload: title), width: 720, height: 300, createdAt: CanvasStore.stamp(), updatedAt: CanvasStore.stamp()))
            for id in group.members { if let i = layout.nodes.firstIndex(where: { $0.id == id }) { layout.nodes[i].topicId = topic.id; layout.nodes[i].parentId = topic.id } }
            pendingNames.insert(topic.id)
        }
        // Member counts and centroids follow the threads.
        for i in layout.topics.indices {
            let members = layout.members(of: layout.topics[i].id)
            layout.topics[i].members = members.count
            let vs = members.compactMap { vectors[$0.id] }
            if !vs.isEmpty { layout.topics[i].centroid = CanvasPopulate.mean(vs) }
        }
        _ = naming
    }

    private var pendingNames = Set<String>()

    /// Names the cards the keywords titled, three to six words each, with the answer model when it is loaded.
    private func nameNewCards(library: LibraryModel) async {
        guard let gemma = library.gemma, !pendingNames.isEmpty, GPUGuard.canStart else { return }
        let ids = Array(pendingNames); pendingNames = []
        for id in ids {
            guard let ti = layout.topics.firstIndex(where: { $0.id == id }) else { continue }
            let members = layout.members(of: id).prefix(6)
            let text = members.map { "- " + $0.title + ": " + ($0.snapshot.metadata?.preview ?? "") }.joined(separator: "\n")
            status = "Naming “\(layout.topics[ti].title)”…"
            let prompt = "These questions and items were asked in one notebook and belong together. Give the group a short title (3 to 6 words, no quotes, no trailing period) and one sentence saying what ties them together. Reply as two lines: the title, then the sentence.\n\n\(text)"
            guard let out = await generate(gemma, instructions: "You title groups of related questions. Reply with exactly two lines.", prompt: prompt, tokens: 80) else { continue }
            let lines = out.split(separator: "\n").map { $0.trimmingCharacters(in: .whitespaces.union(.init(charactersIn: "\"“”*#-"))) }.filter { !$0.isEmpty }
            guard let title = lines.first, title.count <= 80 else { continue }
            layout.topics[ti].title = title
            layout.topics[ti].summary = lines.dropFirst().first ?? ""
            if let ni = layout.nodes.firstIndex(where: { $0.id == id }) { layout.nodes[ni].title = title; layout.nodes[ni].snapshot = .init(payload: layout.topics[ti].summary.isEmpty ? title : layout.topics[ti].summary) }
        }
    }

    /// New members of an existing card move into it (a free slot below its last row); the card grows to fit.
    private func placeNewMembers() {
        for card in layout.topicCards {
            let members = layout.members(of: card.id)
            let inside = members.filter { $0.x >= card.x && $0.x <= card.x + (card.width ?? 0) && $0.y >= card.y && $0.y <= card.y + (card.height ?? 0) }
            let outside = members.filter { m in !inside.contains { $0.id == m.id } }
            guard !outside.isEmpty, let ci = layout.nodes.firstIndex(where: { $0.id == card.id }) else { continue }
            let cols = 3
            var slot = inside.count
            for m in outside {
                let c = slot % cols, r = slot / cols
                layout.move(m.id, x: card.x + CanvasPopulate.cardPadding + Double(c) * (CanvasPopulate.chipWidth + CanvasPopulate.chipGap),
                            y: card.y + 36 + CanvasPopulate.cardPadding + Double(r) * (CanvasPopulate.chipHeight + CanvasPopulate.chipGap))
                slot += 1
            }
            let rows = max(1, (slot + cols - 1) / cols)
            layout.nodes[ci].width = max(layout.nodes[ci].width ?? 0, Double(min(cols, slot)) * CanvasPopulate.chipWidth + Double(min(cols, slot) - 1) * CanvasPopulate.chipGap + 2 * CanvasPopulate.cardPadding)
            layout.nodes[ci].height = max(layout.nodes[ci].height ?? 0, Double(rows) * CanvasPopulate.chipHeight + Double(rows - 1) * CanvasPopulate.chipGap + 2 * CanvasPopulate.cardPadding + 36)
        }
    }

    // MARK: candidates

    /// Latent connections between the threads on the map: similar meaning, shared sources, shared named things from
    /// the knowledge graph when one has been made.
    public func refreshCandidates(notebook: String) {
        let graph = InsightStore.all(notebook: notebook).first(where: { $0.kind == .graph })?.decode(GraphInsight.self)
        let names = graph?.nodes.map { ($0.id, $0.name.lowercased()) } ?? []
        let inputs = layout.nodes.filter { !$0.isTopic }.prefix(60).map { n in
            let text = n.snapshot.payload.lowercased()
            let concepts = Set(names.filter { text.contains($0.1) }.map(\.0))
            return CanvasCandidates.Input(id: n.id, vector: vectors[n.id] ?? [], sourceIds: Set(n.sourceIds), concepts: concepts)
        }
        candidates = CanvasCandidates.compute(Array(inputs), existing: layout.edges)
    }

    /// Promotes the confident candidates (≥ 0.7) to kept dashed edges.
    public func autoConnect() {
        for c in candidates where c.score >= 0.7 {
            layout.upsert(CanvasEdge(source: c.a, target: c.b, state: "curator", label: c.label, meta: ["score": String(format: "%.2f", c.score)], createdAt: CanvasStore.stamp()))
        }
        candidates.removeAll { $0.score >= 0.7 }
        save()
    }

    // MARK: chat with a selection

    /// Answers a question from the sources behind the selected nodes only, then puts the answer on the map beside
    /// them, joined by provenance edges, and records it in the journey. Returns the answer text.
    public func chat(_ question: String, selection: [String], notebook: LibraryNotebookInfo, library: LibraryModel) async -> String? {
        guard !working else { return nil }
        working = true; error = nil; status = "Looking in the selected sources…"
        defer { working = false; status = "" }
        guard let gemma = library.gemma else { error = "The answer model is still getting ready. Try again in a moment."; return nil }
        guard GPUGuard.canStart else { error = "Open the app and try again: this can't run while the screen is off."; return nil }
        let picked = layout.nodes.filter { selection.contains($0.id) }
        let scope = Set(picked.flatMap(\.sourceIds))
        var hits: [LibraryHit] = []
        do {
            let found = try await library.retrieve(question: question, notebook: notebook.id)
            hits = scope.isEmpty ? found.hits : found.hits.filter { scope.contains($0.sourceId) }
            if hits.isEmpty, !scope.isEmpty {
                // The selection's sources did not make the cut: take the best passages from them anyway.
                hits = found.pool.map(\.hit).filter { scope.contains($0.sourceId) }.prefix(5).map { $0 }
            }
        } catch { self.error = error.localizedDescription; return nil }
        guard !hits.isEmpty else { error = "Nothing in the selected sources speaks to that."; return nil }
        status = "Answering…"
        let setup = library.generationSetup(question: question, hits: hits, notebookTitle: notebook.title)
        guard let raw = await generate(gemma, instructions: setup.instructions, prompt: setup.prompt, tokens: setup.params.maxTokens ?? 500) else { return nil }
        let answer = setup.clean(raw, false)
        let turn = JourneyTurn(notebookId: notebook.id, question: question, answer: answer, sourcesUsed: Array(Set(hits.map(\.sourceId))).sorted(),
                               filenames: Array(Set(hits.map(\.filename))).sorted(), origin: "canvas")
        JourneyStore.append(turn)
        let anchor = picked.first.map { ($0.x + CanvasPopulate.chipWidth + CanvasPopulate.chipGap, $0.y) } ?? (0, (layout.nodes.map(\.y).max() ?? 0) + CanvasPopulate.rowHeight)
        let meta = CanvasNode.Meta(sources: turn.sourcesUsed.count, answered: true, preview: JourneyTurn.preview(answer, limit: 180), topics: Array(turn.filenames.prefix(3)), sourceIds: turn.sourcesUsed)
        let node = CanvasNode(id: CanvasNode.derivedId(refType: "canvas_answer", refId: turn.id), kind: CanvasNode.chatKind, refType: "canvas_answer", refId: turn.id,
                              title: String(question.prefix(60)), snapshot: .init(payload: "**Q:** \(question)\n\n\(answer)", metadata: meta),
                              x: anchor.0, y: anchor.1, topicId: picked.first?.topicId, parentId: picked.first?.topicId, createdAt: CanvasStore.stamp(), updatedAt: CanvasStore.stamp())
        layout.nodes.append(node)
        if let v = try? await library.documentVector(node.snapshot.payload) { vectors[node.id] = v }
        for p in picked where !p.isTopic { layout.upsert(CanvasEdge(source: p.id, target: node.id, state: "provenance", label: "asked about", createdAt: CanvasStore.stamp())) }
        for e in CanvasProvenance.edges(for: CanvasLayout(nodes: layout.nodes.filter { $0.id == node.id || $0.kind == CanvasNode.sourceKind })) { layout.upsert(e) }
        save()
        return answer
    }

    public func stop() { task?.cancel() }

    // MARK: plumbing

    private func generate(_ gemma: ModelContainer, instructions: String, prompt: String, tokens: Int) async -> String? {
        let session = ChatSession(gemma, instructions: instructions, generateParameters: GenerateParameters(maxTokens: tokens, temperature: 0.2))
        let box = TextBox()
        let work = Task { @MainActor in
            for try await item in session.streamDetails(to: prompt) { if case .chunk(let s) = item { box.append(s) } }
        }
        task = work
        let observer = GPUGuard.protect(work, label: "canvas")
        do { try await work.value } catch {}
        NotificationCenter.default.removeObserver(observer)
        task = nil
        return work.isCancelled ? nil : box.text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    static func preview(of item: StudioItem) -> String {
        switch item.kind {
        case .doc: return String(item.body.prefix(300))
        case .quiz: return (try? JSONDecoder().decode([StudioQuestion].self, from: Data(item.body.utf8))).map { "\($0.count) questions" } ?? "Quiz"
        case .cards: return (try? JSONDecoder().decode([StudioCard].self, from: Data(item.body.utf8))).map { "\($0.count) cards" } ?? "Flashcards"
        case .audio: return "Audio overview"
        case .video: return "Video"
        case .visual: return "Visual"
        }
    }
    static func preview(of item: Insight) -> String { item.kind.label + " · " + item.created.formatted(date: .abbreviated, time: .omitted) }

    private static func vectorsURL(notebook: String) -> URL { CanvasStore.folder.appendingPathComponent(CanvasStore.safe(notebook) + ".vectors.json") }
    private static func loadVectors(notebook: String) -> [String: [Float]] {
        guard let data = try? Data(contentsOf: vectorsURL(notebook: notebook)) else { return [:] }
        return (try? JSONDecoder().decode([String: [Float]].self, from: data)) ?? [:]
    }
    private static func saveVectors(_ v: [String: [Float]], notebook: String) {
        if let data = try? JSONEncoder().encode(v) { try? data.write(to: vectorsURL(notebook: notebook), options: .atomic) }
    }
}

private final class TextBox: @unchecked Sendable {
    private let lock = NSLock()
    private var buffer = ""
    func append(_ s: String) { lock.lock(); buffer += s; lock.unlock() }
    var text: String { lock.lock(); defer { lock.unlock() }; return buffer }
}
