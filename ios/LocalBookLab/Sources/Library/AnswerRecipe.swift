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
    var cleanSteps: [CleanStep]
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
    }
    struct Retrieval: Codable, Sendable {
        var topK: Int
        var overcollect: Int
        var rerankKeep: Int
        var rrfK: Int
        var candidatesPerRanker: Int
        var reranker: Reranker
        struct Reranker: Codable, Sendable { var enabled: Bool; var model: String }
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
        struct ClassCase: Codable, Sendable { var question: String; var type: String }
        struct CleanCase: Codable, Sendable { var raw: String; var cleaned: String }
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

    // MARK: prompts (rag_generation.generate_answer), without the user's profile and memory

    func systemPrompt(for question: String) -> String {
        systemPrompts[classify(question)] ?? systemPrompts["synthesis"] ?? ""
    }

    func userPrompt(question: String, passages: [String]) -> String {
        let context = passages.enumerated()
            .map { contextItem.replacingOccurrences(of: "{n}", with: "\($0.offset + 1)").replacingOccurrences(of: "{text}", with: $0.element) }
            .joined(separator: contextSeparator)
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

    /// Re-run the phone's code on the Mac's conformance vectors. Empty = the phone behaves like the Mac.
    func check() -> [String] {
        var problems: [String] = []
        for c in conformance.classification where classify(c.question) != c.type {
            problems.append("question type for “\(c.question.prefix(50))”: Mac says \(c.type), phone says \(classify(c.question))")
        }
        for c in conformance.cleaning where clean(c.raw) != c.cleaned {
            problems.append("answer cleaning differs for “\(c.raw.prefix(40).replacingOccurrences(of: "\n", with: " "))”")
        }
        return problems
    }
}
