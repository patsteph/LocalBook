import Foundation
import SQLite3

/// LocalBook's memory: core facts about the person (by category) and long-term archival memories, each tagged with
/// who wrote it (the app, or a companion by name) so the person can remove everything one agent wrote in one go.
/// The old app's `memory_manager` reduced to what the chat and the MCP tools read and write; `memory.sqlite` beside
/// the library. Search is keyword overlap over lower-cased words (no model, so an agent's call is never busy).
public actor MemoryStore {
    public static func defaultURL() -> URL { AppFolders.library.appending(path: "memory.sqlite") }

    public enum Kind: String, Sendable { case core, archival }

    public struct Memory: Sendable, Identifiable, Equatable {
        public let id: String
        public let kind: Kind
        public let category: String
        public let text: String
        public let source: String
        public let created: String
        public let updated: String
    }

    private let db: OpaquePointer

    public init(path: URL? = nil) throws {
        let url = path ?? Self.defaultURL()
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        var handle: OpaquePointer?
        guard sqlite3_open_v2(url.path, &handle, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, nil) == SQLITE_OK, let handle else {
            throw LibraryError.sqlite("could not open \(url.lastPathComponent)")
        }
        db = handle
        // An actor's init cannot call its own methods, so the tables are made through the static form.
        try Self.run(handle, "PRAGMA journal_mode=WAL")
        try Self.run(handle, """
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, category TEXT NOT NULL DEFAULT '', text TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'localbook', created TEXT NOT NULL, updated TEXT NOT NULL)
        """)
        try Self.run(handle, "CREATE INDEX IF NOT EXISTS memories_kind ON memories(kind, updated DESC)")
    }

    private static func run(_ db: OpaquePointer, _ sql: String) throws {
        var err: UnsafeMutablePointer<CChar>?
        guard sqlite3_exec(db, sql, nil, nil, &err) == SQLITE_OK else {
            let text = err.map { String(cString: $0) } ?? "sqlite error"; sqlite3_free(err); throw LibraryError.sqlite(text)
        }
    }
    private func exec(_ sql: String) throws { try Self.run(db, sql) }
    private func statement(_ sql: String) throws -> OpaquePointer {
        var s: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &s, nil) == SQLITE_OK, let s else { throw LibraryError.sqlite(String(cString: sqlite3_errmsg(db))) }
        return s
    }
    private static let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
    private func bind(_ s: OpaquePointer, _ i: Int32, _ v: String) { sqlite3_bind_text(s, i, v, -1, Self.transient) }
    private static func now() -> String { ISO8601DateFormatter().string(from: Date()) }

    private func row(_ s: OpaquePointer) -> Memory {
        Memory(id: String(cString: sqlite3_column_text(s, 0)), kind: Kind(rawValue: String(cString: sqlite3_column_text(s, 1))) ?? .archival,
               category: String(cString: sqlite3_column_text(s, 2)), text: String(cString: sqlite3_column_text(s, 3)),
               source: String(cString: sqlite3_column_text(s, 4)), created: String(cString: sqlite3_column_text(s, 5)), updated: String(cString: sqlite3_column_text(s, 6)))
    }

    // MARK: writing

    @discardableResult
    public func add(_ kind: Kind, text: String, category: String = "", source: String = "localbook") throws -> Memory {
        let body = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !body.isEmpty else { throw LabError.model("a memory needs some text") }
        guard body.count <= 4_000 else { throw LabError.model("a memory is at most 4,000 characters") }
        // The same words again refresh the old row rather than adding a twin.
        if let same = try? all(kind: kind).first(where: { $0.text == body && $0.source == source }) {
            let s = try statement("UPDATE memories SET updated=?, category=? WHERE id=?")
            defer { sqlite3_finalize(s) }
            bind(s, 1, Self.now()); bind(s, 2, category.isEmpty ? same.category : category); bind(s, 3, same.id)
            sqlite3_step(s)
            return Memory(id: same.id, kind: kind, category: category.isEmpty ? same.category : category, text: body, source: source, created: same.created, updated: Self.now())
        }
        let m = Memory(id: UUID().uuidString.lowercased(), kind: kind, category: category, text: body, source: source, created: Self.now(), updated: Self.now())
        let s = try statement("INSERT INTO memories (id, kind, category, text, source, created, updated) VALUES (?,?,?,?,?,?,?)")
        defer { sqlite3_finalize(s) }
        bind(s, 1, m.id); bind(s, 2, m.kind.rawValue); bind(s, 3, m.category); bind(s, 4, m.text); bind(s, 5, m.source); bind(s, 6, m.created); bind(s, 7, m.updated)
        guard sqlite3_step(s) == SQLITE_DONE else { throw LibraryError.sqlite(String(cString: sqlite3_errmsg(db))) }
        return m
    }

    public func update(_ id: String, text: String, category: String? = nil) throws {
        let s = try statement("UPDATE memories SET text=?, category=COALESCE(?, category), updated=? WHERE id=?")
        defer { sqlite3_finalize(s) }
        bind(s, 1, text); if let category { bind(s, 2, category) } else { sqlite3_bind_null(s, 2) }; bind(s, 3, Self.now()); bind(s, 4, id)
        sqlite3_step(s)
    }

    public func delete(_ id: String) throws {
        let s = try statement("DELETE FROM memories WHERE id=?"); defer { sqlite3_finalize(s) }
        bind(s, 1, id); sqlite3_step(s)
    }

    /// Everything one source (a companion) wrote, gone in one action.
    @discardableResult
    public func deleteAll(source: String) throws -> Int {
        let s = try statement("DELETE FROM memories WHERE source=?"); defer { sqlite3_finalize(s) }
        bind(s, 1, source); sqlite3_step(s)
        return Int(sqlite3_changes(db))
    }

    // MARK: reading

    public func all(kind: Kind? = nil) throws -> [Memory] {
        let s = try statement(kind == nil ? "SELECT * FROM memories ORDER BY updated DESC" : "SELECT * FROM memories WHERE kind=? ORDER BY updated DESC")
        defer { sqlite3_finalize(s) }
        if let kind { bind(s, 1, kind.rawValue) }
        var out: [Memory] = []
        while sqlite3_step(s) == SQLITE_ROW { out.append(row(s)) }
        return out
    }

    public func count() -> Int {
        guard let s = try? statement("SELECT COUNT(*) FROM memories") else { return 0 }
        defer { sqlite3_finalize(s) }
        return sqlite3_step(s) == SQLITE_ROW ? Int(sqlite3_column_int64(s, 0)) : 0
    }

    /// Words of three letters or more, lower-cased, stems of five letters so "preferences" meets "preference".
    static func words(_ text: String) -> Set<String> {
        Set(text.lowercased().split { !$0.isLetter && !$0.isNumber }.map(String.init).filter { $0.count >= 3 }.map { String($0.prefix(5)) })
    }

    public struct Hit: Sendable, Equatable { public let memory: Memory; public let score: Double }

    /// The memories whose words overlap the query's most, core facts first on a tie. Empty query: the newest.
    public func search(_ query: String, k: Int = 8) throws -> [Hit] {
        let q = Self.words(query)
        let rows = try all()
        if q.isEmpty { return rows.prefix(k).map { Hit(memory: $0, score: 0) } }
        return rows.compactMap { m -> Hit? in
            let w = Self.words(m.text + " " + m.category)
            let overlap = Double(q.intersection(w).count)
            guard overlap > 0 else { return nil }
            return Hit(memory: m, score: overlap / Double(q.count) + (m.kind == .core ? 0.01 : 0))
        }.sorted { $0.score != $1.score ? $0.score > $1.score : $0.memory.updated > $1.memory.updated }.prefix(k).map { $0 }
    }

    /// What `memory_search` returns: sections of items within a character budget, each tagged with its source.
    /// Boxed, since a dictionary of Any cannot leave the actor as it is.
    public func prefetch(_ query: String, k: Int = 8, charBudget: Int = 2_000) throws -> JSONBox {
        let hits = try search(query, k: k)
        var chars = 0
        var core: [[String: Any]] = [], archival: [[String: Any]] = []
        for h in hits {
            guard chars + h.memory.text.count <= charBudget else { break }
            chars += h.memory.text.count
            let item: [String: Any] = ["id": h.memory.id, "text": h.memory.text, "category": h.memory.category, "source": h.memory.source, "updated": h.memory.updated]
            if h.memory.kind == .core { core.append(item) } else { archival.append(item) }
        }
        var sections: [[String: Any]] = []
        if !core.isEmpty { sections.append(["kind": "core", "items": core]) }
        if !archival.isEmpty { sections.append(["kind": "archival", "items": archival]) }
        return JSONBox(["sections": sections, "chars": chars])
    }
}
