import Foundation
import Testing
@testable import LocalBookCore

/// `tabular_golden.json` holds what the old app's `_dataframe_to_sentences` wrote for a set of CSV texts read by
/// pandas (`scripts/ci/tabular_golden.py`). The Swift CSV reader plus `TabularText` must write the same lines.
@Suite("Tabular text matches the old app")
struct TabularTextTests {
    struct Fixture: Decodable {
        struct Case: Decodable { let name: String; let csv: String; let sheet: String; let sentences: String }
        let pandas: String; let cases: [Case]
    }

    static func load() throws -> Fixture {
        let url = try #require(Bundle.module.url(forResource: "tabular_golden", withExtension: "json", subdirectory: "Fixtures"))
        return try JSONDecoder().decode(Fixture.self, from: Data(contentsOf: url))
    }

    @Test("every CSV case comes out as pandas and the Python function made it")
    func cases() throws {
        for c in try Self.load().cases {
            let got = TabularText.sentences(CSV.table(c.csv), sheet: c.sheet)
            #expect(got == c.sentences, "\(c.name):\n--- swift ---\n\(got)\n--- python ---\n\(c.sentences)")
        }
    }

    @Test("quoted fields, doubled quotes and embedded newlines parse")
    func csvParsing() {
        let rows = CSV.parse("a,b\n\"x, y\",\"say \"\"hi\"\"\"\n\"two\nlines\",3\n")
        #expect(rows == [["a", "b"], ["x, y", "say \"hi\""], ["two\nlines", "3"]])
    }

    @Test("a Word table's cells stay the strings python-docx gave: no number parsing, no label or value columns")
    func wordTable() {
        let t = TextTable(columns: ["Metric", "Value", "As of"],
                          rows: [["Average lead time", "2.10", "Q3 2026"], ["Employees", "63", "Nov 2026"]])
        let text = TabularText.sentences(t, sheet: "Document Table", textCells: true)
        #expect(text == "Data from sheet 'Document Table':\n\nComplete row data:\n"
                + "Row 1: Metric: Average lead time, Value: 2.10, As of: Q3 2026\nRow 2: Metric: Employees, Value: 63, As of: Nov 2026")
        // A header that pandas would call a value column stays text for strings: no "Employees: Total = 63" lines.
        let totals = TextTable(columns: ["Name", "Total"], rows: [["Employees", "63"]])
        #expect(!TabularText.sentences(totals, sheet: "Document Table", textCells: true).contains("Employees: Total = 63"))
        #expect(TabularText.sentences(totals, sheet: "S").contains("Employees: Total = 63"))
    }

    @Test("a workbook lists each sheet under its marker")
    func workbook() {
        let t = TextTable(columns: ["A", "B"], rows: [["1", "x"]])
        let text = TabularText.workbookText([(name: "S1", table: t)])
        #expect(text.hasPrefix("=== Sheet: S1 ===\n\nData from sheet 'S1':"))
        #expect(text.contains("Row 1: A: 1, B: x"))
    }
}
