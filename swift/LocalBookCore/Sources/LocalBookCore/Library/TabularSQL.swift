import Foundation

/// The old app's `tabular_query` + `tabular_store` rules, pure: which questions want a table, how a sheet becomes a
/// SQLite table (names, column types, the values a small model may filter on), the prompt that asks the answer model
/// for one SELECT, the check that it is one read-only SELECT, and the answer written from the rows. The number always
/// comes from SQL, never from the model.
public enum TabularSQL {
    /// A column as the catalog describes it to the model.
    public struct Column: Codable, Sendable, Equatable {
        public var original: String
        public var sanitized: String
        /// "number" or "text".
        public var dtype: String
        public var lowCardinality: Bool
        /// Every distinct value, when there are at most `lowCardinalityMax`.
        public var values: [String]?
        /// A few values otherwise.
        public var samples: [String]?
    }

    /// One sheet as a table.
    public struct Table: Codable, Sendable, Equatable {
        public var sourceId: String
        public var filename: String
        public var sheetName: String
        public var tableName: String
        public var columns: [Column]
        public var rowCount: Int
    }

    public static let lowCardinalityMax = 60
    public static let maxValueLength = 80
    public static let maxPromptValues = 60
    public static let maxAnswerRows = 50
    public static let maxResultRows = 2000

    // MARK: intent

    static let numeric = ["\\bhow many\\b", "\\bhow much\\b", "\\bcount\\b", "\\btotal\\b", "\\bsum\\b", "\\baverage\\b", "\\bpercentage\\b", "\\bpercent\\b",
                          "\\bnumber of\\b", "\\b\\d+\\s*(demos?|meetings?|calls?|sales?|deals?|leads?)\\b", "\\bquota\\b", "\\bmetric\\b", "\\bkpi\\b",
                          "\\brevenue\\b", "\\bpipeline\\b", "\\bforecast\\b"]
    static let comparison = ["\\bcompare\\b", "\\bversus\\b", "\\bvs\\.?\\b", "\\bdifference between\\b", "\\bmore than\\b", "\\bless than\\b", "\\bhigher\\b",
                             "\\blower\\b", "\\bbetter\\b", "\\bworse\\b", "\\bq[1-4]\\s+(?:vs|versus|compared to|and)\\s+q[1-4]\\b"]
    static let lookup = ["\\bwhat is\\b", "\\bwhat was\\b", "\\bwhen did\\b", "\\bwho did\\b", "\\bwho has\\b", "\\bfind\\b", "\\blook up\\b", "\\bget\\b.*\\bfor\\b"]
    static let explanation = ["\\bwhy\\b", "\\bhow does\\b", "\\bexplain\\b", "\\bdescribe\\b", "\\bwhat does.*mean\\b", "\\bunderstand\\b", "\\breason\\b", "\\bcause\\b",
                              "\\banalysis\\b", "\\binsight\\b"]
    static let summary = ["\\bsummar\\w+\\b", "\\boverview\\b", "\\bhighlights?\\b", "\\bkey points?\\b", "\\bmain\\b.*\\b(idea|point|topic)\\b", "\\btell me about\\b",
                          "\\bwhat.*about\\b"]
    static let list = ["\\blist\\b", "\\benumerate\\b", "\\bname\\s+(all|the)\\b", "\\bwhat are (all |the )?\\w+s\\b", "\\bgive me\\b.*\\b(all|list)\\b"]

    static func matches(_ q: String, _ patterns: [String]) -> Int {
        patterns.filter { q.range(of: $0, options: .regularExpression) != nil }.count
    }

    /// `source_router.structured_intent`: a numeric, comparison or list question goes to SQL; lookups, explanations
    /// and summaries (and ties, "mixed") stay with the passages.
    public static func wantsStructured(_ question: String) -> Bool {
        let q = question.lowercased()
        let scores: [(String, Int)] = [("numeric", matches(q, numeric)), ("comparison", matches(q, comparison)), ("lookup", matches(q, lookup)),
                                       ("explanation", matches(q, explanation)), ("summary", matches(q, summary)), ("list", matches(q, list))]
        let top = scores.map(\.1).max() ?? 0
        guard top > 0 else { return false }
        let best = scores.filter { $0.1 == top }
        guard best.count == 1 else { return false }
        return ["numeric", "comparison", "list"].contains(best[0].0)
    }

    // MARK: names and types

    /// `_sanitize_ident`: snake_case, SQL-safe, never empty, never starting with a digit.
    public static func sanitize(_ name: String, fallback: String) -> String {
        var s = name.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        s = s.replacingOccurrences(of: "[^0-9a-z]+", with: "_", options: .regularExpression)
        s = s.trimmingCharacters(in: CharacterSet(charactersIn: "_"))
        if s.isEmpty { s = fallback }
        if let first = s.first, first.isNumber { s = "c_" + s }
        return s
    }

    public static func tableName(sourceId: String, sheetIndex: Int) -> String {
        "tab_\(sanitize(sourceId, fallback: "src"))_\(sheetIndex)"
    }

    /// `_unique_columns`: sanitized names in order, collisions numbered `_2`, `_3`…
    public static func uniqueColumns(_ names: [String]) -> [String] {
        var seen: Set<String> = [], out: [String] = []
        for (i, name) in names.enumerated() {
            let base = sanitize(name, fallback: "col_\(i + 1)")
            var candidate = base, n = 1
            while seen.contains(candidate) { n += 1; candidate = "\(base)_\(n)" }
            seen.insert(candidate)
            out.append(candidate)
        }
        return out
    }

    /// `_column_metadata` over a string table: a column is a number when pandas would have read it as one.
    public static func columns(for table: TextTable) -> [Column] {
        let names = uniqueColumns(table.columns)
        return table.columns.indices.map { i in
            let cells = table.rows.map { $0[i] }
            let kind = TabularText.kind(of: cells)
            let numeric = kind == .integer || kind == .float
            let present = cells.map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !TabularText.missing.contains($0) }
            var c = Column(original: table.columns[i], sanitized: names[i], dtype: numeric ? "number" : "text", lowCardinality: false, values: nil, samples: nil)
            if numeric {
                c.samples = present.prefix(3).map { TabularText.render($0, as: kind) }
            } else {
                let distinct = Array(Set(present.map { String($0.prefix(maxValueLength)) }.filter { !$0.isEmpty })).sorted()
                if distinct.count <= lowCardinalityMax { c.lowCardinality = true; c.values = distinct } else { c.samples = Array(distinct.prefix(5)) }
            }
            return c
        }
    }

    /// The SQLite value for a cell of a column: NULL for a missing cell, a REAL for a number column.
    public static func sqlValue(_ cell: String, numeric: Bool) -> String? {
        let t = cell.trimmingCharacters(in: .whitespacesAndNewlines)
        if TabularText.missing.contains(t) { return nil }
        return numeric ? (Double(t).map { "\($0)" } ?? t) : cell
    }

    // MARK: the model's SQL

    static let forbidden = "\\b(insert|update|delete|drop|alter|create|attach|detach|pragma|replace|truncate|vacuum|reindex|grant|revoke|commit|rollback|begin)\\b"

    /// `_extract_sql`: the statement out of fences and prose, from the first SELECT or WITH, before any `;`.
    public static func extractSQL(_ text: String) -> String {
        var s = text
        if let r = s.range(of: "```(?:sql)?\\s*(.*?)```", options: [.regularExpression, .caseInsensitive]) {
            var inner = String(s[r])
            inner = inner.replacingOccurrences(of: "^```(?:sql)?\\s*", with: "", options: [.regularExpression, .caseInsensitive])
            inner = inner.replacingOccurrences(of: "```$", with: "", options: .regularExpression)
            s = inner
        }
        s = s.trimmingCharacters(in: .whitespacesAndNewlines)
        if let r = s.range(of: "\\b(select|with)\\b", options: [.regularExpression, .caseInsensitive]) { s = String(s[r.lowerBound...]) }
        return (s.components(separatedBy: ";").first ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// `safe_sql`: one read-only SELECT, or why not.
    public static func safeSQL(_ raw: String) -> Result<String, Problem> {
        let sql = extractSQL(raw)
        if sql.isEmpty { return .failure(.noSQL) }
        if sql.range(of: "^\\s*(select|with)\\b", options: [.regularExpression, .caseInsensitive]) == nil { return .failure(.notASelect) }
        if sql.range(of: forbidden, options: [.regularExpression, .caseInsensitive]) != nil { return .failure(.forbidden) }
        if sql.contains(";") { return .failure(.multiple) }
        return .success(sql)
    }

    public enum Problem: Error, Equatable, CustomStringConvertible {
        case noSQL, notASelect, forbidden, multiple
        public var description: String {
            switch self {
            case .noSQL: "no SQL produced"
            case .notASelect: "not a SELECT"
            case .forbidden: "contains a forbidden (non-read-only) keyword"
            case .multiple: "multiple statements"
            }
        }
    }

    /// `_build_prompt`, less the value directives (state names), which the Mac app had for its sales notebooks.
    public static func prompt(question: String, schema: [Table]) -> String {
        var lines: [String] = []
        for t in schema {
            lines.append("Table \"\(t.tableName)\" (from file \"\(t.filename)\", sheet \"\(t.sheetName)\", \(t.rowCount) rows):")
            for c in t.columns {
                var desc = "  - \(c.sanitized) (\(c.dtype))"
                if c.lowCardinality, let values = c.values, !values.isEmpty {
                    desc += " — one of: " + values.prefix(maxPromptValues).joined(separator: ", ") + (values.count > maxPromptValues ? ", …" : "")
                } else if let samples = c.samples, !samples.isEmpty {
                    desc += " — e.g. " + samples.joined(separator: ", ")
                }
                lines.append(desc)
            }
            lines.append("")
        }
        return """
        Translate the user's question into ONE read-only SQLite SELECT over the tables below.

        \(lines.joined(separator: "\n"))
        Rules:
        - Output ONLY the SQL. No explanation, no markdown fences.
        - Use exactly the table and column names shown (snake_case).
        - CRITICAL: filter using ONLY the EXACT values listed for a column. Do not abbreviate, expand, reword, or guess a value — copy the listed spelling verbatim (e.g. if the column lists 'Texas', write state = 'Texas', never 'TX').
        - Add a WHERE condition ONLY for a constraint the question explicitly states. NEVER invent an extra filter on a column the question does not mention — do NOT add clauses like account = '...', person IN (...), etc. unless the question asks for them.
        - For case-insensitive matching on OTHER text columns, compare LOWER(col) = LOWER('value').
        - "how many" / "number of" -> SELECT COUNT(*). Totals -> SUM(...). Averages -> AVG(...).
        - A single read-only SELECT only. Never modify data.

        Question: \(question)
        SQL:
        """
    }

    // MARK: the answer

    static func pretty(_ name: String) -> String {
        name.replacingOccurrences(of: "_", with: " ").trimmingCharacters(in: .whitespaces).capitalized
    }

    /// A number as the old app printed Python values: whole numbers without `.0`.
    public static func cell(_ v: String?) -> String {
        guard let v else { return "" }
        if let d = Double(v), d == d.rounded(), abs(d) < 1e15, v.contains(".") { return String(Int64(d)) }
        return v
    }

    /// `_render_answer`: a bold scalar, one row as `Name: **value**` pairs, or a Markdown table cut at 50 rows.
    public static func render(columns: [String], rows: [[String?]]) -> String {
        guard !rows.isEmpty else { return "No matching rows found." }
        if rows.count == 1, columns.count == 1 { return "**\(cell(rows[0][0]))**" }
        if rows.count == 1 { return zip(columns, rows[0]).map { "\(pretty($0)): **\(cell($1))**" }.joined(separator: ", ") }
        let shown = rows.prefix(maxAnswerRows)
        var out = ["| " + columns.map(pretty).joined(separator: " | ") + " |", "| " + columns.map { _ in "---" }.joined(separator: " | ") + " |"]
        for r in shown { out.append("| " + r.map(cell).joined(separator: " | ") + " |") }
        var text = out.joined(separator: "\n")
        if rows.count > maxAnswerRows { text += "\n\n_…and \(rows.count - maxAnswerRows) more rows._" }
        return text
    }
}
