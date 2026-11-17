import Foundation
import LocalBookCore

/// Spreadsheet questions: the old app's text-to-SQL path. A spreadsheet or CSV read on this Mac also goes into
/// `TabularStore` as typed tables; a question that wants a count, a sum, a comparison or a list is turned into one
/// read-only SELECT by the answer model, checked, run, and answered from the rows. Anything that fails returns nil and
/// the question goes to the passages instead.
extension LibraryModel {
    public struct TableAnswer: Sendable {
        public let answer: String
        public let sql: String
        public let hit: LibraryHit
    }

    /// The sheets of a tabular file as tables, after the source itself is in. Failures are logged, never shown: the
    /// source still answers from its sentences.
    func indexTables(_ url: URL, format: String, notebook: String, sourceId: String, filename: String) async {
        guard let tabular else { return }
        do {
            let sheets: [(name: String, table: TextTable)]
            switch format {
            case "xlsx": sheets = try await Task.detached { try XLSXReader.read(url) }.value
            case "xls": sheets = try await Task.detached { try XLSReader.read(try Data(contentsOf: url)) }.value
            case "csv":
                let table = CSV.table(try TextExtract.plainText(url))
                sheets = table.rows.isEmpty ? [] : [("CSV Data", table)]
            default: return
            }
            let made = try await tabular.index(notebook: notebook, sourceId: sourceId, filename: filename, sheets: sheets)
            SoakTrace.log("tabular: \(filename) → \(made.count) table\(made.count == 1 ? "" : "s"), \(made.map(\.rowCount).reduce(0, +)) rows")
        } catch {
            SoakTrace.log("tabular: \(filename) not indexed: \(error.localizedDescription)")
        }
    }

    /// `answer_tabular`: schema → prompt → the model's SELECT → check → run → answer. Nil when any step fails.
    func answerFromTables(question: String, notebook: String) async -> TableAnswer? {
        guard let tabular else { return nil }
        let schema = await tabular.schema(notebook: notebook)
        guard !schema.isEmpty else { return nil }
        let prompt = TabularSQL.prompt(question: question, schema: schema)
        let raw: String
        do { raw = try await generateText(prompt, maxTokens: 400) } catch {
            SoakTrace.log("tabular-sql: model error: \(error.localizedDescription)"); return nil
        }
        let sql: String
        switch TabularSQL.safeSQL(raw) {
        case .success(let s): sql = s
        case .failure(let why): SoakTrace.log("tabular-sql: no valid SQL (\(why)): \(raw.prefix(150))"); return nil
        }
        let rows: TabularStore.Rows
        do { rows = try await tabular.execute(sql) } catch {
            SoakTrace.log("tabular-sql: execution error: \(error.localizedDescription) for \(sql)"); return nil
        }
        SoakTrace.log("tabular-sql: \(sql) → \(rows.rows.count) rows")
        let first = schema[0]
        let title = notebooks.first { $0.id == notebook }?.title ?? notebook
        let hit = LibraryHit(notebookId: notebook, notebookTitle: title, sourceId: first.sourceId, chunkIndex: 0,
                             filename: first.filename, text: "Computed from \(first.filename) (sheet \(first.sheetName)):\n\(sql)", score: 1)
        return TableAnswer(answer: TabularSQL.render(columns: rows.columns, rows: rows.rows), sql: sql, hit: hit)
    }
}
