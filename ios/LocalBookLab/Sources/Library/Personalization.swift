import Foundation

/// What makes the Mac's answers personal, as the phone receives it: the user's profile text and core memory
/// (sent whole), and one notebook's entities and relationships. Query-dependent memory is not part of it.
struct PersonalContext: Codable, Sendable {
    var notebookId: String
    var userContext: String
    var coreMemory: String
    var entities: [Entity]
    var relationships: [Relationship]
    var version: String?

    struct Entity: Codable, Sendable {
        var name: String
        var type: String
        var mentions: Int
        var sourceIds: [String]
        var snippets: [String]
    }
    struct Relationship: Codable, Sendable {
        var source: String
        var relationship: String
        var target: String
        var strength: Double
    }
}

extension AnswerRecipe {
    struct Routing: Codable, Sendable {
        var patterns: [String: [String]]
        var boosts: [String: [Double]]            // intent -> [tabular, text]
        var tabularTypes: [String]
        var textTypes: [String]
        var tabularExtensions: [String]
        var textExtensions: [String]
    }
    struct PersonalRules: Codable, Sendable {
        var boostFactor: Double, boostCap: Double, mentionDivisor: Double
        var sourceWeightNew: Double, sourceWeightAdd: Double
        var firstNameMinChars: Int, contextEntities: Int, snippets: Int
        var relationshipsPerEntity: Int, relationshipLines: Int
        var entityBackgroundChars: Int, relatedEntitiesChars: Int
    }

    /// A reranked passage as the boosts see it.
    struct Scored {
        var sourceId: String
        var text: String
        var sourceType: String
        var filename: String
        var rerank: Double
        var score: Double          // rerank plus the boosts
        var index: Int             // position before this step, to keep ties in the Mac's (stable) order
    }

    // MARK: response format hint (rag_query_analyzer.detect_response_format)

    func formatHint(_ question: String) -> String {
        let q = question.lowercased()
        for rule in formatRules ?? [] where rule.count == 2 && Self.matches(rule[0], q) { return rule[1] }
        return ""
    }

    static func matches(_ pattern: String, _ text: String) -> Bool {
        guard let re = try? NSRegularExpression(pattern: pattern) else { return false }
        return re.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) != nil
    }

    // MARK: source-type routing (source_router)

    func routingBoost(_ question: String) -> (tabular: Double, text: Double) {
        guard let r = routing else { return (0, 0) }
        let q = question.lowercased()
        let order = ["numeric", "comparison", "lookup", "explanation", "summary", "list"]
        let counts = order.map { name in (name, (r.patterns[name] ?? []).filter { Self.matches($0, q) }.count) }
        let best = counts.map(\.1).max() ?? 0
        let top = counts.filter { $0.1 == best }
        let intent = (best == 0 || top.count > 1) ? "mixed" : top[0].0
        let b = r.boosts[intent] ?? [0, 0]
        return (b.first ?? 0, b.count > 1 ? b[1] : 0)
    }

    // MARK: entities (entity_extractor)

    func entitiesIn(_ question: String, _ p: PersonalContext) -> [PersonalContext.Entity] {
        let rules = personalization
        let q = question.lowercased()
        let words = Set(q.split(whereSeparator: { $0.isWhitespace }).map(String.init))
        var found: [PersonalContext.Entity] = []
        for e in p.entities {
            let name = e.name.lowercased()
            if name.isEmpty { continue }
            if q.contains(name) { found.append(e) }
            else if e.type == "person", e.name.contains(" "),
                    let first = e.name.split(whereSeparator: { $0.isWhitespace }).first?.lowercased(),
                    first.count > (rules?.firstNameMinChars ?? 2), words.contains(first) { found.append(e) }
        }
        // Python's sort is stable: most-mentioned first, ties in the order the Mac listed them.
        return found.enumerated().sorted { ($0.element.mentions, $1.offset) > ($1.element.mentions, $0.offset) }.map(\.element)
    }

    /// Entity boost then source-type boost, each followed by the Mac's stable sort on the new score
    /// (rag_engine step 3c/3d). Passages must already carry their rerank score.
    func boosted(_ items: [Scored], question: String, personal: PersonalContext?) -> [Scored] {
        var out = items.enumerated().map { i, s -> Scored in var s = s; s.score = s.rerank; s.index = i; return s }
        func stableSort() {
            out = out.enumerated().sorted { a, b in
                a.element.score != b.element.score ? a.element.score > b.element.score : a.offset < b.offset
            }.map(\.element)
        }
        if let p = personal, let rules = personalization {
            let found = entitiesIn(question, p)
            var sourceBoost: [String: Double] = [:]
            for e in found {
                let weight = min(1.0, Double(e.mentions) / rules.mentionDivisor)
                for sid in e.sourceIds {
                    if let prev = sourceBoost[sid] { sourceBoost[sid] = min(1.0, prev + weight * rules.sourceWeightAdd) }
                    else { sourceBoost[sid] = weight * rules.sourceWeightNew }
                }
            }
            if !sourceBoost.isEmpty {
                let names = found.map { $0.name.lowercased() }
                for i in out.indices {
                    let text = out[i].text.lowercased()
                    var textBoost = 0.0
                    for n in names where text.contains(n) { textBoost += rules.boostFactor }
                    let total = min(rules.boostCap, (sourceBoost[out[i].sourceId] ?? 0) * rules.boostFactor + textBoost)
                    out[i].score = out[i].rerank + total
                }
                stableSort()
            }
        }
        if let r = routing {
            let b = routingBoost(question)
            if b.tabular != 0 || b.text != 0 {
                for i in out.indices {
                    let type = out[i].sourceType.lowercased()
                    let name = out[i].filename.lowercased()
                    var boost = 0.0
                    if r.tabularTypes.contains(type) { boost = b.tabular }
                    else if r.textTypes.contains(type) { boost = b.text }
                    else if r.tabularExtensions.contains(where: { name.hasSuffix($0) }) { boost = b.tabular }
                    else if r.textExtensions.contains(where: { name.hasSuffix($0) }) { boost = b.text }
                    out[i].score += boost
                }
                stableSort()
            }
        }
        return out
    }

    /// The text the Mac appends after the numbered passages: known entities and how they relate.
    func contextSuffix(question: String, personal p: PersonalContext?) -> String {
        guard let p, let rules = personalization else { return "" }
        let found = entitiesIn(question, p)
        guard !found.isEmpty else { return "" }
        var lines: [String] = []
        for e in found.prefix(rules.contextEntities) {
            let ctx = e.snippets.prefix(2).joined(separator: " | ")
            lines.append(!ctx.isEmpty ? "- \(e.name) (\(e.type)): \(ctx)" : "- \(e.name) (\(e.type)): mentioned \(e.mentions) times")
        }
        var parts: [String] = []
        let entityContext = "KNOWN ENTITIES:\n" + lines.joined(separator: "\n") + "\n\n"
        parts.append("Entity background: " + String(entityContext.prefix(rules.entityBackgroundChars)))

        var rel: [String] = []
        for e in found.prefix(rules.contextEntities) {
            let name = e.name.lowercased()
            let mine = p.relationships.filter { $0.source.lowercased() == name || $0.target.lowercased() == name }
            for r in mine.prefix(rules.relationshipsPerEntity) { rel.append("- \(r.source) \(r.relationship) \(r.target)") }
        }
        if !rel.isEmpty {
            let graph = "ENTITY RELATIONSHIPS:\n" + rel.prefix(rules.relationshipLines).joined(separator: "\n") + "\n\n"
            parts.append("Related entities: " + String(graph.prefix(rules.relatedEntitiesChars)))
        }
        return "\n\n---\nAdditional context:\n" + parts.joined(separator: "\n")
    }

    // MARK: proof against the Mac's own outputs

    func checkPersonalization() -> [String] {
        var problems: [String] = []
        for c in conformance.format ?? [] where formatHint(c.question) != c.hint {
            problems.append("response-format hint for “\(c.question.prefix(40))” differs")
        }
        for c in conformance.routing ?? [] {
            let b = routingBoost(c.question)
            if abs(b.tabular - c.tabular) > 1e-9 || abs(b.text - c.text) > 1e-9 {
                problems.append("source-type routing for “\(c.question.prefix(40))” differs")
            }
        }
        if let v = conformance.personal {
            let ctx = PersonalContext(notebookId: "nb", userContext: "", coreMemory: "", entities: v.entities,
                                      relationships: v.relationships, version: nil)
            let items = v.results.enumerated().map { i, r in
                Scored(sourceId: r.sourceId, text: r.text, sourceType: r.sourceType, filename: r.filename,
                       rerank: r.rerankScore, score: r.rerankScore, index: i)
            }
            for c in v.cases {
                let out = boosted(items, question: c.question, personal: ctx)
                if out.map(\.sourceId) != c.order || zip(out.map(\.score), c.scores).contains(where: { abs($0 - $1) > 1e-5 }) {
                    problems.append("entity/source boosts for “\(c.question.prefix(40))” differ")
                }
                if contextSuffix(question: c.question, personal: ctx) != c.suffix {
                    problems.append("entity context for “\(c.question.prefix(40))” differs")
                }
            }
        }
        return problems
    }
}
