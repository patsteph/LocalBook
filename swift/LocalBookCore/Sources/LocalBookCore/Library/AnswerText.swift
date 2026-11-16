import Foundation
import SwiftUI

/// The answer model writes light markdown (headings, bullets, bold) with `[n]` citations. Show it readably without a
/// full renderer, on the phone and the Mac alike: each `[n]` becomes a link `lb-source://n` the view turns into a
/// citation anchor. Moved here from the iOS app in Phase 2 so both apps render an answer the same way.
public enum AnswerText {
    public static let citationScheme = "lb-source"

    public static func render(_ raw: String) -> AttributedString {
        var out = AttributedString()
        for (i, line) in raw.split(separator: "\n", omittingEmptySubsequences: false).enumerated() {
            let original = String(line)
            var text = linkCitations(original)
            var heading = false
            if let r = text.range(of: #"^#{1,4}\s+"#, options: .regularExpression) { text.removeSubrange(r); heading = true }
            if let r = text.range(of: #"^(\s*)[\*\-]\s+"#, options: .regularExpression) {
                let indent = text[r].prefix { $0 == " " }
                text.replaceSubrange(r, with: indent + "•  ")
            }
            var piece = (try? AttributedString(markdown: text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(original)
            if heading { piece.font = .headline }
            if i > 0 { out.append(AttributedString("\n")) }
            out.append(piece)
        }
        return out
    }

    /// `[3]`, `[1, 3]`, `[2; 5]`: each number becomes a markdown link to `lb-source://n` and the brackets stay as
    /// typed, so every citation the model writes looks and works the same. Numbers over 99 are not citations.
    static func linkCitations(_ text: String) -> String {
        guard let re = try? NSRegularExpression(pattern: #"\[(\d{1,2}(?:\s*[,;]\s*\d{1,2})*)\]"#) else { return text }
        let ns = text as NSString
        var out = "", last = 0
        for m in re.matches(in: text, range: NSRange(location: 0, length: ns.length)) {
            out += ns.substring(with: NSRange(location: last, length: m.range.location - last))
            last = m.range.location + m.range.length
            let inner = ns.substring(with: m.range(at: 1))
            let linked = inner.replacingOccurrences(of: #"\d{1,2}"#, with: "[$0](\(citationScheme)://$0)", options: .regularExpression)
            out += "\\[" + linked + "\\]"
        }
        return out + ns.substring(from: last)
    }

    /// The citation number a tapped link names, if the link is one of ours and within range.
    public static func citation(in url: URL, count: Int) -> Int? {
        guard url.scheme == citationScheme, let n = Int(url.host ?? ""), n >= 1, n <= count else { return nil }
        return n
    }
}
