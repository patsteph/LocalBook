import Foundation

/// A table's rows as the document reader transcribed them, checked against the page's own text layer. Vision reads a
/// ruled table's geometry well but drops a short cell now and then (a lone digit), and the text layer of a born-digital
/// PDF has every character but no cells. Each transcribed row is matched to the text-layer line that holds its words in
/// order; the words the line has between two matched cells fill an empty cell, and the line is marked as the table's
/// so that it is not read twice. A row with no matching line keeps the transcript.
public enum TableRows {
    public struct Repaired: Sendable, Equatable {
        public var rows: [[String]]
        /// The text-layer lines (whitespace folded) the rows came from.
        public var covered: Set<String>
    }

    public static func repair(_ rows: [[String]], lines: [String]) -> Repaired {
        var used: Set<Int> = []
        var out: [[String]] = []
        var covered: Set<String> = []
        for row in rows {
            var fixed: [String]?
            for (i, line) in lines.enumerated() where !used.contains(i) {
                if let cells = align(row, to: line) {
                    fixed = cells; used.insert(i); covered.insert(fold(line)); break
                }
            }
            out.append(fixed ?? row)
        }
        return Repaired(rows: out, covered: covered)
    }

    /// `row`'s cells placed on `line`: every non-empty cell appears, in order, as whole words; an empty cell takes the
    /// words between its neighbours. Nil when the line does not hold the row.
    static func align(_ row: [String], to line: String) -> [String]? {
        let words = fold(line).split(separator: " ").map(String.init)
        guard !words.isEmpty else { return nil }
        let cells = row.map { fold($0).split(separator: " ").map(String.init) }
        guard cells.contains(where: { !$0.isEmpty }) else { return nil }
        // Where each non-empty cell starts and ends in `words`, found left to right.
        var spans: [Range<Int>?] = Array(repeating: nil, count: cells.count)
        var cursor = 0
        for (c, cell) in cells.enumerated() where !cell.isEmpty {
            guard let start = find(cell, in: words, from: cursor) else { return nil }
            spans[c] = start..<(start + cell.count)
            cursor = start + cell.count
        }
        // Each empty cell takes the words between the previous and the next placed cell; the first empty cell in a
        // run takes them all, the rest stay empty (one digit between two cells is the common case).
        var result: [String] = []
        var i = 0
        while i < cells.count {
            if let span = spans[i] { result.append(words[span].joined(separator: " ")); i += 1; continue }
            let before = (0..<i).compactMap { spans[$0] }.last?.upperBound ?? 0
            var j = i
            while j < cells.count, spans[j] == nil { j += 1 }
            let after = j < cells.count ? spans[j]!.lowerBound : words.count
            let gap = before < after ? words[before..<after].joined(separator: " ") : ""
            result.append(gap)
            for _ in (i + 1)..<j { result.append("") }
            i = j
        }
        // The line is this row only when the words outside the cells are nothing but what filled the empty cells.
        let first = spans.compactMap { $0 }.first?.lowerBound ?? 0
        let last = spans.compactMap { $0 }.last?.upperBound ?? words.count
        let leadingUnplaced = first > 0 && spans[0] != nil
        let trailingUnplaced = last < words.count && spans[cells.count - 1] != nil
        if leadingUnplaced || trailingUnplaced { return nil }
        return result
    }

    static func find(_ needle: [String], in words: [String], from: Int) -> Int? {
        guard !needle.isEmpty, words.count - from >= needle.count else { return nil }
        for start in from...(words.count - needle.count) where Array(words[start..<(start + needle.count)]) == needle {
            return start
        }
        return nil
    }

    /// Whitespace folded to single spaces, trimmed.
    public static func fold(_ s: String) -> String {
        s.split(whereSeparator: { $0.isWhitespace || $0.isNewline }).joined(separator: " ")
    }
}
