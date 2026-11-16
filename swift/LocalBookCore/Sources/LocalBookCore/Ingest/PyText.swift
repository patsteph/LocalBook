import Foundation

/// String operations with Python's semantics, so the chunker can be a line-for-line port of the old app's
/// `services/rag_chunking.py`, `services/hierarchical_chunker.py` and `services/token_budget.py`. Python measures and
/// slices text in code points; Swift's `String.count` counts grapheme clusters, so every length here is in Unicode
/// scalars. The golden fixture (`chunk_golden.json`) is what proves the port.
enum PyText {
    /// `len(s)`
    static func len(_ s: String) -> Int { s.unicodeScalars.count }

    /// `s[:n]`
    static func prefix(_ s: String, _ n: Int) -> String {
        guard n < len(s) else { return s }
        var v = String.UnicodeScalarView(); v.append(contentsOf: s.unicodeScalars.prefix(max(0, n))); return String(v)
    }

    /// `s[-n:]`
    static func suffix(_ s: String, _ n: Int) -> String {
        guard n < len(s) else { return s }
        var v = String.UnicodeScalarView(); v.append(contentsOf: s.unicodeScalars.suffix(max(0, n))); return String(v)
    }

    /// `s[a:b]`
    static func slice(_ s: String, _ a: Int, _ b: Int) -> String {
        let scalars = Array(s.unicodeScalars)
        let lo = min(max(0, a), scalars.count), hi = min(max(lo, b), scalars.count)
        var v = String.UnicodeScalarView(); v.append(contentsOf: scalars[lo..<hi]); return String(v)
    }

    /// `s.strip()`
    static func strip(_ s: String) -> String { s.trimmingCharacters(in: .whitespacesAndNewlines) }

    /// `s.split(sep)` (a literal separator, empty parts kept)
    static func split(_ s: String, _ sep: String) -> [String] { s.components(separatedBy: sep) }

    /// `s.split()` (runs of whitespace, no empty parts)
    static func splitWhitespace(_ s: String) -> [String] {
        s.unicodeScalars.split(whereSeparator: { CharacterSet.whitespacesAndNewlines.contains($0) }).map { String(String.UnicodeScalarView($0)) }
    }

    /// `re.split(pattern, s)` (no capture groups in the pattern)
    static func reSplit(_ pattern: String, _ s: String) -> [String] {
        guard let re = try? NSRegularExpression(pattern: pattern) else { return [s] }
        let ns = s as NSString
        var out: [String] = []
        var last = 0
        for m in re.matches(in: s, range: NSRange(location: 0, length: ns.length)) {
            if m.range.length == 0 { continue }
            out.append(ns.substring(with: NSRange(location: last, length: m.range.location - last)))
            last = m.range.location + m.range.length
        }
        out.append(ns.substring(from: last))
        return out
    }

    /// `re.match(pattern, s)`: the groups when the pattern matches at the start, nil otherwise. Index 0 is the whole
    /// match; a group that did not take part is nil.
    static func match(_ pattern: String, _ s: String) -> [String?]? {
        guard let re = try? NSRegularExpression(pattern: pattern, options: [.anchorsMatchLines]) else { return nil }
        let ns = s as NSString
        guard let m = re.firstMatch(in: s, options: [.anchored], range: NSRange(location: 0, length: ns.length)) else { return nil }
        return (0..<m.numberOfRanges).map { i in
            let r = m.range(at: i)
            return r.location == NSNotFound ? nil : ns.substring(with: r)
        }
    }

    /// `needle in s`
    static func contains(_ s: String, _ needle: String) -> Bool { s.range(of: needle) != nil }
}
