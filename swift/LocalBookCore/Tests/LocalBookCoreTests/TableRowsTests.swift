import Foundation
import Testing
@testable import LocalBookCore

/// The pricing PDF of the Phase 3 fixture: Vision transcribed both ruled tables but dropped four one-digit cells, and
/// the text layer has every row as one line of words.
@Suite("Table rows repaired from the text layer")
struct TableRowsTests {
    let lines = [
        "## **6. Key numbers**", "The key numbers, as a ruled table:",
        "Metric Value As of", "Wholesale accounts 240 Nov 2026", "States served 5 Nov 2026",
        "Roastery capacity 1,800 kg per week Oct 2026", "Employees 63 Nov 2026",
        "Accounts by state and plan (counts):",
        "State Basic Pro Enterprise Total", "Texas 51 38 14 103", "Ohio 29 20 9 58", "Oregon 13 7 2 22",
    ]

    @Test("a dropped digit comes back from the line, and the lines are marked as the table's")
    func droppedCells() {
        let vision = [["Metric", "Value", "As of"], ["Wholesale accounts", "240", "Nov 2026"], ["States served", "", "Nov 2026"],
                      ["Roastery capacity", "1,800 kg per week", "Oct 2026"], ["Employees", "63", "Nov 2026"]]
        let r = TableRows.repair(vision, lines: lines)
        #expect(r.rows[2] == ["States served", "5", "Nov 2026"])
        #expect(r.rows == [["Metric", "Value", "As of"], ["Wholesale accounts", "240", "Nov 2026"], ["States served", "5", "Nov 2026"],
                           ["Roastery capacity", "1,800 kg per week", "Oct 2026"], ["Employees", "63", "Nov 2026"]])
        #expect(r.covered == ["Metric Value As of", "Wholesale accounts 240 Nov 2026", "States served 5 Nov 2026",
                              "Roastery capacity 1,800 kg per week Oct 2026", "Employees 63 Nov 2026"])
        let second = TableRows.repair([["State", "Basic", "Pro", "Enterprise", "Total"], ["Ohio", "29", "20", "", "58"]], lines: lines)
        #expect(second.rows[1] == ["Ohio", "29", "20", "9", "58"])
        #expect(second.covered.contains("State Basic Pro Enterprise Total"))
    }

    @Test("a row no line holds keeps the transcript; a line with extra words around the cells is not the row")
    func noMatch() {
        let r = TableRows.repair([["Nowhere", "1"], ["Texas", "51", "", "103"]], lines: lines)
        #expect(r.rows[0] == ["Nowhere", "1"])
        #expect(r.rows[1] == ["Texas", "51", "38 14", "103"])   // two words between the neighbours fill the one empty cell
        #expect(TableRows.align(["Value", "As of"], to: "Metric Value As of") == nil)
        #expect(TableRows.align(["", ""], to: "a b") == nil)
    }

    @Test("a cell may be a lone digit on a line of digits, matched in order")
    func digits() {
        #expect(TableRows.align(["Oregon", "13", "", "2", "22"], to: "Oregon 13 7 2 22") == ["Oregon", "13", "7", "2", "22"])
        #expect(TableRows.align(["Ohio", "", "", "9", "58"], to: "Ohio 29 20 9 58") == ["Ohio", "29 20", "", "9", "58"])
    }
}
