import Foundation

/// The prompts the insights are made with and the readers of what comes back. The texts follow the old app's
/// `structured_llm`, `contradiction_detector`, `entity_extractor` and `topic_perspectives`, so the shapes match; the
/// readers take the model's reply as it comes (a JSON array or object, inside prose or fences) through `JSONObjects`.
public enum InsightPrompts {
    static let plain = "Write plain JSON only: no markdown fences, no commentary before or after."

    // MARK: timeline

    public static let timelineInstructions = """
    You are a timeline extractor. Identify dates and events from the numbered passages of the user's own notes. \
    Output a valid JSON object with this structure: \
    {"events": [{"date": "date or time period", "title": "short title", "description": "what happened", \
    "importance": "low/medium/high", "passage": 1}], "time_span": "overall time period", "context": "brief context"}. \
    Rules: extract every date, year, quarter and time period mentioned; include approximate dates when exact ones are \
    unknown; "passage" is the number of the passage the event comes from; rate importance by significance in the \
    content; only events the passages state, nothing from outside. \(plain)
    """

    public static func timelinePrompt(passages: [String]) -> String {
        "Passages:\n\n" + numbered(passages, limit: 1_400) + "\n\nExtract the timeline."
    }

    /// Events from one reply, with the passage index resolved to a source when `sources` is given.
    public static func timelineEvents(from raw: String, sources: [(id: String, filename: String)] = []) -> (events: [TimelineInsight.Event], timeSpan: String?, context: String?) {
        let objects = JSONObjects.scan(in: raw)
        var events: [TimelineInsight.Event] = []
        var span: String?, context: String?
        func add(_ o: [String: Any]) {
            guard let title = clean(o["title"]), !title.isEmpty else { return }
            let date = clean(o["date"]) ?? ""
            let n = (o["passage"] as? Int) ?? Int(clean(o["passage"]) ?? "") ?? 0
            let src = n >= 1 && n <= sources.count ? sources[n - 1] : nil
            events.append(.init(date: date, title: title, description: clean(o["description"]) ?? "", importance: importance(o["importance"]),
                                sortKey: DateKey.parse(date), sourceId: src?.id, filename: src?.filename))
        }
        for o in objects {
            if let list = o["events"] as? [[String: Any]] {
                span = clean(o["time_span"]) ?? span; context = clean(o["context"]) ?? context
                list.forEach(add)
            } else if o["title"] != nil, o["date"] != nil { add(o) }
        }
        return (events, span, context)
    }

    /// Events from every window merged: the same title on the same date is one event; dated ones first, in order.
    public static func mergeTimeline(_ parts: [(events: [TimelineInsight.Event], timeSpan: String?, context: String?)]) -> TimelineInsight {
        var seen = Set<String>()
        var events: [TimelineInsight.Event] = []
        for part in parts { for e in part.events where seen.insert(key(e.title) + "|" + key(e.date)).inserted { events.append(e) } }
        events.sort { a, b in
            switch (a.sortKey, b.sortKey) {
            case let (x?, y?): x != y ? x < y : a.title < b.title
            case (_?, nil): true
            case (nil, _?): false
            default: a.date < b.date
            }
        }
        let dated = events.compactMap(\.sortKey)
        let span = parts.compactMap(\.timeSpan).first ?? (dated.isEmpty ? "" : "\(Int(dated.min()!)) to \(Int(dated.max()!))")
        return TimelineInsight(events: events, timeSpan: span, context: parts.compactMap(\.context).first ?? "")
    }

    // MARK: entities and the graph

    public static let entityInstructions = """
    Extract named entities from the numbered passages of the user's own notes. Output ONLY a JSON array: \
    [{"name": "...", "type": "...", "context": "...", "passages": [1, 3]}]. Types: person (people's names), \
    company (companies and organizations), location (places, cities, countries), product (products, tools, software), \
    concept (the ideas, methods and themes the passages are about), metric (numbers with their meaning). \
    Normalize names ("Chris Norman", not "Chris"); "context" is a brief phrase showing how the entity is used; \
    "passages" lists the numbers of the passages it appears in. At most 25 entities, the most important first. \(plain)
    """

    public static func entityPrompt(passages: [String]) -> String {
        "Passages:\n\n" + numbered(passages, limit: 1_200) + "\n\nExtract the entities."
    }

    public struct Mention: Sendable, Equatable { public var name: String; public var type: String; public var context: String; public var passages: [Int] }

    public static func entities(from raw: String) -> [Mention] {
        JSONObjects.scan(in: raw).compactMap { o in
            guard let name = clean(o["name"]), name.count > 1, let type = clean(o["type"]) else { return nil }
            let passages = (o["passages"] as? [Any] ?? []).compactMap { ($0 as? Int) ?? Int("\($0)") }
            return Mention(name: name, type: type.lowercased(), context: clean(o["context"]) ?? "", passages: passages)
        }
    }

    public static let relationInstructions = """
    You are given the entities found in the user's own notes and, for each pair that appears in the same passage, \
    the passages they share. For each pair, say how they are related in a few words, from the passages only. \
    Output ONLY a JSON array: [{"a": "entity name", "b": "entity name", "relation": "a few words"}]. \
    Leave out a pair the passages do not connect. \(plain)
    """

    public static func relationPrompt(pairs: [(a: String, b: String, passage: String)]) -> String {
        pairs.enumerated().map { i, p in "[\(i + 1)] \(p.a) — \(p.b)\n\(String(p.passage.prefix(700)))" }.joined(separator: "\n\n") + "\n\nName each relation."
    }

    public static func relations(from raw: String) -> [(a: String, b: String, relation: String)] {
        JSONObjects.scan(in: raw).compactMap { o in
            guard let a = clean(o["a"]), let b = clean(o["b"]), let r = clean(o["relation"]), !r.isEmpty else { return nil }
            return (a, b, r)
        }
    }

    /// The graph from every window's mentions: the same name (case and possessive aside) is one node; an edge joins
    /// two entities named in the same passage, weighted by how often.
    public static func buildGraph(windows: [(mentions: [Mention], passageSources: [String])], relations: [(a: String, b: String, relation: String)] = [], maxNodes: Int = 40) -> GraphInsight {
        struct Acc { var name: String; var type: String; var mentions = 0; var context = ""; var sources = Set<String>() }
        var nodes: [String: Acc] = [:]
        var edges: [String: Int] = [:]
        for w in windows {
            var byPassage: [Int: [String]] = [:]
            for m in w.mentions {
                let k = key(m.name)
                var acc = nodes[k] ?? Acc(name: m.name, type: m.type)
                acc.mentions += max(1, m.passages.count)
                if acc.context.isEmpty { acc.context = m.context }
                if acc.type == "concept", m.type != "concept" { acc.type = m.type }
                for p in m.passages where p >= 1 && p <= w.passageSources.count { acc.sources.insert(w.passageSources[p - 1]); byPassage[p, default: []].append(k) }
                nodes[k] = acc
            }
            for (_, names) in byPassage {
                let list = Array(Set(names)).sorted()
                for i in list.indices { for j in list.indices where j > i { edges[list[i] + "|" + list[j], default: 0] += 1 } }
            }
        }
        let kept = nodes.sorted { $0.value.mentions > $1.value.mentions || ($0.value.mentions == $1.value.mentions && $0.key < $1.key) }.prefix(maxNodes)
        let keptKeys = Set(kept.map(\.key))
        var relationFor: [String: String] = [:]
        for r in relations { relationFor[[key(r.a), key(r.b)].sorted().joined(separator: "|")] = r.relation }
        let outEdges = edges.compactMap { (k, w) -> GraphInsight.Edge? in
            let parts = k.split(separator: "|").map(String.init)
            guard parts.count == 2, keptKeys.contains(parts[0]), keptKeys.contains(parts[1]) else { return nil }
            return GraphInsight.Edge(a: parts[0], b: parts[1], relation: relationFor[k] ?? "", weight: w)
        }.sorted { $0.weight > $1.weight || ($0.weight == $1.weight && $0.id < $1.id) }
        return GraphInsight(nodes: kept.map { GraphInsight.Node(id: $0.key, name: $0.value.name, type: $0.value.type, mentions: $0.value.mentions, context: $0.value.context, sourceIds: $0.value.sources.sorted()) },
                            edges: outEdges)
    }

    // MARK: contradictions

    public static let claimInstructions = """
    Extract factual claims from the numbered passages of the user's own notes. Each claim is a single, atomic \
    statement that can be checked: facts, statistics, dates, conclusions, recommendations. Output ONLY a JSON array: \
    [{"text": "the claim", "type": "factual|statistical|temporal|conclusion", "passage": 1}]. If there are no clear \
    claims, output []. \(plain)
    """

    public static func claimPrompt(passages: [String]) -> String { "Passages:\n\n" + numbered(passages, limit: 1_400) + "\n\nExtract the claims." }

    public struct Claim: Sendable, Equatable { public var text: String; public var type: String; public var sourceId: String; public var filename: String }

    public static func claims(from raw: String, sources: [(id: String, filename: String)]) -> [Claim] {
        JSONObjects.scan(in: raw).compactMap { o in
            guard let text = clean(o["text"]), text.count > 12 else { return nil }
            let n = (o["passage"] as? Int) ?? Int(clean(o["passage"]) ?? "") ?? 0
            let src = n >= 1 && n <= sources.count ? sources[n - 1] : sources.first
            return Claim(text: text, type: clean(o["type"]) ?? "factual", sourceId: src?.id ?? "", filename: src?.filename ?? "")
        }
    }

    public static let contradictionInstructions = """
    Analyze whether two claims from the user's own notes contradict each other. If they contradict, output \
    {"contradicts": true, "type": "factual|statistical|temporal|methodological|interpretive", "severity": "low|medium|high", \
    "explanation": "brief explanation of the contradiction", "resolution_hint": "possible reason for the difference"}. \
    If they do not (they agree, are unrelated, or are compatible), output {"contradicts": false}. \(plain)
    """

    public static func contradictionPrompt(_ a: Claim, _ b: Claim) -> String {
        "Claim 1 (from \"\(a.filename)\"):\n\"\(a.text)\"\n\nClaim 2 (from \"\(b.filename)\"):\n\"\(b.text)\"\n\nDo they contradict?"
    }

    public static func contradiction(from raw: String, _ a: Claim, _ b: Claim) -> ContradictionsInsight.Item? {
        guard let o = JSONObjects.scan(in: raw).first, truthy(o["contradicts"]) else { return nil }
        return .init(claimA: a.text, sourceA: a.filename, claimB: b.text, sourceB: b.filename, type: clean(o["type"]) ?? "factual",
                     severity: ["low", "medium", "high"].contains(clean(o["severity"]) ?? "") ? clean(o["severity"])! : "medium",
                     explanation: clean(o["explanation"]) ?? "", resolutionHint: clean(o["resolution_hint"]))
    }

    // MARK: comparison

    public static let comparisonInstructions = """
    You are a document comparison expert. Analyze two documents from the user's own notes and compare them. \
    Output a valid JSON object: {"similarities": ["..."], "differences": ["..."], "unique_to_first": ["points only in the first"], \
    "unique_to_second": ["points only in the second"], "synthesis": "a synthesized understanding combining both"}. \
    Be specific about what is similar and different; note contradictions; keep every list to the five most important points. \(plain)
    """

    public static func comparisonPrompt(a: String, b: String, titleA: String, titleB: String, focus: String?, limit: Int = 6_000) -> String {
        (focus.map { "FOCUS: \($0)\n\n" } ?? "") + "DOCUMENT 1 (\(titleA)):\n\(String(a.prefix(limit)))\n\nDOCUMENT 2 (\(titleB)):\n\(String(b.prefix(limit)))"
    }

    public static func comparison(from raw: String, sourceA: String, titleA: String, sourceB: String, titleB: String, focus: String?) -> ComparisonInsight? {
        guard let o = JSONObjects.scan(in: raw).first(where: { $0["synthesis"] != nil || $0["similarities"] != nil }) else { return nil }
        return ComparisonInsight(sourceA: sourceA, titleA: titleA, sourceB: sourceB, titleB: titleB, focus: focus,
                                 similarities: strings(o["similarities"]), differences: strings(o["differences"]),
                                 uniqueToA: strings(o["unique_to_first"]), uniqueToB: strings(o["unique_to_second"]), synthesis: clean(o["synthesis"]) ?? "")
    }

    // MARK: perspectives

    public static let perspectiveInstructions = """
    You read one source's excerpts about a topic and report its stance. Output ONLY {"take": "one or two sentences: \
    what this source says about the topic", "claims": ["up to three specific claims it makes, each one sentence"]}. \
    Only what the excerpts say; if they do not address the topic, output {"take": "", "claims": []}. \(plain)
    """

    public static func perspectivePrompt(topic: String, filename: String, excerpts: [String]) -> String {
        "TOPIC: \(topic)\n\nSOURCE: \(filename)\n\nEXCERPTS:\n" + excerpts.map { String($0.prefix(1_200)) }.joined(separator: "\n\n---\n\n")
    }

    public static func perspective(from raw: String, sourceId: String, filename: String) -> PerspectivesInsight.Perspective? {
        guard let o = JSONObjects.scan(in: raw).first else { return nil }
        let take = clean(o["take"]) ?? "", claims = Array(strings(o["claims"]).prefix(3))
        guard !take.isEmpty || !claims.isEmpty else { return nil }
        return .init(sourceId: sourceId, filename: filename, take: take, claims: claims)
    }

    public static let consensusInstructions = """
    You are given what several sources from the user's own notes say about one topic. Output ONLY \
    {"consensus": ["points two or more sources agree on"], "contested": ["points where sources disagree, naming the sides"], \
    "synthesis": "two to four sentences that weigh the sources together"}. Only from the takes and claims given. \(plain)
    """

    public static func consensusPrompt(topic: String, perspectives: [PerspectivesInsight.Perspective]) -> String {
        "TOPIC: \(topic)\n\n" + perspectives.map { "SOURCE \($0.filename):\nTake: \($0.take)\n" + $0.claims.map { "- " + $0 }.joined(separator: "\n") }.joined(separator: "\n\n")
    }

    public static func consensus(from raw: String) -> (consensus: [String], contested: [String], synthesis: String) {
        guard let o = JSONObjects.scan(in: raw).first else { return ([], [], "") }
        return (strings(o["consensus"]), strings(o["contested"]), clean(o["synthesis"]) ?? "")
    }

    // MARK: topics

    public static let topicNameInstructions = """
    Each numbered group below is the key words of one theme found in the user's own notes, with a sample passage. \
    Give each a 2-4 word name a reader would recognise. Output ONLY a JSON array: [{"group": 1, "name": "..."}]. \(plain)
    """

    public static func topicNamePrompt(groups: [(keywords: [String], sample: String)]) -> String {
        groups.enumerated().map { i, g in "[\(i + 1)] \(g.keywords.joined(separator: ", "))\nSample: \(String(g.sample.prefix(400)))" }.joined(separator: "\n\n")
    }

    public static func topicNames(from raw: String, count: Int) -> [Int: String] {
        var out: [Int: String] = [:]
        for o in JSONObjects.scan(in: raw) {
            guard let n = (o["group"] as? Int) ?? Int(clean(o["group"]) ?? ""), n >= 1, n <= count, let name = clean(o["name"]), !name.isEmpty else { continue }
            out[n - 1] = name
        }
        return out
    }

    // MARK: helpers

    static func numbered(_ passages: [String], limit: Int) -> String {
        passages.enumerated().map { "[\($0 + 1)]\n\(String($1.prefix(limit)))" }.joined(separator: "\n\n")
    }
    static func clean(_ v: Any?) -> String? {
        guard let v else { return nil }
        if let s = v as? String { return s.trimmingCharacters(in: .whitespacesAndNewlines) }
        if v is NSNull { return nil }
        return "\(v)".trimmingCharacters(in: .whitespacesAndNewlines)
    }
    static func strings(_ v: Any?) -> [String] { (v as? [Any] ?? []).compactMap { clean($0) }.filter { !$0.isEmpty } }
    static func truthy(_ v: Any?) -> Bool { (v as? Bool) ?? (clean(v)?.lowercased() == "true") }
    static func importance(_ v: Any?) -> String { let s = clean(v)?.lowercased() ?? ""; return ["low", "medium", "high"].contains(s) ? s : "medium" }
    /// A key for merging names: lower-cased, letters and digits only, a trailing possessive dropped.
    public static func key(_ s: String) -> String {
        var t = s.lowercased().replacingOccurrences(of: "’", with: "'")
        if t.hasSuffix("'s") { t.removeLast(2) }
        return String(t.unicodeScalars.filter { CharacterSet.alphanumerics.contains($0) })
    }
}

/// A sortable number for the dates a model writes: `2023-06-30`, `June 2023`, `Q1 2025`, `1990s`, `mid-2024`,
/// `March 3, 2021`, `FY23`, `2019`. The year plus a fraction for the month and day; nil when no year is found.
public enum DateKey {
    static let months = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]

    public static func parse(_ text: String) -> Double? {
        let s = text.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty else { return nil }
        // ISO or slashed: 2023-06-30, 2023/06/30, 2023-06
        if let m = s.firstMatch(of: /(\d{4})[-\/](\d{1,2})(?:[-\/](\d{1,2}))?/), let y = Int(m.1), let mo = Int(m.2), (1...12).contains(mo) {
            return Double(y) + fraction(month: mo, day: m.3.flatMap { Int($0) } ?? 1)
        }
        // US style 6/30/2023
        if let m = s.firstMatch(of: /(\d{1,2})\/(\d{1,2})\/(\d{4})/), let mo = Int(m.1), let d = Int(m.2), let y = Int(m.3), (1...12).contains(mo) {
            return Double(y) + fraction(month: mo, day: d)
        }
        var year: Int?
        if let m = s.firstMatch(of: /(?:^|\D)(1[6-9]\d\d|20\d\d|21\d\d)(?!\d)/) { year = Int(m.1) }      // `1990s` counts; no lookbehind in Swift Regex
        else if let m = s.firstMatch(of: /\bfy\s?'?(\d{2})\b/) { year = 2000 + (Int(m.1) ?? 0) }
        else if let m = s.firstMatch(of: /\b'(\d{2})\b/) { let n = Int(m.1) ?? 0; year = n < 50 ? 2000 + n : 1900 + n }
        guard let year else { return nil }
        // A decade: 1990s → the start of it.
        if s.contains("\(year)s") { return Double(year) }
        if let m = s.firstMatch(of: /\bq([1-4])\b/), let q = Int(m.1) { return Double(year) + fraction(month: (q - 1) * 3 + 1, day: 1) }
        if let m = s.firstMatch(of: /\bh([12])\b/), let h = Int(m.1) { return Double(year) + fraction(month: (h - 1) * 6 + 1, day: 1) }
        for (i, name) in months.enumerated() where s.contains(name) || s.contains(String(name.prefix(3)) + " ") || s.hasSuffix(String(name.prefix(3))) || s.contains(String(name.prefix(3)) + ".") {
            let day = s.firstMatch(of: /\b(\d{1,2})(?:st|nd|rd|th)?\b(?!\d*\s*(?:bc|ad))/).flatMap { Int($0.1) }.flatMap { (1...31).contains($0) ? $0 : nil } ?? 1
            return Double(year) + fraction(month: i + 1, day: day)
        }
        if s.contains("spring") { return Double(year) + fraction(month: 4, day: 1) }
        if s.contains("summer") { return Double(year) + fraction(month: 7, day: 1) }
        if s.contains("fall") || s.contains("autumn") { return Double(year) + fraction(month: 10, day: 1) }
        if s.contains("winter") || s.contains("early") { return Double(year) }
        if s.contains("mid") { return Double(year) + 0.5 }
        if s.contains("late") || s.contains("end of") { return Double(year) + 0.9 }
        return Double(year)
    }

    static func fraction(month: Int, day: Int) -> Double { (Double(month - 1) + Double(max(1, min(31, day)) - 1) / 31) / 12 }

    /// A short label for a key: the year, or month and year when the key carries a month.
    public static func label(_ key: Double) -> String {
        let year = Int(key.rounded(.down))
        let month = Int(((key - Double(year)) * 12).rounded(.down))
        return month > 0 ? "\(months[month].prefix(3).capitalized) \(year)" : "\(year)"
    }
}
