import Foundation

/// The note editor's Markdown: what to style in the text as typed, and how a formatting command changes it.
///
/// Notes are kept as Markdown, the form the old app's BlockNote editor saved for search (`content_markdown`), so the
/// editor edits the Markdown itself and styles it in place: a heading line is set large, `**bold**` is bold with its
/// stars dimmed, list markers are tinted. Nothing is converted on the way in or out, so a note always round-trips.
/// Ranges are UTF-16 offsets (`NSRange`), what `NSTextView` and `NSString` use.
public enum MarkdownEdit {
    public enum Style: Equatable, Sendable {
        case heading(Int)       // the whole line, level 1…6
        case bold, italic, code, link
        case codeBlock          // a line inside a fence
        case quote              // a `> ` line
        case listMarker         // `- `, `1. `, `- [ ] `
        case taskDone           // a `- [x] ` line
        case syntax             // the characters of the syntax itself: `#`, `**`, fences, link targets
    }

    public struct Span: Equatable, Sendable {
        public let range: NSRange
        public let style: Style
        public init(_ range: NSRange, _ style: Style) { self.range = range; self.style = style }
    }

    public enum Action: Equatable, Sendable {
        case heading(Int)       // 0 turns a heading back into a paragraph
        case bold, italic, code
        case bullets, numbers, checklist, quote
        case link(String?)      // an address to use, or nil for a placeholder
    }

    /// One change to the text: replace `range` with `replacement`, then select `selection`.
    public struct Edit: Equatable, Sendable {
        public let range: NSRange
        public let replacement: String
        public let selection: NSRange
        public init(range: NSRange, replacement: String, selection: NSRange) { self.range = range; self.replacement = replacement; self.selection = selection }

        /// The text after the edit, for tests and for callers without a text view.
        public func apply(to text: String) -> String { (text as NSString).replacingCharacters(in: range, with: replacement) }
    }

    // MARK: Lines

    /// What a line starts with: its indent, its block marker, and the rest.
    struct Line {
        enum Marker: Equatable {
            case heading(Int), quote, bullet(Character), number(Int, Character), task(Bool, Character)
            var text: String {
                switch self {
                case .heading(let n): return String(repeating: "#", count: n) + " "
                case .quote: return "> "
                case .bullet(let c): return "\(c) "
                case .number(let n, let p): return "\(n)\(p) "
                case .task(let done, let c): return "\(c) [\(done ? "x" : " ")] "
                }
            }
        }
        let indent: String
        let marker: Marker?
        let rest: String
        /// The marker as typed (a quote may be `>` with no space), for ranges into the original text.
        let markerLength: Int

        init(_ line: String) {
            let ns = line as NSString
            let indentEnd = line.prefix { $0 == " " || $0 == "\t" }.utf16.count
            indent = ns.substring(to: indentEnd)
            let body = ns.substring(from: indentEnd)
            let rules: [(String, (NSTextCheckingResult, NSString) -> Marker)] = [
                (#"^(#{1,6}) "#, { m, s in .heading(s.substring(with: m.range(at: 1)).count) }),
                (#"^> ?"#, { _, _ in .quote }),
                (#"^([-*+]) \[([ xX])\] "#, { m, s in .task(s.substring(with: m.range(at: 2)) != " ", Character(s.substring(with: m.range(at: 1)))) }),
                (#"^([-*+]) "#, { m, s in .bullet(Character(s.substring(with: m.range(at: 1)))) }),
                (#"^(\d{1,4})([.)]) "#, { m, s in .number(Int(s.substring(with: m.range(at: 1))) ?? 1, Character(s.substring(with: m.range(at: 2)))) }),
            ]
            let bodyNS = body as NSString
            for (pattern, make) in rules {
                if let re = try? NSRegularExpression(pattern: pattern), let m = re.firstMatch(in: body, range: NSRange(location: 0, length: bodyNS.length)) {
                    marker = make(m, bodyNS)
                    markerLength = m.range.length
                    rest = bodyNS.substring(from: m.range.length)
                    return
                }
            }
            marker = nil
            markerLength = 0
            rest = body
        }

        static func make(indent: String, marker: Marker?, rest: String) -> String { indent + (marker?.text ?? "") + rest }
    }

    // MARK: Styling

    /// Everything worth styling in `text`, line by line, inline marks included. Lines inside a code fence get only
    /// `.codeBlock`; the fences themselves are `.syntax`.
    public static func spans(_ text: String) -> [Span] {
        let ns = text as NSString
        var out: [Span] = []
        var inFence = false
        var location = 0
        while location < ns.length {
            let lineRange = ns.lineRange(for: NSRange(location: location, length: 0))
            var content = lineRange
            while content.length > 0, ["\n", "\r"].contains(ns.substring(with: NSRange(location: content.location + content.length - 1, length: 1))) { content.length -= 1 }
            location = lineRange.location + max(1, lineRange.length)
            let line = ns.substring(with: content)
            if line.trimmingCharacters(in: .whitespaces).hasPrefix("```") {
                out.append(Span(content, .syntax)); inFence.toggle(); continue
            }
            if inFence { out.append(Span(content, .codeBlock)); continue }
            let parsed = Line(line)
            let markerStart = content.location + parsed.indent.utf16.count
            var inlineStart = content.location
            switch parsed.marker {
            case .heading(let n)?:
                out.append(Span(content, .heading(n)))
                out.append(Span(NSRange(location: markerStart, length: parsed.markerLength), .syntax))
                inlineStart = markerStart + parsed.markerLength
            case .quote?:
                out.append(Span(content, .quote))
                out.append(Span(NSRange(location: markerStart, length: parsed.markerLength), .syntax))
                inlineStart = markerStart + parsed.markerLength
            case .bullet?, .number?:
                out.append(Span(NSRange(location: markerStart, length: parsed.markerLength), .listMarker))
                inlineStart = markerStart + parsed.markerLength
            case .task(let done, _)?:
                if done { out.append(Span(content, .taskDone)) }
                out.append(Span(NSRange(location: markerStart, length: parsed.markerLength), .listMarker))
                inlineStart = markerStart + parsed.markerLength
            case nil: break
            }
            out.append(contentsOf: inlineSpans(ns, in: NSRange(location: inlineStart, length: content.location + content.length - inlineStart)))
        }
        return out
    }

    private static let inlineRules: [(NSRegularExpression, Style)] = {
        func re(_ p: String) -> NSRegularExpression { try! NSRegularExpression(pattern: p) }
        return [
            (re(#"`[^`\n]+`"#), .code),
            (re(#"\*\*(?=\S)(?:[^*\n]|\*(?!\*))+?(?<=\S)\*\*"#), .bold),
            (re(#"__(?=\S)[^_\n]+?(?<=\S)__"#), .bold),
            (re(#"(?<![*\w])\*(?=\S)(?:[^*\n])+?(?<=\S)\*(?![*\w])"#), .italic),
            (re(#"(?<![_\w])_(?=\S)(?:[^_\n])+?(?<=\S)_(?![_\w])"#), .italic),
            (re(#"\[([^\]\n]+)\]\(([^)\n]+)\)"#), .link),
        ]
    }()

    static func inlineSpans(_ ns: NSString, in range: NSRange) -> [Span] {
        guard range.length > 0 else { return [] }
        var out: [Span] = []
        var taken: [NSRange] = []            // code spans win over everything inside them
        for (re, style) in inlineRules {
            for m in re.matches(in: ns as String, range: range) {
                if taken.contains(where: { NSIntersectionRange($0, m.range).length > 0 }) { continue }
                switch style {
                case .code:
                    out.append(Span(m.range, .code)); taken.append(m.range)
                case .bold, .italic:
                    let n = style == .bold ? 2 : 1
                    out.append(Span(m.range, style))
                    out.append(Span(NSRange(location: m.range.location, length: n), .syntax))
                    out.append(Span(NSRange(location: m.range.location + m.range.length - n, length: n), .syntax))
                case .link:
                    out.append(Span(m.range(at: 1), .link))
                    out.append(Span(NSRange(location: m.range.location, length: 1), .syntax))
                    let afterText = m.range(at: 1).location + m.range(at: 1).length
                    out.append(Span(NSRange(location: afterText, length: m.range.location + m.range.length - afterText), .syntax))
                    taken.append(m.range)
                default: break
                }
            }
        }
        return out
    }

    // MARK: Commands

    /// The change a formatting command makes at `selection`, or nil when there is nothing to do.
    public static func edit(_ text: String, selection: NSRange, action: Action) -> Edit? {
        switch action {
        case .heading, .bullets, .numbers, .checklist, .quote: return blockEdit(text, selection: selection, action: action)
        case .bold: return wrap(text, selection: selection, marker: "**")
        case .italic: return wrap(text, selection: selection, marker: "_")
        case .code: return wrap(text, selection: selection, marker: "`")
        case .link(let address): return link(text, selection: selection, address: address)
        }
    }

    /// The lines the selection touches, without the newline that ends the last one.
    static func paragraphRange(_ ns: NSString, _ selection: NSRange) -> NSRange {
        var r = ns.lineRange(for: selection)
        while r.length > 0, ["\n", "\r"].contains(ns.substring(with: NSRange(location: r.location + r.length - 1, length: 1))) { r.length -= 1 }
        return r
    }

    static func blockEdit(_ text: String, selection: NSRange, action: Action) -> Edit? {
        let ns = text as NSString
        let range = paragraphRange(ns, selection)
        let originals = ns.substring(with: range).components(separatedBy: "\n")
        let lines = originals.map(Line.init)
        let marked = lines.filter { !$0.rest.isEmpty || $0.marker != nil }
        func allHave(_ test: (Line.Marker?) -> Bool) -> Bool { !marked.isEmpty && marked.allSatisfy { test($0.marker) } }
        var number = 1
        let rebuilt: [String] = lines.map { line in
            let keep = line.rest.isEmpty && line.marker == nil && lines.count > 1    // a blank line between items stays blank
            if keep { return line.indent + line.rest }
            let marker: Line.Marker?
            switch action {
            case .heading(let n):
                marker = (n <= 0 || allHave { $0 == .heading(n) }) ? nil : .heading(n)
            case .bullets:
                marker = allHave { if case .bullet? = $0 { return true } else { return false } } ? nil : .bullet("-")
            case .numbers:
                marker = allHave { if case .number? = $0 { return true } else { return false } } ? nil : .number(number, ".")
                number += 1
            case .checklist:
                if allHave({ if case .task? = $0 { return true } else { return false } }) { marker = nil }
                else if case .task(let done, let c)? = line.marker { marker = .task(done, c) }
                else { marker = .task(false, "-") }
            case .quote:
                marker = allHave { $0 == .quote } ? nil : .quote
            default:
                marker = line.marker
            }
            return Line.make(indent: line.indent, marker: marker, rest: line.rest)
        }
        let replacement = rebuilt.joined(separator: "\n")
        guard replacement != ns.substring(with: range) else { return nil }
        // Keep the caret on the same text: shift it by how much the first line's marker grew or shrank.
        let firstDelta = (rebuilt.first?.utf16.count ?? 0) - (originals.first?.utf16.count ?? 0)
        let newSelection: NSRange
        if selection.length == 0 {
            newSelection = NSRange(location: max(range.location, selection.location + firstDelta), length: 0)
        } else {
            newSelection = NSRange(location: range.location, length: replacement.utf16.count)
        }
        return Edit(range: range, replacement: replacement, selection: newSelection)
    }

    /// Wraps the selection in `marker` on both sides, or unwraps it when it already is (or when the markers sit just
    /// outside it). With nothing selected, inserts a pair and leaves the caret between.
    static func wrap(_ text: String, selection: NSRange, marker: String) -> Edit? {
        let ns = text as NSString
        let n = marker.utf16.count
        guard selection.location + selection.length <= ns.length else { return nil }
        if selection.length == 0 {
            // Inside an empty pair already? Then take the pair away.
            if selection.location >= n, selection.location + n <= ns.length,
               ns.substring(with: NSRange(location: selection.location - n, length: n)) == marker,
               ns.substring(with: NSRange(location: selection.location, length: n)) == marker {
                return Edit(range: NSRange(location: selection.location - n, length: 2 * n), replacement: "", selection: NSRange(location: selection.location - n, length: 0))
            }
            return Edit(range: selection, replacement: marker + marker, selection: NSRange(location: selection.location + n, length: 0))
        }
        let selected = ns.substring(with: selection)
        if selected.utf16.count >= 2 * n, selected.hasPrefix(marker), selected.hasSuffix(marker) {
            let inner = String(selected.dropFirst(marker.count).dropLast(marker.count))
            return Edit(range: selection, replacement: inner, selection: NSRange(location: selection.location, length: inner.utf16.count))
        }
        if selection.location >= n, selection.location + selection.length + n <= ns.length,
           ns.substring(with: NSRange(location: selection.location - n, length: n)) == marker,
           ns.substring(with: NSRange(location: selection.location + selection.length, length: n)) == marker {
            return Edit(range: NSRange(location: selection.location - n, length: selection.length + 2 * n), replacement: selected,
                        selection: NSRange(location: selection.location - n, length: selection.length))
        }
        // Leading or trailing spaces stay outside the markers, or the Markdown would not take.
        let lead = selected.prefix { $0 == " " }.count, trail = selected.reversed().prefix { $0 == " " }.count
        guard lead + trail < selected.count else { return nil }
        let core = String(selected.dropFirst(lead).dropLast(trail))
        let replacement = String(repeating: " ", count: lead) + marker + core + marker + String(repeating: " ", count: trail)
        return Edit(range: selection, replacement: replacement, selection: NSRange(location: selection.location + lead + n, length: core.utf16.count))
    }

    /// `[selected text](address)`; with no selection, `[link](address)` with the word "link" selected for typing over.
    /// Without an address the placeholder `https://` is selected instead, so the next keystroke replaces it.
    static func link(_ text: String, selection: NSRange, address: String?) -> Edit? {
        let ns = text as NSString
        guard selection.location + selection.length <= ns.length else { return nil }
        let label = selection.length > 0 ? ns.substring(with: selection) : "link"
        let target = address ?? "https://"
        let replacement = "[\(label)](\(target))"
        let newSelection: NSRange
        if address == nil {                                  // type the address next
            newSelection = NSRange(location: selection.location + label.utf16.count + 3, length: target.utf16.count)
        } else if selection.length == 0 {                    // type the label next
            newSelection = NSRange(location: selection.location + 1, length: label.utf16.count)
        } else {                                             // both known: carry on after the link
            newSelection = NSRange(location: selection.location + replacement.utf16.count, length: 0)
        }
        return Edit(range: selection, replacement: replacement, selection: newSelection)
    }

    /// Return pressed at `selection` inside a list: the next line gets the next marker, and Return on an empty item
    /// ends the list by taking its marker away. Nil when the line is not a list item, so the text view does the usual.
    public static func newline(_ text: String, selection: NSRange) -> Edit? {
        let ns = text as NSString
        guard selection.location <= ns.length else { return nil }
        let lineRange = ns.lineRange(for: NSRange(location: selection.location, length: 0))
        let content = paragraphRange(ns, NSRange(location: selection.location, length: 0))
        let line = Line(ns.substring(with: content))
        let next: Line.Marker
        switch line.marker {
        case .bullet(let c)?: next = .bullet(c)
        case .number(let n, let p)?: next = .number(n + 1, p)
        case .task(_, let c)?: next = .task(false, c)
        case .quote?: next = .quote
        default: return nil
        }
        let caretInLine = selection.location - lineRange.location
        if line.rest.trimmingCharacters(in: .whitespaces).isEmpty, caretInLine >= line.indent.utf16.count + line.markerLength {
            // An empty item: Return ends the list.
            return Edit(range: content, replacement: line.indent, selection: NSRange(location: content.location + line.indent.utf16.count, length: 0))
        }
        guard caretInLine >= line.indent.utf16.count + line.markerLength else { return nil }   // caret inside the marker: usual Return
        let insert = "\n" + line.indent + next.text
        return Edit(range: selection, replacement: insert, selection: NSRange(location: selection.location + insert.utf16.count, length: 0))
    }

    /// `- [ ]` ⇄ `- [x]` for the line at `location`, when it is a task.
    public static func toggleTask(_ text: String, at location: Int) -> Edit? {
        let ns = text as NSString
        guard location <= ns.length else { return nil }
        let content = paragraphRange(ns, NSRange(location: location, length: 0))
        let line = Line(ns.substring(with: content))
        guard case .task(let done, let c)? = line.marker else { return nil }
        let replacement = Line.make(indent: line.indent, marker: .task(!done, c), rest: line.rest)
        return Edit(range: content, replacement: replacement, selection: NSRange(location: location, length: 0))
    }

    /// Words in the text as a person counts them, syntax left out.
    public static func wordCount(_ text: String) -> Int {
        var count = 0
        for line in text.split(whereSeparator: \.isNewline) {
            let l = Line(String(line))
            if l.rest.trimmingCharacters(in: .whitespaces).hasPrefix("```") { continue }
            count += l.rest.split { $0.isWhitespace }.filter { word in word.contains { $0.isLetter || $0.isNumber } }.count
        }
        return count
    }
}
