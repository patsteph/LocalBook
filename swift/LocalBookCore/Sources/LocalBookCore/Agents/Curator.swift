import Foundation

/// The Curator: the one agent that sees every notebook. Its profile (name, personality, voice) and what it writes
/// (briefs, the weekly wrap, devil's advocate) and finds (cross-notebook connections) live under `Curator/`. The
/// prompts are the old app's, trimmed to what this app records.
public struct CuratorProfile: Codable, Sendable, Hashable {
    public enum Voice: String, Codable, Sendable, CaseIterable, Identifiable {
        case smartColleague = "smart_colleague", executiveBrief = "executive_brief", conversationalAnalyst = "conversational_analyst"
        public var id: String { rawValue }
        public var label: String { switch self { case .smartColleague: "Smart colleague"; case .executiveBrief: "Executive brief"; case .conversationalAnalyst: "Conversational analyst" } }
        public var description: String {
            switch self {
            case .smartColleague: "Observational, opinion-bearing, gently candid"
            case .executiveBrief: "Crisp, status-focused, action-oriented"
            case .conversationalAnalyst: "Chatty, curious, signposts thinking, invites engagement"
            }
        }
        public var prompt: String {
            switch self {
            case .smartColleague:
                "VOICE: Write like a smart colleague who has been paying attention to this person's research for a while. Observational, opinion-bearing, gently candid. Use phrases like 'I noticed', 'you've been wrestling with', 'this looks like'. Don't pad with statistics — name the actual thing. First-person curator voice. Comfortable being a little blunt when something matters."
            case .executiveBrief:
                "VOICE: Write a crisp executive brief. Status. Open questions. Suggested action. Lead with what changed. Use short paragraphs and bulleted observations. Tone: deliberate, professional, useful. Minimal first-person — focus on the situation. Skip pleasantries; the reader is busy."
            case .conversationalAnalyst:
                "VOICE: Write like a curious analyst chatting through what they're seeing. Use phrases like 'curious what you'll make of', 'this actually undercuts', 'I keep coming back to'. First-person curator voice, conversational but substantive. Signpost your reasoning so the user can follow your thinking. Mix observations with light questions — invite engagement."
            }
        }
    }
    public var name: String
    public var personality: String
    public var voice: Voice
    public var excludedNotebooks: [String]
    public var mutedTopics: [String]
    public var lastBrief: Date?
    public var lastWeekly: Date?
    enum CodingKeys: String, CodingKey { case name, personality, voice, excludedNotebooks = "excluded_notebooks", mutedTopics = "muted_topics", lastBrief = "last_brief", lastWeekly = "last_weekly" }
    public init(name: String = "Curator", personality: String = "thoughtful, well-read, direct", voice: Voice = .smartColleague, excludedNotebooks: [String] = [], mutedTopics: [String] = []) {
        self.name = name; self.personality = personality; self.voice = voice; self.excludedNotebooks = excludedNotebooks; self.mutedTopics = mutedTopics
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? "Curator"
        personality = try c.decodeIfPresent(String.self, forKey: .personality) ?? "thoughtful, well-read, direct"
        voice = Voice(rawValue: try c.decodeIfPresent(String.self, forKey: .voice) ?? "") ?? .smartColleague
        excludedNotebooks = try c.decodeIfPresent([String].self, forKey: .excludedNotebooks) ?? []
        mutedTopics = try c.decodeIfPresent([String].self, forKey: .mutedTopics) ?? []
        lastBrief = try c.decodeIfPresent(Date.self, forKey: .lastBrief)
        lastWeekly = try c.decodeIfPresent(Date.self, forKey: .lastWeekly)
    }
}

/// Something the Curator wrote.
public struct CuratorPiece: Codable, Sendable, Hashable, Identifiable {
    public enum Kind: String, Codable, Sendable, CaseIterable { case brief, weekly, patterns, devilsAdvocate = "devils_advocate", digest }
    public var id: String
    public var kind: Kind
    public var title: String
    public var markdown: String
    public var created: Date
    public var notebookId: String?
    enum CodingKeys: String, CodingKey { case id, kind, title, markdown, created, notebookId = "notebook_id" }
    public init(id: String = UUID().uuidString, kind: Kind, title: String, markdown: String, created: Date = Date(), notebookId: String? = nil) {
        self.id = id; self.kind = kind; self.title = title; self.markdown = markdown; self.created = created; self.notebookId = notebookId
    }
}

/// A connection between two notebooks: passages that say close to the same thing, named by the model.
public struct CuratorConnection: Codable, Sendable, Hashable, Identifiable {
    public enum Status: String, Codable, Sendable { case active, approved, dismissed }
    public var id: String
    public var notebookA: String, titleA: String
    public var notebookB: String, titleB: String
    public var strength: Double
    public var label: String
    public var passageA: String, passageB: String
    public var fileA: String, fileB: String
    public var status: Status
    public var created: Date
    enum CodingKeys: String, CodingKey { case id, notebookA = "notebook_a", titleA = "title_a", notebookB = "notebook_b", titleB = "title_b", strength, label, passageA = "passage_a", passageB = "passage_b", fileA = "file_a", fileB = "file_b", status, created }
    public init(notebookA: String, titleA: String, notebookB: String, titleB: String, strength: Double, label: String, passageA: String, passageB: String, fileA: String, fileB: String) {
        id = CanvasEdge.pairId(notebookA + "/" + fileA, notebookB + "/" + fileB, "nb")
        self.notebookA = notebookA; self.titleA = titleA; self.notebookB = notebookB; self.titleB = titleB; self.strength = strength; self.label = label
        self.passageA = passageA; self.passageB = passageB; self.fileA = fileA; self.fileB = fileB; status = .active; created = Date()
    }
}

public struct CuratorState: Codable, Sendable, Hashable {
    public var profile: CuratorProfile
    public var pieces: [CuratorPiece]
    public var connections: [CuratorConnection]
    public var digests: [String: CuratorPiece]        // notebook → its latest digest
    public init(profile: CuratorProfile = CuratorProfile()) { self.profile = profile; pieces = []; connections = []; digests = [:] }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        profile = try c.decodeIfPresent(CuratorProfile.self, forKey: .profile) ?? CuratorProfile()
        pieces = try c.decodeIfPresent([CuratorPiece].self, forKey: .pieces) ?? []
        connections = try c.decodeIfPresent([CuratorConnection].self, forKey: .connections) ?? []
        digests = try c.decodeIfPresent([String: CuratorPiece].self, forKey: .digests) ?? [:]
    }
    enum CodingKeys: String, CodingKey { case profile, pieces, connections, digests }
}

public enum CuratorStore {
    public static var folder: URL {
        let dir = AppFolders.base.appendingPathComponent("Curator", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }
    static var url: URL { folder.appendingPathComponent("curator.json") }
    public static func load() -> CuratorState {
        let d = JSONDecoder(); d.dateDecodingStrategy = .iso8601
        guard let data = try? Data(contentsOf: url), let s = try? d.decode(CuratorState.self, from: data) else { return CuratorState() }
        return s
    }
    public static func save(_ s: CuratorState) {
        let e = JSONEncoder(); e.dateEncodingStrategy = .iso8601; e.outputFormatting = [.sortedKeys]
        if let data = try? e.encode(s) { try? data.write(to: url, options: .atomic) }
    }
}

/// The brief's raw data and prompts. The data block is built from the event log, the journey and the stores; the
/// model turns it into the newsletter. Numbers are computed here and quoted, never worked out by the model.
public enum CuratorBrief {
    public struct NotebookFacts: Sendable, Hashable {
        public var id: String, title: String
        public var totalSources: Int
        public var newSources: [String]            // titles added in the window
        public var collectorSources: [String]      // of those, the ones the Collector added
        public var questions: [String]
        public var gaps: [String]
        public var studio: [String]
        public var insights: [String]
        public var pending: Int                    // Collector items waiting
        public init(id: String, title: String, totalSources: Int, newSources: [String], collectorSources: [String], questions: [String], gaps: [String], studio: [String], insights: [String], pending: Int) {
            self.id = id; self.title = title; self.totalSources = totalSources; self.newSources = newSources; self.collectorSources = collectorSources; self.questions = questions; self.gaps = gaps; self.studio = studio; self.insights = insights; self.pending = pending
        }
        public var active: Bool { !newSources.isEmpty || !questions.isEmpty || !studio.isEmpty || !insights.isEmpty || pending > 0 }
    }

    public static func rawData(_ facts: [NotebookFacts], since: Date, now: Date = Date(), connections: [CuratorConnection] = []) -> String {
        let f = DateFormatter(); f.dateStyle = .medium; f.timeStyle = .short
        var out = "Window: \(f.string(from: since)) → \(f.string(from: now)) (\(span(since, now)))\n"
        let totals = (sources: facts.reduce(0) { $0 + $1.newSources.count }, collector: facts.reduce(0) { $0 + $1.collectorSources.count },
                      questions: facts.reduce(0) { $0 + $1.questions.count }, studio: facts.reduce(0) { $0 + $1.studio.count }, pending: facts.reduce(0) { $0 + $1.pending })
        out += "Totals: \(totals.sources) sources added (\(totals.collector) by the Collector, \(totals.sources - totals.collector) by you), \(totals.questions) questions asked, \(totals.studio) Studio items made, \(totals.pending) Collector items waiting for approval\n"
        out += "Notebooks: \(facts.count) in all, \(facts.filter(\.active).count) active in the window\n\n"
        for n in facts where n.active {
            out += "## \(n.title) (\(n.totalSources) sources in all)\n"
            if !n.newSources.isEmpty { out += "- New sources (\(n.newSources.count)): " + n.newSources.prefix(8).map { "\"\($0)\"" }.joined(separator: ", ") + (n.newSources.count > 8 ? ", …" : "") + "\n" }
            if !n.collectorSources.isEmpty { out += "- Of those, added by the Collector: " + n.collectorSources.prefix(6).map { "\"\($0)\"" }.joined(separator: ", ") + "\n" }
            if !n.questions.isEmpty { out += "- Questions asked (\(n.questions.count)): " + n.questions.prefix(6).map { "\"\($0)\"" }.joined(separator: "; ") + "\n" }
            if !n.gaps.isEmpty { out += "- Questions the notebook could not answer: " + n.gaps.prefix(4).map { "\"\($0)\"" }.joined(separator: "; ") + "\n" }
            if !n.studio.isEmpty { out += "- Studio: " + n.studio.prefix(6).joined(separator: ", ") + "\n" }
            if !n.insights.isEmpty { out += "- Insights: " + n.insights.prefix(6).joined(separator: ", ") + "\n" }
            if n.pending > 0 { out += "- \(n.pending) Collector item\(n.pending == 1 ? "" : "s") waiting for approval\n" }
            out += "\n"
        }
        let quiet = facts.filter { !$0.active }
        if !quiet.isEmpty { out += "Quiet notebooks: " + quiet.prefix(10).map(\.title).joined(separator: ", ") + "\n" }
        let fresh = connections.filter { $0.status != .dismissed && $0.created >= since }
        if !fresh.isEmpty { out += "\nCross-notebook connections found: " + fresh.prefix(5).map { "\($0.titleA) ↔ \($0.titleB): \($0.label)" }.joined(separator: "; ") + "\n" }
        return out
    }

    public static func span(_ a: Date, _ b: Date) -> String {
        let hours = Int(b.timeIntervalSince(a) / 3_600)
        if hours < 1 { return "less than an hour" }
        if hours < 48 { return "\(hours) hour\(hours == 1 ? "" : "s")" }
        return "\(hours / 24) days"
    }

    static let formatting = """
        NEWSLETTER FORMATTING (CRITICAL):
        - Use markdown for a modern newsletter layout: `###` headers for each notebook or major section, **bold** for source titles, key numbers and names, bullet points (`-`) for lists.
        - Keep paragraphs very short (1-2 sentences). No dense walls of text. Be highly scannable. Blank lines between sections.
        - Use the notebook and source names exactly as given. Use only numbers that appear in the data; never compute or round.
        - Do not invent dates, people or events. If the window was quiet, say so briefly and suggest one thing to pick up.
        """

    public static func briefPrompt(profile: CuratorProfile, rawData: String, now: Date = Date(), awayFor: String) -> (instructions: String, prompt: String) {
        let f = DateFormatter(); f.dateFormat = "MMMM d, yyyy"
        let instructions = "You are \(profile.name), the Curator of the user's research notebooks. Personality: \(profile.personality).\n\(profile.voice.prompt)"
        let prompt = """
            Write the morning brief for today, \(f.string(from: now)). The user was away for \(awayFor). Turn the raw activity data below into something worth reading. \
            Today's date is \(f.string(from: now)); use it exactly.

            ACTIVITY DATA:
            \(rawData)

            BRIEF STRUCTURE:
            1. A one-line opening that says what changed, in the voice.
            2. ### per active notebook: what came in (name the actual sources), what was asked, what it could not answer, what was made.
            3. ### Collector: what it gathered on its own and what waits for approval, kept apart from what the user added.
            4. ### Threads to pick up: unanswered questions and connections worth a look.
            5. A closing stat line: "Since last time: X sources added, Y questions, Z Studio items."

            RULES: 200-400 words. \(formatting)
            Write the brief now:
            """
        return (instructions, prompt)
    }

    public static func weeklyPrompt(profile: CuratorProfile, rawData: String, now: Date = Date()) -> (instructions: String, prompt: String) {
        let f = DateFormatter(); f.dateFormat = "MMMM d, yyyy"
        let instructions = "You are \(profile.name), a personal research assistant. Personality: \(profile.personality).\n\(profile.voice.prompt)"
        let prompt = """
            You are writing a WEEKLY WRAP UP for \(f.string(from: now)). This covers the ENTIRE past week of the user's research activity — a broader, more reflective view than the daily morning brief.

            RAW DATA:
            \(rawData)

            WEEKLY WRAP UP STRUCTURE:
            1. **Opening** — A warm "Here's your week in review" opening. Set a reflective tone.
            2. **The Big Picture** — What were the major themes across all notebooks this week? Any patterns emerging?
            3. **Per-notebook highlights** — For each active notebook, summarize the week's key additions and discoveries. Use actual titles.
            4. **Collector Report** — If the background collector gathered sources, summarize what it found. Distinguish clearly from what the user added themselves.
            5. **Your Activity** — How actively did the user engage? Questions, Studio items, insights. Frame it positively.
            6. **Threads to Pick Up** — Open questions and unfinished conversations worth revisiting this week.
            7. **Looking Ahead** — Based on this week's momentum, what should the user focus on next week?
            8. **Weekly Stat Line** — End with a clean summary: "This week: X sources added, Y questions, Z Studio items."

            RULES:
            - Use exact numbers from the data. Never invent or round.
            - This is a WEEKLY summary — use "this week", "over the past week" framing.
            - Distinguish collector-gathered sources from user-added ones.
            - Length: 300-500 words.

            \(formatting)
            Tone: warm, reflective, slightly celebratory of progress. Like a trusted advisor reviewing the week together.

            Write the weekly wrap up now:
            """
        return (instructions, prompt)
    }

    public static func digestPrompt(notebook: String, subject: String, sources: Int, recentTitles: [String], themes: [String], concepts: [String], shared: [String]) -> String {
        """
        Summarize this research notebook's current state in 3-4 sentences.

        Notebook: \(notebook)\(subject.isEmpty ? "" : " (tracking: \(subject))")
        Sources: \(sources) total\(recentTitles.isEmpty ? "" : ", recent additions: " + recentTitles.joined(separator: ", "))
        Key themes (from clustering): \(themes.isEmpty ? "not yet clustered" : themes.joined(separator: ", "))
        Top concepts: \(concepts.isEmpty ? "none extracted yet" : concepts.joined(separator: ", "))
        Cross-notebook concepts (shared with other notebooks): \(shared.isEmpty ? "none" : shared.joined(separator: ", "))

        Write a concise summary covering: what this notebook is about, its current research direction, and any notable patterns. Be specific — use actual topic and concept names.
        """
    }

    public static func patternsPrompt(profile: CuratorProfile, pairs: [(titleA: String, passageA: String, titleB: String, passageB: String)]) -> (instructions: String, prompt: String) {
        let list = pairs.enumerated().map { i, p in "Pair \(i + 1):\n[\(p.titleA)] \(String(p.passageA.prefix(500)))\n[\(p.titleB)] \(String(p.passageB.prefix(500)))" }.joined(separator: "\n\n")
        let prompt = """
            Below are pairs of passages from DIFFERENT notebooks that say close to the same thing. For each pair, name the connection in one short phrase (3 to 8 words): the theme, entity or claim they share. Then say in one sentence why it might matter to the user that these two notebooks meet here.

            \(list)

            Reply as JSON: {"pairs": [{"n": 1, "label": "…", "why": "…"}, …]}. Reply with the JSON only.
            """
        return ("You are \(profile.name), a research curator who notices what connects a person's notebooks. Personality: \(profile.personality).", prompt)
    }

    /// The model's labels for the pairs, by number.
    public static func patternLabels(from raw: String) -> [Int: (label: String, why: String)] {
        guard let obj = JSONObjects.first(in: raw), let pairs = obj["pairs"] as? [[String: Any]] else { return [:] }
        var out: [Int: (String, String)] = [:]
        for p in pairs {
            let n = (p["n"] as? Int) ?? Int((p["n"] as? String) ?? "") ?? ((p["n"] as? Double).map { Int($0) }) ?? 0
            guard n > 0, let label = (p["label"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines), !label.isEmpty else { continue }
            out[n] = (label, (p["why"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines))
        }
        return out
    }

    public static func devilsAdvocatePrompt(profile: CuratorProfile, thesis: String, passages: [(notebook: String, filename: String, text: String)]) -> (instructions: String, prompt: String) {
        let numbered = passages.enumerated().map { i, p in "[\(i + 1)] (\(p.notebook) · \(p.filename))\n\(String(p.text.prefix(900)))" }.joined(separator: "\n\n")
        let prompt = """
            Thesis to challenge: "\(thesis)"

            Passages from the user's notebooks:
            \(numbered)

            Play devil's advocate. Write:
            1. **The strongest case against** the thesis, in 2-4 short paragraphs, grounded in the passages and cited like [1], [2].
            2. **What the thesis gets right**, in one short paragraph, so the challenge is fair.
            3. **What would settle it**: 2-3 bullet points naming the evidence that would decide the question.
            Use only the passages for facts; where they are silent, say what is missing rather than inventing it. 250-400 words.
            """
        return ("You are \(profile.name), a research curator playing devil's advocate. Personality: \(profile.personality). Be sharp but fair; cite passages by number.", prompt)
    }

    /// The best cross-notebook pairs from sampled chunk vectors: cosine above `threshold`, one pair per source pair,
    /// strongest first, at most `max`. Pure: the engine supplies the vectors.
    public struct Sample: Sendable { public let notebookId: String, notebookTitle: String, sourceId: String, filename: String, text: String, vector: [Float]
        public init(notebookId: String, notebookTitle: String, sourceId: String, filename: String, text: String, vector: [Float]) { self.notebookId = notebookId; self.notebookTitle = notebookTitle; self.sourceId = sourceId; self.filename = filename; self.text = text; self.vector = vector } }

    public static func crossNotebookPairs(_ samples: [Sample], threshold: Float = 0.74, max: Int = 12) -> [CuratorConnection] {
        var best: [String: (a: Sample, b: Sample, score: Float)] = [:]
        for i in samples.indices { for j in samples.indices where j > i {
            let a = samples[i], b = samples[j]
            guard a.notebookId != b.notebookId, a.vector.count == b.vector.count, !a.vector.isEmpty else { continue }
            let s = VectorMath.dot(VectorMath.normalized(a.vector), VectorMath.normalized(b.vector))
            guard s >= threshold else { continue }
            let key = [a.notebookId + "/" + a.sourceId, b.notebookId + "/" + b.sourceId].sorted().joined(separator: "|")
            if let cur = best[key], cur.score >= s { continue }
            best[key] = (a, b, s)
        } }
        return best.values.sorted { $0.score > $1.score || ($0.score == $1.score && $0.a.sourceId < $1.a.sourceId) }.prefix(max).map { p in
            CuratorConnection(notebookA: p.a.notebookId, titleA: p.a.notebookTitle, notebookB: p.b.notebookId, titleB: p.b.notebookTitle, strength: Double(p.score),
                              label: "", passageA: p.a.text, passageB: p.b.text, fileA: p.a.filename, fileB: p.b.filename)
        }
    }
}
