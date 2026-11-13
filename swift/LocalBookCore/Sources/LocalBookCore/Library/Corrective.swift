import Foundation

/// The Mac's "second look": when an answer looks weak (rag_query_analyzer.check_answer_quality) it retrieves
/// again with rewritten queries (generate_query_variants, rag_search.corrective_retrieval) and answers again.
extension AnswerRecipe {
    public struct Quality: Codable, Sendable {
        public var minChars: Int
        public var failurePhrases: [String]
        public var placeholderRegex: String
        public var placeholderTokens: [String]
        public var metaPhrases: [String]
    }
    public struct Variants: Codable, Sendable {
        public var expansions: [[String]]
        public var countWords: [String]
        public var countSuffix: String
        public var entityRegex: String
        public var max: Int
    }
    public struct Corrective: Codable, Sendable {
        public var hybridK: Int
        public var vectorCandidates: Int
        public var bm25Candidates: Int
        public var resultCap: Int
        public var rerankKeep: Int
    }

    /// False when the Mac would retry: too short, an admission of failure, a factual answer without a number,
    /// or leftover placeholders.
    public func answerLooksGood(question: String, answer: String, type: String) -> Bool {
        guard let q = quality else { return true }
        if answer.trimmingCharacters(in: .whitespacesAndNewlines).count < q.minChars { return false }
        let lower = answer.lowercased()
        if q.failurePhrases.contains(where: { lower.contains($0) }) { return false }
        if type == "factual" {
            if answer.range(of: #"\d+"#, options: .regularExpression) == nil { return false }
            if answer.range(of: q.placeholderRegex, options: [.regularExpression, .caseInsensitive]) != nil { return false }
        }
        if q.placeholderTokens.contains(where: { answer.contains($0) }) { return false }
        if q.metaPhrases.contains(where: { lower.contains($0) }) { return false }
        return true
    }

    public func queryVariants(_ question: String) -> [String] {
        guard let v = variants else { return [question] }
        var out = [question]
        let lower = question.lowercased()
        for pair in v.expansions where pair.count == 2 && lower.contains(pair[0]) {
            if let re = try? NSRegularExpression(pattern: "\\b\(NSRegularExpression.escapedPattern(for: pair[0]))\\b", options: .caseInsensitive) {
                let expanded = re.stringByReplacingMatches(in: question, range: NSRange(question.startIndex..., in: question),
                                                           withTemplate: NSRegularExpression.escapedTemplate(for: pair[1]))
                if expanded != question { out.append(expanded); break }
            }
        }
        if v.countWords.contains(where: { lower.contains($0) }) { out.append(question + v.countSuffix) }
        if let re = try? NSRegularExpression(pattern: v.entityRegex),
           let m = re.firstMatch(in: question, range: NSRange(question.startIndex..., in: question)),
           let r = Range(m.range, in: question) {
            out.append("\(question[r]) \(question)")
        }
        return Array(out.prefix(v.max))
    }

    public func checkCorrective() -> [String] {
        var problems: [String] = []
        for c in conformance.quality ?? [] where answerLooksGood(question: c.question, answer: c.answer, type: c.type) != c.ok {
            problems.append("answer quality check for “\(c.answer.prefix(30))” differs")
        }
        for c in conformance.variants ?? [] where queryVariants(c.question) != c.variants {
            problems.append("query variants for “\(c.question.prefix(40))” differ")
        }
        return problems
    }
}
