import Foundation
import MLXLMCommon

/// The Mac's answer recipe, followed by the phone so both answer the same way. It is DATA the Mac publishes
/// (prompts, classifier rules, decode settings, cleaning steps), so a change on the Mac reaches the phone at
/// the next sync instead of needing a second copy to be edited. `conformance` holds sample inputs with the
/// outputs the Mac produced; `check()` re-runs the phone's own code on them and reports any mismatch.
struct AnswerRecipe: Codable, Sendable {
    var recipeVersion: String
    var systemPrompts: [String: String]
    var classify: Classify
    var userPrompt: String
    var contextItem: String
    var contextSeparator: String
    var generation: Generation
    var retrieval: Retrieval
    var queryExpansion: QueryExpansion
    var citations: CitationRules
    var cleanSteps: [CleanStep]
    var formatRules: [[String]]?
    var routing: Routing?
    var personalization: PersonalRules?
    var quality: Quality?
    var variants: Variants?
    var corrective: Corrective?
    var conformance: Conformance

    struct Classify: Codable, Sendable {
        var complexPatterns: [String]
        var factualPatterns: [String]
        var longQuestionChars: Int
        var multiPartPhrases: [String]
        var multiPartRegex: String
    }
    struct Generation: Codable, Sendable {
        var temperature: Float
        var maxTokens: Int
        var topP: Float
        var repetitionPenalty: Float
        var repetitionContextSize: Int
        var lengthHint: String?
    }
    struct Retrieval: Codable, Sendable {
        var topK: Int
        var overcollect: Int
        var rerankKeep: Int
        var hybridK: Int
        var vectorCandidates: Int
        var bm25Candidates: Int
        var rrfK: Int
        var rrfMissingRank: Int
        var bm25: BM25
        var reranker: Reranker
        struct BM25: Codable, Sendable { var k1: Double; var b: Double; var epsilon: Double }
        struct Reranker: Codable, Sendable { var enabled: Bool; var model: String; var precision: String? }
    }
    struct QueryExpansion: Codable, Sendable {
        var expansions: [[String]]          // ordered [term, expansion] pairs
        var nameExpansions: [[String]]
    }
    struct CitationRules: Codable, Sendable {
        var rerankFloor: Double, rerankScale: Double, rerankOffset: Double
        var rrfBase: Double, rrfScale: Double
        var minConfidence: Double, veryLowConfidence: Double
        var fallbackKeep: Int
        var noAnswer: String
    }
    struct CleanStep: Codable, Sendable {
        var pattern: String
        var replace: String
        var flags: [String]
        var keepOriginalIfEmpty: Bool?
    }
    struct Conformance: Codable, Sendable {
        var classification: [ClassCase]
        var cleaning: [CleanCase]
        var citationFix: [CitationCase]?
        var format: [FormatCase]?
        var quality: [QualityCase]?
        var variants: [VariantCase]?
        var routing: [RoutingCase]?
        var personal: PersonalCases?
        var expansion: [ExpandCase]?
        struct ExpandCase: Codable, Sendable { var question: String; var expanded: String }
        struct ClassCase: Codable, Sendable { var question: String; var type: String }
        struct CleanCase: Codable, Sendable { var raw: String; var cleaned: String }
        struct CitationCase: Codable, Sendable { var raw: String; var count: Int; var fixed: String }
        struct FormatCase: Codable, Sendable { var question: String; var hint: String }
        struct QualityCase: Codable, Sendable { var question: String; var answer: String; var type: String; var ok: Bool }
        struct VariantCase: Codable, Sendable { var question: String; var variants: [String] }
        struct RoutingCase: Codable, Sendable { var question: String; var tabular: Double; var text: Double }
        struct PersonalCases: Codable, Sendable {
            var entities: [PersonalContext.Entity]
            var relationships: [PersonalContext.Relationship]
            var results: [Passage]
            var cases: [Case]
            struct Passage: Codable, Sendable {
                var sourceId: String; var text: String; var rerankScore: Double; var sourceType: String; var filename: String
            }
            struct Case: Codable, Sendable { var question: String; var order: [String]; var scores: [Double]; var suffix: String }
        }
    }

    // MARK: the Mac's classifier (rag_query_analyzer.classify_query), driven by its pattern lists

    func classify(_ question: String) -> String {
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

    func expand(_ question: String) -> String {
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
    func confidence(rerank: Double, rrfNormalised: Double) -> Double {
        let c = citations
        if rerank > c.rerankFloor { return min(1, rerank * c.rerankScale + c.rerankOffset) }
        if rrfNormalised > 0 { return c.rrfBase + rrfNormalised * c.rrfScale }
        return 0.5
    }

    // MARK: prompts (rag_generation.generate_answer), without the user's profile and memory

    func systemPrompt(for question: String, personal: PersonalContext? = nil) -> String {
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
        if let hint = generation.lengthHint, !hint.isEmpty { parts.append(hint) }
        return parts.joined(separator: "\n\n")
    }

    func userPrompt(question: String, passages: [String], suffix: String = "") -> String {
        let context = passages.enumerated()
            .map { contextItem.replacingOccurrences(of: "{n}", with: "\($0.offset + 1)").replacingOccurrences(of: "{text}", with: $0.element) }
            .joined(separator: contextSeparator) + suffix
        return userPrompt.replacingOccurrences(of: "{context}", with: context).replacingOccurrences(of: "{question}", with: question)
    }

    var generateParameters: GenerateParameters {
        var p = GenerateParameters(maxTokens: generation.maxTokens, temperature: generation.temperature)
        p.topP = generation.topP
        p.repetitionPenalty = generation.repetitionPenalty
        p.repetitionContextSize = generation.repetitionContextSize
        return p
    }

    // MARK: the Mac's output cleaner (rag_generation.clean_llm_output), as data

    func clean(_ text: String) -> String {
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

    /// Mac `rag_generation.fix_citations`: a bracket made only of numbers keeps the entries that are whole
    /// numbers from 1 to `count` (the sources given); an emptied bracket goes, with the space before it.
    func fixCitations(_ text: String, count: Int) -> String {
        guard count >= 1,
              let re = try? NSRegularExpression(pattern: #"( ?)\[\s*(\d+(?:\.\d+)*(?:\s*[,;]\s*\d+(?:\.\d+)*)*)\s*\]"#) else { return text }
        let ns = text as NSString
        var out = "", last = 0
        for m in re.matches(in: text, range: NSRange(location: 0, length: ns.length)) {
            out += ns.substring(with: NSRange(location: last, length: m.range.location - last))
            last = m.range.location + m.range.length
            let whole = ns.substring(with: m.range)
            let items = ns.substring(with: m.range(at: 2)).trimmingCharacters(in: .whitespaces)
                .components(separatedBy: CharacterSet(charactersIn: ",;")).map { $0.trimmingCharacters(in: .whitespaces) }
            let keep = items.filter { Int($0).map { $0 >= 1 && $0 <= count } ?? false && !$0.contains(".") }
            if keep.count == items.count { out += whole }
            else if !keep.isEmpty { out += ns.substring(with: m.range(at: 1)) + "[" + keep.joined(separator: ", ") + "]" }
        }
        return out + ns.substring(from: last)
    }

    /// An answer that ran out of tokens ends mid-sentence; cut it back to the last finished sentence or list
    /// item so it never reads as if the model stopped on purpose. Leaves it alone if there is no full stop.
    func trimUnfinished(_ text: String) -> String {
        guard let re = try? NSRegularExpression(pattern: #"[.!?](?:\*\*|\*|\]|\)|"|')*(?=\s|$)"#) else { return text }
        let ns = text as NSString
        guard let last = re.matches(in: text, range: NSRange(location: 0, length: ns.length)).last,
              last.range.location + last.range.length > text.count / 3 else { return text }
        return ns.substring(to: last.range.location + last.range.length)
    }

    /// Re-run the phone's code on the Mac's conformance vectors. Empty = the phone behaves like the Mac.
    func check() -> [String] {
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
        return problems
    }
}
