import Foundation

/// The Mac's answer recipe, followed by the phone so both answer the same way. It is DATA the Mac publishes
/// (prompts, classifier rules, decode settings, cleaning steps), so a change on the Mac reaches the phone at
/// the next sync instead of needing a second copy to be edited. `conformance` holds sample inputs with the
/// outputs the Mac produced; `check()` re-runs the phone's own code on them and reports any mismatch.
public struct AnswerRecipe: Codable, Sendable {
    public var recipeVersion: String
    public var systemPrompts: [String: String]
    public var classify: Classify
    public var userPrompt: String
    public var contextItem: String
    public var contextSeparator: String
    public var generation: Generation
    public var retrieval: Retrieval
    public var queryExpansion: QueryExpansion
    public var citations: CitationRules
    public var cleanSteps: [CleanStep]
    public var formatRules: [[String]]?
    public var routing: Routing?
    public var personalization: PersonalRules?
    public var quality: Quality?
    public var variants: Variants?
    public var corrective: Corrective?
    public var conformance: Conformance

    public struct Classify: Codable, Sendable {
        public var complexPatterns: [String]
        public var factualPatterns: [String]
        public var longQuestionChars: Int
        public var multiPartPhrases: [String]
        public var multiPartRegex: String
    }
    public struct Generation: Codable, Sendable {
        public var temperature: Float
        public var maxTokens: Int
        public var topP: Float
        public var repetitionPenalty: Float
        public var repetitionContextSize: Int
        public var lengthHint: String?
    }
    public struct Retrieval: Codable, Sendable {
        public var topK: Int
        public var overcollect: Int
        public var rerankKeep: Int
        public var hybridK: Int
        public var vectorCandidates: Int
        public var bm25Candidates: Int
        public var rrfK: Int
        public var rrfMissingRank: Int
        public var bm25: BM25
        public var reranker: Reranker
        public struct BM25: Codable, Sendable { public var k1: Double; public var b: Double; public var epsilon: Double }
        public struct Reranker: Codable, Sendable { public var enabled: Bool; public var model: String; public var precision: String? }
    }
    public struct QueryExpansion: Codable, Sendable {
        public var expansions: [[String]]          // ordered [term, expansion] pairs
        public var nameExpansions: [[String]]
    }
    public struct CitationRules: Codable, Sendable {
        public var rerankFloor: Double, rerankScale: Double, rerankOffset: Double
        public var rrfBase: Double, rrfScale: Double
        public var minConfidence: Double, veryLowConfidence: Double
        public var fallbackKeep: Int
        public var combine: String?
        public var noAnswer: String
    }
    public struct CleanStep: Codable, Sendable {
        public var pattern: String
        public var replace: String
        public var flags: [String]
        public var keepOriginalIfEmpty: Bool?
    }
    public struct Conformance: Codable, Sendable {
        public var classification: [ClassCase]
        public var cleaning: [CleanCase]
        public var citationFix: [CitationCase]?
        public var format: [FormatCase]?
        public var quality: [QualityCase]?
        public var confidence: [ConfidenceCase]?
        public var variants: [VariantCase]?
        public var routing: [RoutingCase]?
        public var personal: PersonalCases?
        public var expansion: [ExpandCase]?
        public struct ExpandCase: Codable, Sendable { public var question: String; public var expanded: String }
        public struct ClassCase: Codable, Sendable { public var question: String; public var type: String }
        public struct CleanCase: Codable, Sendable { public var raw: String; public var cleaned: String }
        public struct CitationCase: Codable, Sendable { public var raw: String; public var count: Int; public var fixed: String }
        public struct FormatCase: Codable, Sendable { public var question: String; public var hint: String }
        public struct ConfidenceCase: Codable, Sendable { public var rerank: Double?; public var rrf: Double; public var confidence: Double }
        public struct QualityCase: Codable, Sendable { public var question: String; public var answer: String; public var type: String; public var ok: Bool }
        public struct VariantCase: Codable, Sendable { public var question: String; public var variants: [String] }
        public struct RoutingCase: Codable, Sendable { public var question: String; public var tabular: Double; public var text: Double }
        public struct PersonalCases: Codable, Sendable {
            public var entities: [PersonalContext.Entity]
            public var relationships: [PersonalContext.Relationship]
            public var results: [Passage]
            public var cases: [Case]
            public struct Passage: Codable, Sendable {
                public var sourceId: String; var text: String; var rerankScore: Double; var sourceType: String; var filename: String
            }
            public struct Case: Codable, Sendable { public var question: String; public var order: [String]; public var scores: [Double]; public var suffix: String }
        }
    }

    // MARK: the Mac's classifier (rag_query_analyzer.classify_query), driven by its pattern lists

    public func classify(_ question: String) -> String {
        let q = question.lowercased()
        let questionMarks = question.filter { $0 == "?" }.count
        let multiPart = questionMarks > 1
            || classify.multiPartPhrases.contains { q.contains($0) }
            || q.range(of: classify.multiPartRegex, options: .regularExpression) != nil
        if classify.complexPatterns.contains(where: { q.contains($0) }) { return "complex" }
        if question.count > classify.longQuestionChars || questionMarks > 1 { return "complex" }
        if multiPart { return "synthesis" }
        if classify.factualPatterns.contains(where: { q.contains($0) }) { return "factual" }
        return "synthesis"
    }

    // MARK: the Mac's rule-based query expansion (rag_query_analyzer.expand_query)

    public func expand(_ question: String) -> String {
        var expanded = question
        let q = question.lowercased()
        for pair in queryExpansion.expansions where pair.count == 2 && q.contains(pair[0]) && !q.contains(pair[1]) {
            expanded += " " + pair[1]
        }
        let words = Set(q.split(whereSeparator: \.isWhitespace).map(String.init))
        for pair in queryExpansion.nameExpansions where pair.count == 2 && words.contains(pair[0]) {
            expanded += " " + pair[1]
        }
        return expanded
    }

    /// Confidence of a reranked passage as the Mac computes it (rag_context.build_citations_and_context).
    public func confidence(rerank: Double, rrfNormalised: Double) -> Double {
        let c = citations
        if c.combine == "max" {            // the higher of the two signals wins (no cliff at the rerank floor)
            var best: Double?
            if rerank > c.rerankFloor { best = min(1, rerank * c.rerankScale + c.rerankOffset) }
            if rrfNormalised > 0 { best = max(best ?? 0, c.rrfBase + rrfNormalised * c.rrfScale) }
            return best ?? 0.5
        }
        if rerank > c.rerankFloor { return min(1, rerank * c.rerankScale + c.rerankOffset) }
        if rrfNormalised > 0 { return c.rrfBase + rrfNormalised * c.rrfScale }
        return 0.5
    }

    // MARK: prompts (rag_generation.generate_answer), without the user's profile and memory

    public func systemPrompt(for question: String, personal: PersonalContext? = nil, lengthWords: Int? = nil) -> String {
        let base = systemPrompts[classify(question)] ?? systemPrompts["synthesis"] ?? ""
        var parts: [String] = []
        if let p = personal {
            if !p.userContext.isEmpty { parts.append("User context: " + p.userContext) }
            if !p.coreMemory.isEmpty { parts.append(p.coreMemory) }
        }
        parts.append(base)
        if classify(question) == "factual" {
            let hint = formatHint(question)
            if !hint.isEmpty { parts.append(hint) }
        }
        if let words = lengthWords {
            parts.append("Keep the whole answer under \(words) words and finish every sentence.")      // the user's chosen length
        } else if let hint = generation.lengthHint, !hint.isEmpty { parts.append(hint) }
        return parts.joined(separator: "\n\n")
    }

    public func userPrompt(question: String, passages: [String], suffix: String = "") -> String {
        let context = passages.enumerated()
            .map { contextItem.replacingOccurrences(of: "{n}", with: "\($0.offset + 1)").replacingOccurrences(of: "{text}", with: $0.element) }
            .joined(separator: contextSeparator) + suffix
        return userPrompt.replacingOccurrences(of: "{context}", with: context).replacingOccurrences(of: "{question}", with: question)
    }


    // MARK: the Mac's output cleaner (rag_generation.clean_llm_output), as data

    public func clean(_ text: String) -> String {
        var out = text
        for step in cleanSteps {
            var opts: NSRegularExpression.Options = []
            if step.flags.contains("dotall") { opts.insert(.dotMatchesLineSeparators) }
            if step.flags.contains("ignorecase") { opts.insert(.caseInsensitive) }
            guard let re = try? NSRegularExpression(pattern: step.pattern, options: opts) else { continue }
            let before = out
            // Python's `$` (no MULTILINE) matches at the very end or just before a final newline; so does ICU's.
            let template = step.replace.replacingOccurrences(of: "\\1", with: "$1")
            out = re.stringByReplacingMatches(in: out, range: NSRange(out.startIndex..., in: out), withTemplate: template)
            if step.keepOriginalIfEmpty == true, out.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
               !before.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { out = before }
        }
        return out.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// Mac `rag_generation.fix_citations`: in a bracket made only of citation-like items, a whole number from 1
    /// to `count` stays, "<section> in Source N" becomes N when N is a real source, everything else goes
    /// (section numbers, "[Local Context]" tags); an emptied bracket goes with the space before it.
    public func fixCitations(_ text: String, count: Int) -> String {
        guard count >= 1 else { return text }
        let item = #"(?:\d+(?:\.\d+)*(?:\s+in\s+Source\s+\d+)?|(?:Local(?:Book)?\s+|Contextual\s+)?(?:Context|Note))"#
        guard let re = try? NSRegularExpression(pattern: "( ?)\\[\\s*(\(item)(?:\\s*[,;]\\s*\(item))*)\\s*\\]", options: .caseInsensitive),
              let inSource = try? NSRegularExpression(pattern: #"\bin\s+Source\s+(\d+)$"#, options: .caseInsensitive) else { return text }
        func number(_ raw: String) -> Int? {
            let s = raw.trimmingCharacters(in: .whitespaces)
            var n: Int?
            if !s.isEmpty, s.allSatisfy(\.isNumber) { n = Int(s) }
            else if let m = inSource.firstMatch(in: s, range: NSRange(s.startIndex..., in: s)), let r = Range(m.range(at: 1), in: s) { n = Int(s[r]) }
            guard let n, n >= 1, n <= count else { return nil }
            return n
        }
        let ns = text as NSString
        var out = "", last = 0
        for m in re.matches(in: text, range: NSRange(location: 0, length: ns.length)) {
            out += ns.substring(with: NSRange(location: last, length: m.range.location - last))
            last = m.range.location + m.range.length
            let whole = ns.substring(with: m.range)
            let items = ns.substring(with: m.range(at: 2)).trimmingCharacters(in: .whitespaces)
                .components(separatedBy: CharacterSet(charactersIn: ",;")).map { $0.trimmingCharacters(in: .whitespaces) }
            var keep: [String] = []
            for it in items { if let n = number(it), !keep.contains(String(n)) { keep.append(String(n)) } }
            if keep.count == items.count, items.allSatisfy({ !$0.isEmpty && $0.allSatisfy(\.isNumber) }) { out += whole }
            else if !keep.isEmpty { out += ns.substring(with: m.range(at: 1)) + "[" + keep.joined(separator: ", ") + "]" }
        }
        return out + ns.substring(from: last)
    }

    /// An answer that ran out of tokens ends mid-sentence; cut it back to the last finished sentence or list
    /// item so it never reads as if the model stopped on purpose. Leaves it alone if there is no full stop.
    public func trimUnfinished(_ text: String) -> String {
        guard let re = try? NSRegularExpression(pattern: #"[.!?](?:\*\*|\*|\]|\)|"|')*(?=\s|$)"#) else { return text }
        let ns = text as NSString
        guard let last = re.matches(in: text, range: NSRange(location: 0, length: ns.length)).last,
              last.range.location + last.range.length > text.count / 3 else { return text }
        return ns.substring(to: last.range.location + last.range.length)
    }

    /// Re-run the phone's code on the Mac's conformance vectors. Empty = the phone behaves like the Mac.
    public func check() -> [String] {
        var problems: [String] = []
        for c in conformance.classification where classify(c.question) != c.type {
            problems.append("question type for “\(c.question.prefix(50))”: Mac says \(c.type), phone says \(classify(c.question))")
        }
        for c in conformance.expansion ?? [] where expand(c.question) != c.expanded {
            problems.append("query expansion for “\(c.question.prefix(40))” differs")
        }
        for c in conformance.cleaning where clean(c.raw) != c.cleaned {
            problems.append("answer cleaning differs for “\(c.raw.prefix(40).replacingOccurrences(of: "\n", with: " "))”")
        }
        for c in conformance.citationFix ?? [] where fixCitations(c.raw, count: c.count) != c.fixed {
            problems.append("citation numbers differ for “\(c.raw.prefix(40).replacingOccurrences(of: "\n", with: " "))”")
        }
        problems += checkPersonalization()
        problems += checkCorrective()
        for c in conformance.confidence ?? [] where abs(confidence(rerank: c.rerank ?? 0, rrfNormalised: c.rrf) - c.confidence) > 1e-9 {
            problems.append("citation confidence for rerank \(c.rerank.map { String($0) } ?? "none") / rrf \(c.rrf) differs")
        }
        return problems
    }
}
