import Foundation

/// An answer as it should be SPOKEN: no markdown, no citation brackets, in sentence groups the voice can take.
public enum SpeechText {
    public static func chunks(from answer: String, maxCharacters: Int = 220) -> [String] {
        var lines: [String] = []
        for raw in answer.split(separator: "\n", omittingEmptySubsequences: true) {
            var t = String(raw)
            t = t.replacingOccurrences(of: #"\[\d+(?:\s*,\s*\d+)*\]"#, with: "", options: .regularExpression)     // [1], [2, 3]
            t = t.replacingOccurrences(of: #"^\s*#{1,6}\s*"#, with: "", options: .regularExpression)             // headings
            t = t.replacingOccurrences(of: #"^\s*([\*\-•]|\d+\.)\s+"#, with: "", options: .regularExpression)     // bullets
            t = t.replacingOccurrences(of: #"[\*_`]+"#, with: "", options: .regularExpression)                    // emphasis
            t = t.replacingOccurrences(of: " & ", with: " and ")
            t = t.replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
            t = t.replacingOccurrences(of: #"\s+([.,;:!?])"#, with: "$1", options: .regularExpression).trimmingCharacters(in: .whitespaces)
            if t.count > 1 { lines.append(t.last.map { ".!?:".contains($0) } == true ? t : t + ".") }
        }
        // sentences, then group up to maxCharacters
        var sentences: [String] = []
        for line in lines {
            line.enumerateSubstrings(in: line.startIndex..., options: .bySentences) { s, _, _, _ in
                if let s { sentences.append(s.trimmingCharacters(in: .whitespaces)) }
            }
        }
        var out: [String] = []
        var group = ""
        for s in sentences where !s.isEmpty {
            if !group.isEmpty && group.count + s.count + 1 > maxCharacters { out.append(group); group = s }
            else { group = group.isEmpty ? s : group + " " + s }
        }
        if !group.isEmpty { out.append(group) }
        return out
    }
}
