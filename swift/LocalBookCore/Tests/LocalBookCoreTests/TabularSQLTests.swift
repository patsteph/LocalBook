import Foundation
import Testing
@testable import LocalBookCore

/// Spreadsheet questions: which ones go to SQL, how a sheet becomes a table, what the model may run, and the answer.
@Suite("Tabular questions")
struct TabularSQLTests {
    static let sheet = TextTable(columns: ["Account", "State", "ARR", "Renewal Date"],
                                 rows: [["Acme", "Texas", "1200", "2024-03-05 00:00:00"], ["Beta", "Ohio", "800.5", ""], ["Gamma", "Texas", "", "2025-01-01 00:00:00"]])

    @Test("aggregate, comparison and list questions want the table; lookups and explanations do not")
    func intent() {
        #expect(TabularSQL.wantsStructured("How many accounts are in Texas?"))
        #expect(TabularSQL.wantsStructured("Total ARR by state"))
        #expect(TabularSQL.wantsStructured("Compare Texas versus Ohio"))
        #expect(TabularSQL.wantsStructured("List the accounts renewing in 2025"))
        #expect(!TabularSQL.wantsStructured("What is Acme?"))
        #expect(!TabularSQL.wantsStructured("Why did Beta churn?"))
        #expect(!TabularSQL.wantsStructured("Tell me about the renewals"))
    }

    @Test("names are snake_case and unique, the table is named after the source")
    func names() {
        #expect(TabularSQL.sanitize("Renewal Date", fallback: "x") == "renewal_date")
        #expect(TabularSQL.sanitize("2024 Q1", fallback: "x") == "c_2024_q1")
        #expect(TabularSQL.sanitize("***", fallback: "col_3") == "col_3")
        #expect(TabularSQL.uniqueColumns(["A", "a", "A ", "", "B"]) == ["a", "a_2", "a_3", "col_4", "b"])
        #expect(TabularSQL.tableName(sourceId: "local:file-ab12", sheetIndex: 0) == "tab_local_file_ab12_0")
    }

    @Test("columns are typed as pandas typed them, with the values a model may filter on")
    func columns() {
        let cols = TabularSQL.columns(for: Self.sheet)
        #expect(cols.map(\.sanitized) == ["account", "state", "arr", "renewal_date"])
        #expect(cols[0].dtype == "text" && cols[0].lowCardinality == true && cols[0].values == ["Acme", "Beta", "Gamma"])
        #expect(cols[1].values == ["Ohio", "Texas"])
        #expect(cols[2].dtype == "number" && cols[2].samples == ["1200.0", "800.5"])
        #expect(cols[3].dtype == "text")
        #expect(TabularSQL.sqlValue("", numeric: true) == nil)
        #expect(TabularSQL.sqlValue(" 12 ", numeric: true) == "12.0")
        #expect(TabularSQL.sqlValue("Texas", numeric: false) == "Texas")
    }

    @Test("one read-only SELECT passes; prose, fences and anything else is handled or refused")
    func safety() throws {
        #expect(TabularSQL.extractSQL("Sure! ```sql\nSELECT COUNT(*) FROM t WHERE state = 'Texas';\n```") == "SELECT COUNT(*) FROM t WHERE state = 'Texas'")
        #expect(TabularSQL.extractSQL("The query is select a from t; select b from t") == "select a from t")
        #expect(try TabularSQL.safeSQL("WITH x AS (SELECT 1) SELECT * FROM x").get() == "WITH x AS (SELECT 1) SELECT * FROM x")
        #expect(TabularSQL.safeSQL("DROP TABLE t") == .failure(.notASelect))
        #expect(TabularSQL.safeSQL("SELECT * FROM t WHERE 1; DELETE FROM t") == .success("SELECT * FROM t WHERE 1"))
        #expect(TabularSQL.safeSQL("SELECT * FROM t; -- then\nDROP") == .success("SELECT * FROM t"))
        #expect(TabularSQL.safeSQL("select load_extension('x') from pragma_table_info('t')") == .failure(.forbidden))
        #expect(TabularSQL.safeSQL("I cannot answer that.") == .failure(.noSQL))
    }

    @Test("the prompt lists tables, typed columns and their values")
    func prompt() {
        let table = TabularSQL.Table(sourceId: "s", filename: "accounts.xlsx", sheetName: "Sheet1", tableName: "tab_s_0", columns: TabularSQL.columns(for: Self.sheet), rowCount: 3)
        let p = TabularSQL.prompt(question: "How many accounts are in Texas?", schema: [table])
        #expect(p.contains("Table \"tab_s_0\" (from file \"accounts.xlsx\", sheet \"Sheet1\", 3 rows):"))
        #expect(p.contains("  - state (text) — one of: Ohio, Texas"))
        #expect(p.contains("  - arr (number) — e.g. 1200.0, 800.5"))
        #expect(p.hasSuffix("Question: How many accounts are in Texas?\nSQL:"))
    }

    @Test("answers: a bold scalar, a row of pairs, a table cut at fifty rows")
    func render() {
        #expect(TabularSQL.render(columns: ["COUNT(*)"], rows: [["2"]]) == "**2**")
        #expect(TabularSQL.render(columns: ["state", "total_arr"], rows: [["Texas", "1200.0"]]) == "State: **Texas**, Total Arr: **1200**")
        #expect(TabularSQL.render(columns: ["a"], rows: []) == "No matching rows found.")
        let many = TabularSQL.render(columns: ["account", "arr"], rows: (0..<60).map { ["A\($0)", "\($0).5"] })
        #expect(many.hasPrefix("| Account | Arr |\n| --- | --- |\n| A0 | 0.5 |"))
        #expect(many.hasSuffix("_…and 10 more rows._"))
    }

    @Test("the store indexes a sheet, answers a count and a sum, refuses writes, and drops the source")
    func store() async throws {
        let url = FileManager.default.temporaryDirectory.appending(path: "tabular-test-\(UUID().uuidString).sqlite")
        defer { try? FileManager.default.removeItem(at: url) }
        let store = try TabularStore(path: url)
        try await store.prepare()
        let made = try await store.index(notebook: "nb", sourceId: "local:file-1", filename: "accounts.xlsx", sheets: [("Sheet1", Self.sheet), ("Empty", TextTable(columns: ["x"], rows: []))])
        #expect(made.count == 1 && made[0].tableName == "tab_local_file_1_0" && made[0].rowCount == 3)
        #expect(await store.hasTables(notebook: "nb"))
        #expect(await store.schema(notebook: "other").isEmpty)
        let count = try await store.execute("SELECT COUNT(*) FROM tab_local_file_1_0 WHERE state = 'Texas'")
        #expect(count.rows == [["2"]])
        let sum = try await store.execute("SELECT state, SUM(arr) AS total FROM tab_local_file_1_0 GROUP BY state ORDER BY state")
        #expect(sum.columns == ["state", "total"])
        #expect(sum.rows == [["Ohio", "800.5"], ["Texas", "1200"]])
        await #expect(throws: (any Error).self) { _ = try await store.execute("DELETE FROM tab_local_file_1_0") }
        try await store.delete(sourceId: "local:file-1")
        #expect(await store.hasTables(notebook: "nb") == false)
    }
}
