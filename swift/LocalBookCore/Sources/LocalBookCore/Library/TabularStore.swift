import Foundation
import SQLite3

/// The structured side of a spreadsheet or CSV source: each sheet as a typed SQLite table in `tabular.sqlite`, with a
/// catalog the text-to-SQL prompt is built from. The old app's `storage/tabular_store.py`. Queries run with
/// `query_only` on and a row cap; the model's SQL is checked by `TabularSQL.safeSQL` before it gets here.
public actor TabularStore {
    private let db: OpaquePointer
    public static let catalog = "_tabular_catalog"

    public static func defaultURL() -> URL { AppFolders.library.appending(path: "tabular.sqlite") }

    public init(path: URL? = nil) throws {
        let url = path ?? Self.defaultURL()
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        var handle: OpaquePointer?
        guard sqlite3_open_v2(url.path, &handle, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, nil) == SQLITE_OK, let handle else {
            throw LibraryError.sqlite("could not open \(url.lastPathComponent)")
        }
        db = handle
    }

    public func prepare() throws {
        try exec("PRAGMA journal_mode=WAL")
        try exec("""
        CREATE TABLE IF NOT EXISTS \(Self.catalog) (
            notebook_id TEXT NOT NULL, source_id TEXT NOT NULL, filename TEXT NOT NULL, sheet_name TEXT NOT NULL,
            table_name TEXT NOT NULL UNIQUE, columns_json TEXT NOT NULL, row_count INTEGER NOT NULL,
            PRIMARY KEY (source_id, table_name))
        """)
    }

    // MARK: writing

    /// Every sheet of a source as a table; a source indexed before is replaced. Sheets with no rows are skipped.
    @discardableResult
    public func index(notebook: String, sourceId: String, filename: String, sheets: [(name: String, table: TextTable)]) throws -> [TabularSQL.Table] {
        try delete(sourceId: sourceId)
        var made: [TabularSQL.Table] = []
        try exec("BEGIN IMMEDIATE")
        do {
            for (i, sheet) in sheets.enumerated() where !sheet.table.rows.isEmpty && !sheet.table.columns.isEmpty {
                let columns = TabularSQL.columns(for: sheet.table)
                let tableName = TabularSQL.tableName(sourceId: sourceId, sheetIndex: i)
                let decl = columns.map { "\"\($0.sanitized)\" \($0.dtype == "number" ? "REAL" : "TEXT")" }.joined(separator: ", ")
                try exec("DROP TABLE IF EXISTS \"\(tableName)\"")
                try exec("CREATE TABLE \"\(tableName)\" (\(decl))")
                let placeholders = columns.map { _ in "?" }.joined(separator: ",")
                let insert = try statement("INSERT INTO \"\(tableName)\" VALUES (\(placeholders))")
                defer { sqlite3_finalize(insert) }
                for row in sheet.table.rows {
                    sqlite3_reset(insert)
                    for (c, column) in columns.enumerated() {
                        let value = c < row.count ? TabularSQL.sqlValue(row[c], numeric: column.dtype == "number") : nil
                        if let value {
                            if column.dtype == "number", let d = Double(value) { sqlite3_bind_double(insert, Int32(c + 1), d) }
                            else { sqlite3_bind_text(insert, Int32(c + 1), value, -1, Self.transient) }
                        } else {
                            sqlite3_bind_null(insert, Int32(c + 1))
                        }
                    }
                    guard sqlite3_step(insert) == SQLITE_DONE else { throw LibraryError.sqlite("insert into \(tableName): \(message)") }
                }
                let entry = TabularSQL.Table(sourceId: sourceId, filename: filename, sheetName: sheet.name, tableName: tableName,
                                             columns: columns, rowCount: sheet.table.rows.count)
                let cat = try statement("INSERT OR REPLACE INTO \(Self.catalog)(notebook_id, source_id, filename, sheet_name, table_name, columns_json, row_count) VALUES (?,?,?,?,?,?,?)")
                defer { sqlite3_finalize(cat) }
                bind(cat, 1, notebook); bind(cat, 2, sourceId); bind(cat, 3, filename); bind(cat, 4, sheet.name); bind(cat, 5, tableName)
                bind(cat, 6, String(decoding: try JSONEncoder().encode(columns), as: UTF8.self))
                sqlite3_bind_int64(cat, 7, Int64(sheet.table.rows.count))
                guard sqlite3_step(cat) == SQLITE_DONE else { throw LibraryError.sqlite("catalog: \(message)") }
                made.append(entry)
            }
            try exec("COMMIT")
        } catch {
            try? exec("ROLLBACK")
            throw error
        }
        return made
    }

    /// Drops a source's tables and catalog rows.
    public func delete(sourceId: String) throws {
        let s = try statement("SELECT table_name FROM \(Self.catalog) WHERE source_id=?")
        bind(s, 1, sourceId)
        var tables: [String] = []
        while sqlite3_step(s) == SQLITE_ROW { tables.append(String(cString: sqlite3_column_text(s, 0))) }
        sqlite3_finalize(s)
        for t in tables { try exec("DROP TABLE IF EXISTS \"\(t)\"") }
        let d = try statement("DELETE FROM \(Self.catalog) WHERE source_id=?")
        defer { sqlite3_finalize(d) }
        bind(d, 1, sourceId)
        _ = sqlite3_step(d)
    }

    /// The source moved to another notebook here: its catalog rows follow (the tables are named after the source).
    public func moveSource(sourceId: String, to notebook: String) throws {
        let s = try statement("UPDATE \(Self.catalog) SET notebook_id=? WHERE source_id=?")
        defer { sqlite3_finalize(s) }
        bind(s, 1, notebook); bind(s, 2, sourceId)
        _ = sqlite3_step(s)
    }

    /// A notebook made here was created on the paired Mac under another id.
    public func renumberNotebook(from old: String, to new: String) throws {
        let s = try statement("UPDATE \(Self.catalog) SET notebook_id=? WHERE notebook_id=?")
        defer { sqlite3_finalize(s) }
        bind(s, 1, new); bind(s, 2, old)
        _ = sqlite3_step(s)
    }

    // MARK: reading

    public func hasTables(notebook: String) -> Bool { !schema(notebook: notebook).isEmpty }

    /// The catalog for a notebook, in the order the sources were indexed.
    public func schema(notebook: String) -> [TabularSQL.Table] {
        guard let s = try? statement("SELECT source_id, filename, sheet_name, table_name, columns_json, row_count FROM \(Self.catalog) WHERE notebook_id=? ORDER BY rowid") else { return [] }
        defer { sqlite3_finalize(s) }
        bind(s, 1, notebook)
        var out: [TabularSQL.Table] = []
        while sqlite3_step(s) == SQLITE_ROW {
            let json = Data(String(cString: sqlite3_column_text(s, 4)).utf8)
            guard let columns = try? JSONDecoder().decode([TabularSQL.Column].self, from: json) else { continue }
            out.append(TabularSQL.Table(sourceId: String(cString: sqlite3_column_text(s, 0)), filename: String(cString: sqlite3_column_text(s, 1)),
                                        sheetName: String(cString: sqlite3_column_text(s, 2)), tableName: String(cString: sqlite3_column_text(s, 3)),
                                        columns: columns, rowCount: Int(sqlite3_column_int64(s, 5))))
        }
        return out
    }

    public struct Rows: Sendable {
        public var columns: [String]
        public var rows: [[String?]]
        public var truncated: Bool
    }

    /// Runs one SELECT that `TabularSQL.safeSQL` passed, read-only, at most `maxResultRows` rows.
    public func execute(_ sql: String) throws -> Rows {
        try exec("PRAGMA query_only = ON")
        defer { try? exec("PRAGMA query_only = OFF") }
        let s = try statement(sql)
        defer { sqlite3_finalize(s) }
        let count = Int(sqlite3_column_count(s))
        let columns = (0..<count).map { String(cString: sqlite3_column_name(s, Int32($0))) }
        var rows: [[String?]] = []
        var truncated = false
        while true {
            let rc = sqlite3_step(s)
            if rc == SQLITE_DONE { break }
            guard rc == SQLITE_ROW else { throw LibraryError.sqlite(message) }
            if rows.count >= TabularSQL.maxResultRows { truncated = true; break }
            rows.append((0..<count).map { i in
                let col = Int32(i)
                switch sqlite3_column_type(s, col) {
                case SQLITE_NULL: return nil
                case SQLITE_FLOAT: return TabularSQL.cell("\(sqlite3_column_double(s, col))")
                case SQLITE_INTEGER: return String(sqlite3_column_int64(s, col))
                default: return sqlite3_column_text(s, col).map { String(cString: $0) }
                }
            })
        }
        return Rows(columns: columns, rows: rows, truncated: truncated)
    }

    // MARK: sqlite

    private static let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
    private var message: String { String(cString: sqlite3_errmsg(db)) }

    func exec(_ sql: String) throws {
        guard sqlite3_exec(db, sql, nil, nil, nil) == SQLITE_OK else { throw LibraryError.sqlite("\(message) in \(sql.prefix(80))") }
    }

    func statement(_ sql: String) throws -> OpaquePointer {
        var s: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &s, nil) == SQLITE_OK, let s else { throw LibraryError.sqlite("\(message) in \(sql.prefix(120))") }
        return s
    }

    func bind(_ s: OpaquePointer, _ index: Int32, _ value: String) {
        sqlite3_bind_text(s, index, value, -1, Self.transient)
    }
}
