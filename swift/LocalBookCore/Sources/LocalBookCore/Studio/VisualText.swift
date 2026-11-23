import CoreText
import Foundation
#if canImport(UIKit)
import UIKit
#elseif canImport(AppKit)
import AppKit
#endif

/// Measures and wraps words with the font the picture is drawn in, so a card is as tall as its words and a line
/// never runs past its box. Core Text on both platforms; no view is needed, so the layout runs anywhere.
public enum VisualText {
    static func font(_ style: VisualTextStyle) -> CTFont {
        #if canImport(UIKit)
        let weight: UIFont.Weight = switch style.weight { case .regular: .regular; case .semibold: .semibold; case .bold: .bold; case .heavy: .heavy }
        let base = UIFont.systemFont(ofSize: style.size, weight: weight)
        let design: UIFontDescriptor.SystemDesign? = style.rounded ? .rounded : (style.design == .serif ? .serif : (style.design == .rounded ? .rounded : nil))
        if let design, let d = base.fontDescriptor.withDesign(design) { return UIFont(descriptor: d, size: style.size) as CTFont }
        return base as CTFont
        #else
        let weight: NSFont.Weight = switch style.weight { case .regular: .regular; case .semibold: .semibold; case .bold: .bold; case .heavy: .heavy }
        let base = NSFont.systemFont(ofSize: style.size, weight: weight)
        let design: NSFontDescriptor.SystemDesign? = style.rounded ? .rounded : (style.design == .serif ? .serif : (style.design == .rounded ? .rounded : nil))
        if let design, let d = base.fontDescriptor.withDesign(design), let f = NSFont(descriptor: d, size: style.size) { return f as CTFont }
        return base as CTFont
        #endif
    }

    private static func attributed(_ text: String, _ font: CTFont) -> NSAttributedString {
        NSAttributedString(string: text, attributes: [NSAttributedString.Key(kCTFontAttributeName as String): font])
    }

    /// The width of one line of words.
    public static func width(of text: String, style: VisualTextStyle) -> CGFloat {
        let line = CTLineCreateWithAttributedString(attributed(text, font(style)))
        return CGFloat(CTLineGetTypographicBounds(line, nil, nil, nil))
    }

    /// `text` broken into lines no wider than `width`, with the line height and ascent of the font. A paragraph
    /// break in the text starts a new line; blank lines are dropped.
    public static func wrap(_ text: String, style: VisualTextStyle, width: CGFloat) -> (lines: [String], lineHeight: CGFloat, ascent: CGFloat) {
        let f = font(style)
        let ascent = CTFontGetAscent(f)
        let lineHeight = ((ascent + CTFontGetDescent(f) + CTFontGetLeading(f)) * 1.18).rounded()
        var lines: [String] = []
        for paragraph in text.components(separatedBy: .newlines) {
            let p = paragraph.trimmingCharacters(in: .whitespaces)
            guard !p.isEmpty else { continue }
            let ns = p as NSString
            let typesetter = CTTypesetterCreateWithAttributedString(attributed(p, f))
            var start = 0
            while start < ns.length {
                var count = CTTypesetterSuggestLineBreak(typesetter, start, Double(max(1, width)))
                if count <= 0 { count = 1 }
                let line = ns.substring(with: NSRange(location: start, length: min(count, ns.length - start)))
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                if !line.isEmpty { lines.append(line) }
                start += count
            }
        }
        return (lines, lineHeight, ascent)
    }
}
