import Foundation
import LocalBookCore
import MLXLMCommon
import Observation

/// The Curator: briefs and the weekly wrap from what happened across every notebook, cross-notebook patterns from
/// the search model's vectors named by the answer model, devil's advocate against a thesis from every notebook's
/// passages, a digest per notebook. All of it stays on this Mac.
@MainActor @Observable
public final class CuratorEngine {
    public private(set) var state = CuratorStore.load()
    public private(set) var working = false
    public private(set) var status = ""
    public private(set) var error: String?

    public init() {}

    public var profile: CuratorProfile { state.profile }
    public func setProfile(_ p: CuratorProfile) { state.profile = p; CuratorStore.save(state) }
    public var pieces: [CuratorPiece] { state.pieces.sorted { $0.created > $1.created } }
    public var connections: [CuratorConnection] { state.connections.filter { $0.status != .dismissed }.sorted { $0.strength > $1.strength } }

    public func delete(_ piece: CuratorPiece) { state.pieces.removeAll { $0.id == piece.id }; CuratorStore.save(state) }
    public func dismiss(_ c: CuratorConnection) { set(c, .dismissed) }
    public func approve(_ c: CuratorConnection) { set(c, .approved) }
    private func set(_ c: CuratorConnection, _ status: CuratorConnection.Status) {
        if let i = state.connections.firstIndex(where: { $0.id == c.id }) { state.connections[i].status = status; if status == .approved { state.connections[i].strength = min(1, state.connections[i].strength + 0.1) } }
        CuratorStore.save(state)
    }

    // MARK: facts

    /// What each notebook did in the window, from the stores and the event log.
    public func facts(since: Date, library: LibraryModel) async -> [CuratorBrief.NotebookFacts] {
        let events = EventLog.since(since)
        var out: [CuratorBrief.NotebookFacts] = []
        for nb in library.notebooks where !state.profile.excludedNotebooks.contains(nb.id) {
            let sources = await library.sources(in: nb.id)
            let iso = ISO8601DateFormatter()
            let recent = sources.filter { s in (iso.date(from: s.created) ?? Self.parse(s.created)).map { $0 >= since } ?? false }.map(\.name)
            let byCollector = events.filter { $0.notebookId == nb.id && ($0.kind == "collector_stored" || ($0.kind == "source_added" && $0.detail.contains("Collector"))) }.map(\.title)
            let turns = JourneyStore.turns(notebook: nb.id).filter { $0.timestamp >= since && CanvasPopulate.isLearning($0.question) }
            let gaps = turns.filter { CanvasGaps.reason(for: $0) != nil }.map(\.question)
            let studio = StudioStore.all(notebook: nb.id).filter { $0.created >= since }.map { "\($0.kind.label): \($0.title)" }
            let insights = InsightStore.all(notebook: nb.id).filter { $0.created >= since }.map { "\($0.kind.label)" }
            let pending = Agents.collector.state(nb.id).pending.count
            out.append(.init(id: nb.id, title: nb.title, totalSources: sources.count, newSources: recent.isEmpty ? events.filter { $0.notebookId == nb.id && $0.kind == "source_added" }.map(\.title) : recent,
                             collectorSources: byCollector, questions: turns.map(\.question), gaps: gaps, studio: studio, insights: insights, pending: pending))
        }
        return out
    }

    static func parse(_ s: String) -> Date? {
        let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX")
        for fmt in ["yyyy-MM-dd'T'HH:mm:ss.SSSSSS", "yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd HH:mm:ss", "yyyy-MM-dd"] { f.dateFormat = fmt; if let d = f.date(from: s) { return d } }
        return nil
    }

    // MARK: pieces

    /// The morning brief: since the last one (or the last day), across every notebook.
    public func brief(library: LibraryModel) async -> CuratorPiece? {
        let since = state.profile.lastBrief ?? Date().addingTimeInterval(-86_400)
        return await write(.brief, title: "Brief · \(Date().formatted(date: .abbreviated, time: .omitted))", library: library) { gemma in
            self.status = "Reading what happened…"
            let facts = await self.facts(since: since, library: library)
            let raw = CuratorBrief.rawData(facts, since: since, connections: self.state.connections)
            let p = CuratorBrief.briefPrompt(profile: self.state.profile, rawData: raw, awayFor: CuratorBrief.span(since, Date()))
            self.status = "\(self.state.profile.name) is writing…"
            guard let text = await AgentText.generate(gemma, instructions: p.instructions, prompt: p.prompt, tokens: 900, temperature: 0.5, label: "curator") else { return nil }
            self.state.profile.lastBrief = Date()
            return text
        }
    }

    public func weekly(library: LibraryModel) async -> CuratorPiece? {
        let since = Date().addingTimeInterval(-7 * 86_400)
        return await write(.weekly, title: "Week in review · \(Date().formatted(date: .abbreviated, time: .omitted))", library: library) { gemma in
            self.status = "Reading the week…"
            let facts = await self.facts(since: since, library: library)
            let raw = CuratorBrief.rawData(facts, since: since, connections: self.state.connections)
            let p = CuratorBrief.weeklyPrompt(profile: self.state.profile, rawData: raw)
            self.status = "\(self.state.profile.name) is writing…"
            guard let text = await AgentText.generate(gemma, instructions: p.instructions, prompt: p.prompt, tokens: 1_100, temperature: 0.5, label: "curator") else { return nil }
            self.state.profile.lastWeekly = Date()
            return text
        }
    }

    /// Cross-notebook patterns: sampled passages of every notebook compared by vector, the close pairs named.
    public func patterns(library: LibraryModel) async -> CuratorPiece? {
        await write(.patterns, title: "Patterns across notebooks · \(Date().formatted(date: .abbreviated, time: .omitted))", library: library) { gemma in
            guard let store = library.store else { self.error = "The library is not open."; return nil }
            let notebooks = library.notebooks.filter { !self.state.profile.excludedNotebooks.contains($0.id) }
            guard notebooks.count >= 2 else { self.error = "Patterns need at least two notebooks."; return nil }
            self.status = "Sampling every notebook…"
            var samples: [CuratorBrief.Sample] = []
            for nb in notebooks {
                let rows = await store.chunkVectors(notebook: nb.id, limit: 1_500)
                let step = max(1, rows.count / 60)
                for (i, r) in rows.enumerated() where i % step == 0 {
                    samples.append(.init(notebookId: nb.id, notebookTitle: nb.title, sourceId: r.sourceId, filename: r.filename, text: r.text, vector: r.vector))
                }
            }
            let pairs = await Task.detached(priority: .userInitiated) { [samples] in CuratorBrief.crossNotebookPairs(samples) }.value
            guard !pairs.isEmpty else { self.error = "No two notebooks say close to the same thing yet."; return nil }
            self.status = "Naming \(pairs.count) connection\(pairs.count == 1 ? "" : "s")…"
            let p = CuratorBrief.patternsPrompt(profile: self.state.profile, pairs: pairs.map { ($0.titleA, $0.passageA, $0.titleB, $0.passageB) })
            let labels = (await AgentText.generate(gemma, instructions: p.instructions, prompt: p.prompt, tokens: 700, temperature: 0.2, label: "curator")).map(CuratorBrief.patternLabels) ?? [:]
            var md = "Passages from different notebooks that meet. Approve the ones that matter, dismiss the rest; the Curator remembers.\n\n"
            for (i, pair) in pairs.enumerated() {
                var c = pair
                let named = labels[i + 1]
                c.label = named?.label ?? "Shared ground"
                if let old = self.state.connections.firstIndex(where: { $0.id == c.id }) {
                    if self.state.connections[old].status == .dismissed { continue }
                    self.state.connections[old].strength = c.strength; self.state.connections[old].label = c.label
                } else { self.state.connections.append(c); EventLog.record("connection_found", notebook: nil, title: "\(c.titleA) ↔ \(c.titleB)", detail: c.label) }
                md += "### \(c.label)\n**\(c.titleA)** (\(c.fileA)) ↔ **\(c.titleB)** (\(c.fileB)) · \(Int(c.strength * 100)) %\n\n"
                if let why = named?.why, !why.isEmpty { md += "\(why)\n\n" }
                md += "> \(String(c.passageA.prefix(240)))\n\n> \(String(c.passageB.prefix(240)))\n\n"
            }
            return md
        }
    }

    /// Argue against a thesis from every notebook's passages.
    public func devilsAdvocate(_ thesis: String, library: LibraryModel) async -> CuratorPiece? {
        let thesis = thesis.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !thesis.isEmpty else { return nil }
        return await write(.devilsAdvocate, title: "Devil's advocate · \(String(thesis.prefix(60)))", library: library) { gemma in
            self.status = "Reading every notebook on it…"
            guard let found = try? await library.searchAcrossNotebooks(thesis, topK: 8) else { self.error = "The search model is not ready."; return nil }
            let passages = found.hits.map { (notebook: $0.notebookTitle, filename: $0.filename, text: $0.text) }
            guard !passages.isEmpty else { self.error = "No notebook has anything on that thesis."; return nil }
            let p = CuratorBrief.devilsAdvocatePrompt(profile: self.state.profile, thesis: thesis, passages: passages)
            self.status = "\(self.state.profile.name) is arguing…"
            guard let text = await AgentText.generate(gemma, instructions: p.instructions, prompt: p.prompt, tokens: 800, temperature: 0.4, label: "curator") else { return nil }
            let cites = "\n\n---\n" + passages.enumerated().map { "[\($0.offset + 1)] \($0.element.notebook) · \($0.element.filename)" }.joined(separator: "  \n")
            return text + cites
        }
    }

    /// A 3–4 sentence digest of one notebook's state, kept until the next rebuild.
    public func digest(_ notebook: LibraryNotebookInfo, library: LibraryModel) async -> CuratorPiece? {
        guard !working else { return nil }
        working = true; error = nil; status = "Reading \(notebook.title)…"
        defer { working = false; status = "" }
        guard let gemma = await AgentText.model(library) else { error = "The answer model is still getting ready."; return nil }
        let sources = await library.sources(in: notebook.id)
        let recent = sources.sorted { $0.created > $1.created }.prefix(5).map { String($0.name.prefix(80)) }
        var topics = InsightStore.all(notebook: notebook.id).first { $0.kind == .topics }?.decode(TopicsInsight.self)?.topics.map(\.name) ?? []
        if topics.isEmpty, let store = library.store { topics = await store.topics(notebook: notebook.id).map(\.name) }
        let concepts = InsightStore.all(notebook: notebook.id).first { $0.kind == .graph }?.decode(GraphInsight.self)?.nodes.sorted { $0.mentions > $1.mentions }.prefix(8).map(\.name) ?? []
        let shared = state.connections.filter { $0.status != .dismissed && ($0.notebookA == notebook.id || $0.notebookB == notebook.id) }.map(\.label)
        let subject = Agents.collector.state(notebook.id).config.subject
        let prompt = CuratorBrief.digestPrompt(notebook: notebook.title, subject: subject, sources: sources.count, recentTitles: Array(recent), themes: Array(topics.prefix(6)), concepts: Array(concepts), shared: Array(shared.prefix(4)))
        guard let text = await AgentText.generate(gemma, instructions: "You summarize research notebooks plainly and specifically.", prompt: prompt, tokens: 220, label: "curator") else { return nil }
        let piece = CuratorPiece(kind: .digest, title: notebook.title, markdown: text, notebookId: notebook.id)
        state.digests[notebook.id] = piece
        CuratorStore.save(state)
        return piece
    }

    /// Every notebook's digest, for the local API's `todays_digest` and the brain status.
    public func digests() -> [CuratorPiece] { state.digests.values.sorted { $0.title < $1.title } }

    // MARK: plumbing

    private func write(_ kind: CuratorPiece.Kind, title: String, library: LibraryModel, _ body: (ModelContainer) async -> String?) async -> CuratorPiece? {
        guard !working else { return nil }
        working = true; error = nil; status = "Getting ready…"
        defer { working = false; status = "" }
        guard GPUGuard.canStart else { error = "Open the app and try again: this can't run while the screen is off."; return nil }
        guard let gemma = await AgentText.model(library) else { error = "The answer model is still getting ready. Try again in a moment."; return nil }
        let awake = KeepAwake.begin(reason: "Curator")
        defer { awake.end() }
        guard let text = await body(gemma) else { if error == nil { error = "Stopped before it finished." }; return nil }
        let piece = CuratorPiece(kind: kind, title: title, markdown: text)
        state.pieces.append(piece)
        state.pieces = Array(state.pieces.suffix(100))
        CuratorStore.save(state)
        EventLog.record("brief_written", notebook: nil, title: title, detail: kind.rawValue)
        return piece
    }
}
