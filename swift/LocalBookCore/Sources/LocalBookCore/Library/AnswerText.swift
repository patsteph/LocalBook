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
            var text = String(line)
            var heading = false
            text = text.replacingOccurrences(of: #"\[(\d{1,2})\]"#, with: "[\\[$1\\]](\(citationScheme)://$1)", options: .regularExpression)
            if let r = text.range(of: #"^#{1,4}\s+"#, options: .regularExpression) { text.removeSubrange(r); heading = true }
            if let r = text.range(of: #"^(\s*)[\*\-]\s+"#, options: .regularExpression) {
                let indent = text[r].prefix { $0 == " " }
                text.replaceSubrange(r, with: indent + "•  ")
            }
            var piece = (try? AttributedString(markdown: text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(text)
            if heading { piece.font = .headline }
            if i > 0 { out.append(AttributedString("\n")) }
            out.append(piece)
        }
        return out
    }

    /// The citation number a tapped link names, if the link is one of ours and within range.
    public static func citation(in url: URL, count: Int) -> Int? {
        guard url.scheme == citationScheme, let n = Int(url.host ?? ""), n >= 1, n <= count else { return nil }
        return n
    }
}
