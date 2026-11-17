import Foundation

/// A sheet, CSV or slide table as the old app saw it through pandas: a header row and string cells.
public struct TextTable: Sendable, Equatable {
    public var columns: [String]
    public var rows: [[String]]
    public init(columns: [String], rows: [[String]]) { self.columns = columns; self.rows = rows }

    /// pandas' reading of a header row: an empty name becomes `Unnamed: N`, a repeated name gets `.1`, `.2`…
    public static func pandasColumns(_ header: [String]) -> [String] {
        var seen: [String: Int] = [:]
        var out: [String] = []
        for (i, raw) in header.enumerated() {
            var name = raw.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "Unnamed: \(i)" : raw
            if let n = seen[name] { seen[name] = n + 1; name = "\(name).\(n)" } else { seen[name] = 1 }
            out.append(name)
        }
        return out
    }

    /// From raw rows (the first is the header), padded to the widest row as pandas pads with NaN.
    public static func fromRows(_ raw: [[String]]) -> TextTable {
        guard let first = raw.first else { return TextTable(columns: [], rows: []) }
        let width = raw.map(\.count).max() ?? 0
        var header = first
        if header.count < width { header += Array(repeating: "", count: width - header.count) }
        let rows = raw.dropFirst().map { r in r.count < width ? r + Array(repeating: "", count: width - r.count) : r }
        return TextTable(columns: pandasColumns(header), rows: Array(rows))
    }
}

/// The old app's `DocumentProcessor._dataframe_to_sentences`, from a string table instead of a pandas frame, with the
/// frame's reading of the cells reproduced: a column whose values are all whole numbers prints them as integers, one
/// with a blank or a decimal prints every value as a float (`7.0`), anything else prints the text as it came. The golden
/// fixture (`tabular_golden.json`, written by the Python function) is the proof.
public enum TabularText {
    /// Strings pandas reads as missing.
    static let missing: Set<String> = ["", "#N/A", "#N/A N/A", "#NA", "-1.#IND", "-1.#QNAN", "-NaN", "-nan", "1.#IND", "1.#QNAN", "<NA>", "N/A", "NA", "NULL", "NaN", "None", "n/a", "nan", "null"]

    /// `typed` is a column pandas held as bool or datetime (an xls date cell, a True/False cell): neither a label nor a
    /// value in the sentences, printed as it came in the rows.
    enum Kind { case integer, float, text, typed }

    /// pandas' dtype for a column of CSV cells: int64 when every cell is a whole number, float64 when every present cell
    /// is a number and at least one is missing or has a fraction, object otherwise.
    static func kind(of cells: [String]) -> Kind {
        var anyMissing = false, anyFraction = false, anyNumber = false, anyTyped = false
        for c in cells {
            let t = c.trimmingCharacters(in: .whitespaces)
            if missing.contains(t) { anyMissing = true; continue }
            if Int64(t) != nil { anyNumber = true; continue }
            if let d = Double(t), d.isFinite { anyNumber = true; anyFraction = true; continue }
            if t == "True" || t == "False" || isDateTime(t) { anyTyped = true; continue }
            return .text
        }
        if anyTyped { return anyNumber ? .text : .typed }                 // numbers mixed with bools or dates: object
        return (anyMissing || anyFraction) ? .float : .integer
    }

    /// The shape `XLSReader` gives a date cell, which pandas read as a Timestamp.
    static func isDateTime(_ t: String) -> Bool {
        t.range(of: "^\\d{4}-\\d{2}-\\d{2} \\d{2}:\\d{2}:\\d{2}$", options: .regularExpression) != nil
    }

    /// `str(cell)` as pandas would have held it.
    static func render(_ cell: String, as kind: Kind) -> String {
        let t = cell.trimmingCharacters(in: .whitespaces)
        if missing.contains(t) { return "" }
        switch kind {
        case .integer: return Int64(t).map { String($0) } ?? t
        case .float: return Double(t).map { "\($0)" } ?? t
        case .text, .typed: return cell
        }
    }

    /// Numeric when the frame's column would be int64 or float64 *after* `fillna('')`: a column with a missing cell
    /// became object there, so only a complete numeric column counts.
    static func numericDtype(_ cells: [String], kind: Kind) -> Bool {
        (kind == .integer || kind == .float) && !cells.contains { missing.contains($0.trimmingCharacters(in: .whitespaces)) }
    }

    static func looksLikeValue(_ name: String) -> Bool {
        name.range(of: "(Q\\s*\\d|FY\\s*\\d|Total|Count|Sum|Amount)", options: [.regularExpression, .caseInsensitive]) != nil
    }

    static func digitsOnly(_ s: String) -> Bool {
        let stripped = s.replacingOccurrences(of: ".", with: "").replacingOccurrences(of: "-", with: "")
        return !stripped.isEmpty && stripped.unicodeScalars.allSatisfy { CharacterSet.decimalDigits.contains($0) }
    }

    public static func sentences(_ table: TextTable, sheet: String) -> String {
        let cols = table.columns
        let kinds = cols.indices.map { i in kind(of: table.rows.map { $0[i] }) }
        let cells: [[String]] = table.rows.map { row in row.indices.map { render(row[$0], as: kinds[$0]) } }
        var out = ["Data from sheet '\(sheet)':"]

        let clean = cols.enumerated().map { i, c -> String in
            let n = c.trimmingCharacters(in: .whitespacesAndNewlines)
            return (n.isEmpty || n.contains("Unnamed")) ? "Column\(i + 1)" : n
        }

        if cols.contains(where: looksLikeValue) {
            var labelCols: [Int] = [], valueCols: [Int] = []
            for (i, c) in cols.enumerated() {
                if looksLikeValue(c.trimmingCharacters(in: .whitespacesAndNewlines)) { valueCols.append(i); continue }
                let column = cells.map { $0[i] }
                guard !column.isEmpty else { continue }
                let textCount = column.prefix(10).filter { kinds[i] == .text && !$0.trimmingCharacters(in: .whitespaces).isEmpty && !digitsOnly($0) }.count
                if textCount > 0 { labelCols.append(i) }
                else if numericDtype(table.rows.map { $0[i] }, kind: kinds[i]) { valueCols.append(i) }
            }
            var last = Array(repeating: "", count: labelCols.count)
            for row in cells {
                var labels: [String] = []
                for (j, ci) in labelCols.enumerated() {
                    let v = row[ci].trimmingCharacters(in: .whitespaces)
                    if !v.isEmpty && !["0", "0.0", "nan", "None"].contains(v) { last[j] = v }
                    if !last[j].isEmpty { labels.append(last[j]) }
                }
                if labels.isEmpty { continue }
                let subject = labels.joined(separator: " - ")
                for ci in valueCols {
                    let v = row[ci]
                    guard !v.isEmpty, let num = Double(v.trimmingCharacters(in: .whitespaces)), num != 0 else { continue }
                    let shown = num == num.rounded() && abs(num) < 1e18 ? String(Int64(num)) : "\(num)"
                    out.append("\(subject): \(clean[ci]) = \(shown)")
                }
            }
        }

        out.append("")
        out.append("Complete row data:")
        for (r, row) in cells.enumerated() {
            var parts: [String] = []
            for (ci, v) in row.enumerated() {
                let s = v.trimmingCharacters(in: .whitespacesAndNewlines)
                if !s.isEmpty && s != "nan" && s != "None" { parts.append("\(clean[ci]): \(s)") }
            }
            if !parts.isEmpty { out.append("Row \(r + 1): " + parts.joined(separator: ", ")) }
        }
        return out.joined(separator: "\n")
    }

    /// `_extract_from_excel`'s shape: each sheet under a marker, then its sentences, then a blank gap.
    public static func workbookText(_ sheets: [(name: String, table: TextTable)]) -> String {
        var parts: [String] = []
        for (name, table) in sheets {
            parts.append("=== Sheet: \(name) ===\n")
            parts.append(sentences(table, sheet: name))
            parts.append("\n\n")
        }
        return parts.joined(separator: "\n")
    }
}

/// A CSV reader with the rules pandas applies by default: comma separated, double quotes enclose a field and double
/// up inside it, a quoted field may span lines, blank lines are skipped.
public enum CSV {
    public static func parse(_ text: String) -> [[String]] {
        var rows: [[String]] = []
        var row: [String] = []
        var field = ""
        var quoted = false
        var i = text.startIndex
        var afterQuote = false
        while i < text.endIndex {
            let c = text[i]
            if quoted {
                if c == "\"" {
                    let next = text.index(after: i)
                    if next < text.endIndex, text[next] == "\"" { field.append("\""); i = next }
                    else { quoted = false; afterQuote = true }
                } else { field.append(c) }
            } else if c == "\"" && field.isEmpty && !afterQuote {
                quoted = true
            } else if c == "," {
                row.append(field); field = ""; afterQuote = false
            } else if c == "\n" || c == "\r\n" || c == "\r" {
                row.append(field); field = ""; afterQuote = false
                if !(row.count == 1 && row[0].isEmpty) { rows.append(row) }
                row = []
            } else { field.append(c) }
            i = text.index(after: i)
        }
        if !field.isEmpty || !row.isEmpty || quoted { row.append(field); if !(row.count == 1 && row[0].isEmpty) { rows.append(row) } }
        return rows
    }

    public static func table(_ text: String) -> TextTable { TextTable.fromRows(parse(text)) }
}
