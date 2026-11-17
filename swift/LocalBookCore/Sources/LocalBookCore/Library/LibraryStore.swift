import Foundation
import Accelerate
import SQLite3

/// The phone's copy of the Mac's library: chunk text and the Mac's own vectors (float16). Read-only
/// from the phone's point of view — it is replaced by sync, never edited. At this size (about 11,000
/// chunks) a plain scan over the vectors answers a question in milliseconds, so there is no vector index.
public struct LibraryChunk: Sendable {
    public let notebookId: String
    public let sourceId: String
    public let chunkIndex: Int
    public let text: String
    public let summary: Bool
    public let filename: String
    public let sourceType: String
    public let vector: Data            // 768 × float16, little endian
    public init(notebookId: String, sourceId: String, chunkIndex: Int, text: String, summary: Bool,
                filename: String, sourceType: String, vector: Data) {
        self.notebookId = notebookId; self.sourceId = sourceId; self.chunkIndex = chunkIndex; self.text = text
        self.summary = summary; self.filename = filename; self.sourceType = sourceType; self.vector = vector
    }
}

public struct LibraryHit: Sendable, Identifiable {
    public var id: String { "\(notebookId)/\(sourceId)/\(chunkIndex)" }
    public let notebookId: String
    public let notebookTitle: String
    public let sourceId: String
    public let chunkIndex: Int
    public let filename: String
    public let text: String
    public let score: Float
    public var rrf: Float = 0              // Reciprocal Rank Fusion score divided by the best one in the pool (0…1)
    public var sourceType: String = ""


    public init(notebookId: String, notebookTitle: String, sourceId: String, chunkIndex: Int, filename: String, text: String, score: Float, rrf: Float = 0, sourceType: String = "") {

        self.notebookId = notebookId; self.notebookTitle = notebookTitle; self.sourceId = sourceId; self.chunkIndex = chunkIndex; self.filename = filename; self.text = text; self.score = score; self.rrf = rrf; self.sourceType = sourceType

    }
}

public struct LibraryNotebookInfo: Sendable, Identifiable {
    public let id: String
    public let title: String
    public let digest: String
    public let chunks: Int
    public var section: String? = nil       // the desktop's section for it, if any
    public var color: String? = nil         // the desktop's notebook color (hex)
    public var sectionCollapsed = false     // whether the desktop shows its section folded
}

public enum LibraryError: LocalizedError, Sendable {
    case sqlite(String)
    case embedderMismatch(String)
    case notPaired
    case badResponse(String)
    public var errorDescription: String? {
        switch self {
        case .sqlite(let m): "Library database: \(m)"
        case .embedderMismatch(let m): m
        case .notPaired: "Pair with a Mac first (row 3)."
        case .badResponse(let m): "Unexpected reply from the Mac: \(m)"
        }
    }
}

public actor LibraryStore {
    public static let dims = 768
    private var db: OpaquePointer?
    /// The vectors of ONE notebook as Float32 in one block (about 11 MB for 3,700 chunks), built on its first
    /// search and kept until another notebook is searched or the data changes. The app searches one notebook
    /// at a time, so memory follows the notebook being used, not the size of the whole library.
    private var matrix: [Float] = []
    private var rowKeys: [(src: String, idx: Int)] = []
    private var loadedNotebook: String?

    private func dropMatrix() { matrix = []; rowKeys = []; loadedNotebook = nil; bm25 = [:] }

    private func loadMatrix(_ notebook: String) throws {
        if loadedNotebook == notebook { return }
        matrix = []; rowKeys = []; loadedNotebook = nil
        bm25 = bm25.filter { $0.key == notebook }
        let count = try statement("SELECT COUNT(*) FROM chunks WHERE notebook_id=?")
        bind(count, 1, notebook)
        let n = sqlite3_step(count) == SQLITE_ROW ? Int(sqlite3_column_int64(count, 0)) : 0
        sqlite3_finalize(count)
        var m = [Float](repeating: 0, count: n * Self.dims)
        var keys: [(src: String, idx: Int)] = []
        keys.reserveCapacity(n)
        let s = try statement("SELECT source_id, chunk_index, vector FROM chunks WHERE notebook_id=? ORDER BY source_id, chunk_index")
        defer { sqlite3_finalize(s) }
        bind(s, 1, notebook)
        var row = 0
        while sqlite3_step(s) == SQLITE_ROW, row < n {
            guard let blob = sqlite3_column_blob(s, 2), sqlite3_column_bytes(s, 2) == Self.dims * 2 else { continue }
            let halves = blob.assumingMemoryBound(to: Float16.self)
            let base = row * Self.dims
            for i in 0..<Self.dims { m[base + i] = Float(halves[i]) }
            keys.append((String(cString: sqlite3_column_text(s, 0)), Int(sqlite3_column_int64(s, 1))))
            row += 1
        }
        matrix = m; rowKeys = keys; loadedNotebook = notebook
    }

    public init(path: URL? = nil) throws {
        let url = path ?? Self.defaultURL()
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        var handle: OpaquePointer?
        guard sqlite3_open_v2(url.path, &handle, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, nil) == SQLITE_OK else {
            throw LibraryError.sqlite("could not open \(url.lastPathComponent)")
        }
        db = handle
    }

    /// Create the tables (idempotent). Separate from `init` because an actor's init cannot call its own methods.
    public func prepare() throws {
        for sql in [
            "PRAGMA journal_mode=WAL",
            "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
            "CREATE TABLE IF NOT EXISTS notebooks (id TEXT PRIMARY KEY, title TEXT NOT NULL, digest TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS sources (notebook_id TEXT NOT NULL, source_id TEXT NOT NULL, hash TEXT NOT NULL, PRIMARY KEY (notebook_id, source_id))",
            """
            CREATE TABLE IF NOT EXISTS chunks (
              notebook_id TEXT NOT NULL, source_id TEXT NOT NULL, chunk_index INTEGER NOT NULL,
              text TEXT NOT NULL, summary INTEGER NOT NULL, filename TEXT NOT NULL, source_type TEXT NOT NULL,
              vector BLOB NOT NULL, PRIMARY KEY (notebook_id, source_id, chunk_index))
            """,
            // The whole text of a source as the old app extracted it (the chunks are pieces of it), from the import.
            "CREATE TABLE IF NOT EXISTS source_text (notebook_id TEXT NOT NULL, source_id TEXT NOT NULL, text TEXT NOT NULL, url TEXT, format TEXT, PRIMARY KEY (notebook_id, source_id))",
            // The old app's Canvas notes, moved over as they were; read here, never sent anywhere.
            "CREATE TABLE IF NOT EXISTS imported_notes (id TEXT PRIMARY KEY, notebook_id TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL, kind TEXT NOT NULL, created TEXT NOT NULL)",
            "CREATE TABLE IF NOT EXISTS topics (notebook_id TEXT NOT NULL, topic_id TEXT NOT NULL, name TEXT NOT NULL, keywords TEXT NOT NULL, chunks INTEGER NOT NULL, sources INTEGER NOT NULL, position INTEGER NOT NULL, PRIMARY KEY (notebook_id, topic_id))",
        ] { try exec(sql) }
        // Keyword ranking is exact BM25 computed in memory (see bm25Index); the SQLite full-text table an earlier
        // build kept beside the chunks is not used any more, so drop it and its space.
        try? exec("DROP TABLE IF EXISTS chunks_fts")
        // The desktop's order and sections for notebooks (older stores lack the columns).
        try addColumn("notebooks", "position", "INTEGER NOT NULL DEFAULT 0")
        try addColumn("notebooks", "section", "TEXT")
        try addColumn("notebooks", "color", "TEXT")
        try addColumn("notebooks", "collapsed", "INTEGER NOT NULL DEFAULT 0")
        try addColumn("sources", "created", "TEXT")          // when the source was added on the Mac (newest-first lists)
        // The kept original of a source made here: a path under the app's files, or an absolute path inside a watched folder.
        try addColumn("source_text", "file", "TEXT")
        for sql in [
            // Folders this Mac watches for new files (the old app's linked folders): read-only, polled, never twice.
            "CREATE TABLE IF NOT EXISTS watched_folders (id TEXT PRIMARY KEY, notebook_id TEXT NOT NULL, path TEXT NOT NULL, bookmark BLOB, recursive INTEGER NOT NULL DEFAULT 1, added TEXT NOT NULL, last_scan TEXT, last_report TEXT)",
            "CREATE TABLE IF NOT EXISTS watched_files (folder_id TEXT NOT NULL, path TEXT NOT NULL, mtime REAL NOT NULL, size INTEGER NOT NULL, source_id TEXT, PRIMARY KEY (folder_id, path))",
            // A note's text as written (Markdown), kept beside its chunks so the editor opens exactly what was saved.
            "CREATE TABLE IF NOT EXISTS note_text (notebook_id TEXT NOT NULL, source_id TEXT NOT NULL, text TEXT NOT NULL, PRIMARY KEY (notebook_id, source_id))",
        ] { try exec(sql) }
    }

    /// Adds a column to an older store; does nothing (and logs nothing) when it is already there.
    private func addColumn(_ table: String, _ column: String, _ definition: String) throws {
        let s = try statement("PRAGMA table_info(\(table))")
        defer { sqlite3_finalize(s) }
        while sqlite3_step(s) == SQLITE_ROW {
            if let name = sqlite3_column_text(s, 1), String(cString: name) == column { return }
        }
        try exec("ALTER TABLE \(table) ADD COLUMN \(column) \(definition)")
    }

    // No deinit: the store lives as long as the app, and SQLite closes the file with the process.

    public static func defaultURL() -> URL {
        AppFolders.library.appending(path: "library.sqlite")
    }

    // MARK: basics

    private func exec(_ sql: String) throws {
        var err: UnsafeMutablePointer<CChar>?
        guard sqlite3_exec(db, sql, nil, nil, &err) == SQLITE_OK else {
            let m = err.map { String(cString: $0) } ?? "unknown"
            sqlite3_free(err)
            throw LibraryError.sqlite(m)
        }
    }

    private func statement(_ sql: String) throws -> OpaquePointer {
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK, let stmt else {
            throw LibraryError.sqlite(String(cString: sqlite3_errmsg(db)))
        }
        return stmt
    }

    private static let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

    private func bind(_ stmt: OpaquePointer, _ index: Int32, _ value: String) {
        sqlite3_bind_text(stmt, index, value, -1, Self.transient)
    }

    // MARK: meta

    public func meta(_ key: String) -> String? {
        guard let s = try? statement("SELECT value FROM meta WHERE key=?") else { return nil }
        defer { sqlite3_finalize(s) }
        bind(s, 1, key)
        return sqlite3_step(s) == SQLITE_ROW ? String(cString: sqlite3_column_text(s, 0)) : nil
    }

    public func setMeta(_ key: String, _ value: String) throws {
        let s = try statement("INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)")
        defer { sqlite3_finalize(s) }
        bind(s, 1, key); bind(s, 2, value)
        guard sqlite3_step(s) == SQLITE_DONE else { throw LibraryError.sqlite(String(cString: sqlite3_errmsg(db))) }
    }

    public func metaKeys(prefix: String) -> [String] {
        guard let s = try? statement("SELECT key FROM meta WHERE key LIKE ? || '%'") else { return [] }
        defer { sqlite3_finalize(s) }
        bind(s, 1, prefix)
        var out: [String] = []
        while sqlite3_step(s) == SQLITE_ROW { out.append(String(cString: sqlite3_column_text(s, 0))) }
        return out
    }

    public func deleteMeta(_ key: String) throws {
        let s = try statement("DELETE FROM meta WHERE key=?")
        defer { sqlite3_finalize(s) }
        bind(s, 1, key); sqlite3_step(s)
    }

    // MARK: moved over from the old app

    public func setSourceText(notebook: String, sourceId: String, text: String, url: String?, format: String?) throws {
        let s = try statement("INSERT OR REPLACE INTO source_text(notebook_id, source_id, text, url, format) VALUES (?,?,?,?,?)")
        defer { sqlite3_finalize(s) }
        bind(s, 1, notebook); bind(s, 2, sourceId); bind(s, 3, text)
        if let url { bind(s, 4, url) } else { sqlite3_bind_null(s, 4) }
        if let format { bind(s, 5, format) } else { sqlite3_bind_null(s, 5) }
        guard sqlite3_step(s) == SQLITE_DONE else { throw LibraryError.sqlite(String(cString: sqlite3_errmsg(db))) }
    }

    public func hasSourceText(notebook: String, sourceId: String) -> Bool {
        guard let s = try? statement("SELECT 1 FROM source_text WHERE notebook_id=? AND source_id=?") else { return false }
        defer { sqlite3_finalize(s) }
        bind(s, 1, notebook); bind(s, 2, sourceId)
        return sqlite3_step(s) == SQLITE_ROW
    }

    public struct ImportedNote: Sendable, Identifiable, Hashable {
        public let id: String, notebookId: String, title: String, text: String, kind: String, created: String
        public init(id: String, notebookId: String, title: String, text: String, kind: String, created: String) {
            self.id = id; self.notebookId = notebookId; self.title = title; self.text = text; self.kind = kind; self.created = created
        }
    }

    public func addImportedNote(_ n: ImportedNote) throws {
        let s = try statement("INSERT OR REPLACE INTO imported_notes(id, notebook_id, title, text, kind, created) VALUES (?,?,?,?,?,?)")
        defer { sqlite3_finalize(s) }
        bind(s, 1, n.id); bind(s, 2, n.notebookId); bind(s, 3, n.title); bind(s, 4, n.text); bind(s, 5, n.kind); bind(s, 6, n.created)
        guard sqlite3_step(s) == SQLITE_DONE else { throw LibraryError.sqlite(String(cString: sqlite3_errmsg(db))) }
    }

    // MARK: topics (the Mac's topic model, one row per topic per notebook, biggest first)

    public struct TopicInfo: Sendable, Identifiable, Hashable {
        public let id: String, notebookId: String, name: String, keywords: [String], chunks: Int, sources: Int
        public init(id: String, notebookId: String, name: String, keywords: [String], chunks: Int, sources: Int) {
            self.id = id; self.notebookId = notebookId; self.name = name; self.keywords = keywords; self.chunks = chunks; self.sources = sources
        }
    }

    /// Replaces the notebook's topics with this list, in this order.
    public func setTopics(notebook: String, _ list: [TopicInfo]) throws {
        try exec("BEGIN")
        do {
            let d = try statement("DELETE FROM topics WHERE notebook_id=?")
            bind(d, 1, notebook); _ = sqlite3_step(d); sqlite3_finalize(d)
            for (i, t) in list.enumerated() {
                let s = try statement("INSERT INTO topics(notebook_id, topic_id, name, keywords, chunks, sources, position) VALUES (?,?,?,?,?,?,?)")
                defer { sqlite3_finalize(s) }
                bind(s, 1, notebook); bind(s, 2, t.id); bind(s, 3, t.name); bind(s, 4, t.keywords.joined(separator: "\u{1F}"))
                sqlite3_bind_int64(s, 5, Int64(t.chunks)); sqlite3_bind_int64(s, 6, Int64(t.sources)); sqlite3_bind_int64(s, 7, Int64(i))
                guard sqlite3_step(s) == SQLITE_DONE else { throw LibraryError.sqlite(String(cString: sqlite3_errmsg(db))) }
            }
            try exec("COMMIT")
        } catch { try? exec("ROLLBACK"); throw error }
    }

    public func topics(notebook: String) -> [TopicInfo] {
        guard let s = try? statement("SELECT topic_id, name, keywords, chunks, sources FROM topics WHERE notebook_id=? ORDER BY position") else { return [] }
        defer { sqlite3_finalize(s) }
        bind(s, 1, notebook)
        var out: [TopicInfo] = []
        while sqlite3_step(s) == SQLITE_ROW {
            let kw = String(cString: sqlite3_column_text(s, 2))
            out.append(.init(id: String(cString: sqlite3_column_text(s, 0)), notebookId: notebook, name: String(cString: sqlite3_column_text(s, 1)),
                             keywords: kw.isEmpty ? [] : kw.components(separatedBy: "\u{1F}"),
                             chunks: Int(sqlite3_column_int64(s, 3)), sources: Int(sqlite3_column_int64(s, 4))))
        }
        return out
    }

    /// The one-line summary a sidebar row shows on hover: how many sources, and when the newest was added.
    public func notebookSummary(notebook: String) -> (sources: Int, lastAdded: String?) {
        guard let s = try? statement("SELECT COUNT(*), MAX(created) FROM sources WHERE notebook_id=?") else { return (0, nil) }
        defer { sqlite3_finalize(s) }
        bind(s, 1, notebook)
        guard sqlite3_step(s) == SQLITE_ROW else { return (0, nil) }
        let last = sqlite3_column_text(s, 1).map { String(cString: $0) }
        return (Int(sqlite3_column_int64(s, 0)), (last?.isEmpty == false) ? last : nil)
    }

    public func importedNotes(notebook: String?) -> [ImportedNote] {
        guard let s = try? statement("SELECT id, notebook_id, title, text, kind, created FROM imported_notes WHERE (? IS NULL OR notebook_id=?) ORDER BY created DESC") else { return [] }
        defer { sqlite3_finalize(s) }
        if let notebook { bind(s, 1, notebook); bind(s, 2, notebook) } else { sqlite3_bind_null(s, 1); sqlite3_bind_null(s, 2) }
        var out: [ImportedNote] = []
        while sqlite3_step(s) == SQLITE_ROW {
            out.append(.init(id: String(cString: sqlite3_column_text(s, 0)), notebookId: String(cString: sqlite3_column_text(s, 1)),
                             title: String(cString: sqlite3_column_text(s, 2)), text: String(cString: sqlite3_column_text(s, 3)),
                             kind: String(cString: sqlite3_column_text(s, 4)), created: String(cString: sqlite3_column_text(s, 5))))
        }
        return out
    }

    // MARK: what is here

    public func notebooks() -> [LibraryNotebookInfo] {
        guard let s = try? statement("""
            SELECT n.id, n.title, n.digest, (SELECT COUNT(*) FROM chunks c WHERE c.notebook_id = n.id), n.section, n.color, n.collapsed
            FROM notebooks n ORDER BY n.position, n.title COLLATE NOCASE
            """) else { return [] }
        defer { sqlite3_finalize(s) }
        var out: [LibraryNotebookInfo] = []
        while sqlite3_step(s) == SQLITE_ROW {
            let section = sqlite3_column_type(s, 4) == SQLITE_NULL ? nil : String(cString: sqlite3_column_text(s, 4))
            out.append(.init(id: String(cString: sqlite3_column_text(s, 0)), title: String(cString: sqlite3_column_text(s, 1)),
                             digest: String(cString: sqlite3_column_text(s, 2)), chunks: Int(sqlite3_column_int64(s, 3)), section: section,
                             color: sqlite3_column_type(s, 5) == SQLITE_NULL ? nil : String(cString: sqlite3_column_text(s, 5)),
                             sectionCollapsed: sqlite3_column_int(s, 6) != 0))
        }
        return out
    }

    /// The desktop's order and sections, as the Mac listed them.
    public func setLayout(_ items: [(id: String, position: Int, section: String?, color: String?, collapsed: Bool)]) throws {
        for item in items {
            let s = try statement("UPDATE notebooks SET position=?, section=?, color=?, collapsed=? WHERE id=?")
            sqlite3_bind_int64(s, 1, Int64(item.position))
            if let sec = item.section { bind(s, 2, sec) } else { sqlite3_bind_null(s, 2) }
            if let c = item.color { bind(s, 3, c) } else { sqlite3_bind_null(s, 3) }
            sqlite3_bind_int(s, 4, item.collapsed ? 1 : 0)
            bind(s, 5, item.id)
            sqlite3_step(s); sqlite3_finalize(s)
        }
    }

    public func sourceHashes(notebook: String) -> [String: String] {
        guard let s = try? statement("SELECT source_id, hash FROM sources WHERE notebook_id=?") else { return [:] }
        defer { sqlite3_finalize(s) }
        bind(s, 1, notebook)
        var out: [String: String] = [:]
        while sqlite3_step(s) == SQLITE_ROW {
            out[String(cString: sqlite3_column_text(s, 0))] = String(cString: sqlite3_column_text(s, 1))
        }
        return out
    }

    // MARK: notes made on this phone (source ids start with localNotePrefix; sync never touches them)

    /// Source ids of notes made on this device start with this; the Mac never removes them.
    public static let localNotePrefix = "local:"

    public struct LocalNote: Sendable, Identifiable { public let id: String; public let notebookId: String; public let title: String; public let text: String; public let kind: String; public var hasPicture = false }

    public func addLocalNote(notebook: String, sourceId: String, title: String, kind: String, chunks: [LibraryChunk], hash: String, text: String? = nil) throws {
        dropMatrix()
        try exec("BEGIN IMMEDIATE")
        do {
            if let text {
                let t = try statement("INSERT OR REPLACE INTO note_text(notebook_id, source_id, text) VALUES (?,?,?)")
                bind(t, 1, notebook); bind(t, 2, sourceId); bind(t, 3, text); sqlite3_step(t); sqlite3_finalize(t)
            }
            let insert = try statement("""
                INSERT OR REPLACE INTO chunks(notebook_id, source_id, chunk_index, text, summary, filename, source_type, vector)
                VALUES (?,?,?,?,?,?,?,?)
                """)
            defer { sqlite3_finalize(insert) }
            for c in chunks {
                sqlite3_reset(insert)
                bind(insert, 1, c.notebookId); bind(insert, 2, c.sourceId)
                sqlite3_bind_int64(insert, 3, Int64(c.chunkIndex))
                bind(insert, 4, c.text); sqlite3_bind_int(insert, 5, 0)
                bind(insert, 6, c.filename); bind(insert, 7, kind)
                _ = c.vector.withUnsafeBytes { sqlite3_bind_blob(insert, 8, $0.baseAddress, Int32($0.count), Self.transient) }
                guard sqlite3_step(insert) == SQLITE_DONE else { throw LibraryError.sqlite(String(cString: sqlite3_errmsg(db))) }
            }
            let s = try statement("INSERT INTO sources(notebook_id, source_id, hash, created) VALUES (?,?,?,?) ON CONFLICT(notebook_id, source_id) DO UPDATE SET hash=excluded.hash")
            bind(s, 1, notebook); bind(s, 2, sourceId); bind(s, 3, hash); bind(s, 4, ISO8601DateFormatter().string(from: Date())); sqlite3_step(s); sqlite3_finalize(s)
            try exec("COMMIT")
        } catch { try? exec("ROLLBACK"); throw error }
    }

    /// A source this device ingested itself (Phase 3): its chunks as the chunker cut them, the summary chunk at -1 when
    /// there is one, its whole text for the reader, and a `sources` row so sync leaves it alone. Local notes are the
    /// ones without a `source_text` row; these have one, so they are listed as sources, not notes.
    public func addLocalSource(notebook: String, sourceId: String, title: String, format: String, text: String, url: String?, chunks: [LibraryChunk], file: String? = nil) throws {
        dropMatrix()
        try exec("BEGIN IMMEDIATE")
        do {
            let insert = try statement("""
                INSERT OR REPLACE INTO chunks(notebook_id, source_id, chunk_index, text, summary, filename, source_type, vector)
                VALUES (?,?,?,?,?,?,?,?)
                """)
            defer { sqlite3_finalize(insert) }
            for c in chunks {
                sqlite3_reset(insert)
                bind(insert, 1, notebook); bind(insert, 2, sourceId)
                sqlite3_bind_int64(insert, 3, Int64(c.chunkIndex))
                bind(insert, 4, c.text); sqlite3_bind_int(insert, 5, c.summary ? 1 : 0)
                bind(insert, 6, title); bind(insert, 7, c.sourceType)
                _ = c.vector.withUnsafeBytes { sqlite3_bind_blob(insert, 8, $0.baseAddress, Int32($0.count), Self.transient) }
                guard sqlite3_step(insert) == SQLITE_DONE else { throw LibraryError.sqlite(String(cString: sqlite3_errmsg(db))) }
            }
            let s = try statement("INSERT INTO sources(notebook_id, source_id, hash, created) VALUES (?,?,?,?) ON CONFLICT(notebook_id, source_id) DO UPDATE SET hash=excluded.hash")
            bind(s, 1, notebook); bind(s, 2, sourceId); bind(s, 3, "local"); bind(s, 4, ISO8601DateFormatter().string(from: Date())); sqlite3_step(s); sqlite3_finalize(s)
            let t = try statement("INSERT OR REPLACE INTO source_text(notebook_id, source_id, text, url, format, file) VALUES (?,?,?,?,?,?)")
            bind(t, 1, notebook); bind(t, 2, sourceId); bind(t, 3, text)
            if let url { bind(t, 4, url) } else { sqlite3_bind_null(t, 4) }
            bind(t, 5, format)
            if let file { bind(t, 6, file) } else { sqlite3_bind_null(t, 6) }
            sqlite3_step(t); sqlite3_finalize(t)
            try exec("COMMIT")
        } catch { try? exec("ROLLBACK"); throw error }
    }

    /// A note's picture is one extra chunk at this index, apart from its text chunks (0…).
    public static let pictureIndex = 1000

    public func localNotes() -> [LocalNote] {
        guard let s = try? statement("""
            SELECT n.notebook_id, n.source_id, n.filename, COALESCE(w.text, n.joined), n.kind, n.pic
            FROM (SELECT notebook_id, source_id, filename,
                         COALESCE(GROUP_CONCAT(CASE WHEN chunk_index < \(Self.pictureIndex) THEN text END, char(10)), '') AS joined,
                         MIN(source_type) AS kind, MAX(chunk_index >= \(Self.pictureIndex)) AS pic
                  FROM (SELECT * FROM chunks c WHERE c.source_id LIKE 'local:%'
                          AND NOT EXISTS (SELECT 1 FROM source_text t WHERE t.notebook_id = c.notebook_id AND t.source_id = c.source_id)
                        ORDER BY notebook_id, source_id, chunk_index)
                  GROUP BY notebook_id, source_id) n
            LEFT JOIN note_text w ON w.notebook_id = n.notebook_id AND w.source_id = n.source_id
            ORDER BY n.source_id DESC
            """) else { return [] }
        defer { sqlite3_finalize(s) }
        var out: [LocalNote] = []
        while sqlite3_step(s) == SQLITE_ROW {
            out.append(.init(id: String(cString: sqlite3_column_text(s, 1)), notebookId: String(cString: sqlite3_column_text(s, 0)),
                             title: String(cString: sqlite3_column_text(s, 2)), text: String(cString: sqlite3_column_text(s, 3)),
                             kind: String(cString: sqlite3_column_text(s, 4)), hasPicture: sqlite3_column_int(s, 5) != 0))
        }
        return out
    }

    /// Drop a note's text chunks but keep its picture (the Mac now holds the text).
    public func removeLocalNoteText(notebook: String, sourceId: String) throws {
        dropMatrix()
        let s = try statement("DELETE FROM chunks WHERE notebook_id=? AND source_id=? AND chunk_index < ?")
        bind(s, 1, notebook); bind(s, 2, sourceId); sqlite3_bind_int64(s, 3, Int64(Self.pictureIndex)); sqlite3_step(s); sqlite3_finalize(s)
        let w = try statement("DELETE FROM note_text WHERE notebook_id=? AND source_id=?")
        bind(w, 1, notebook); bind(w, 2, sourceId); sqlite3_step(w); sqlite3_finalize(w)
    }

    public func removeLocalNote(notebook: String, sourceId: String) throws {
        dropMatrix()
        for t in ["chunks", "sources", "source_text", "note_text"] {
            let s = try statement("DELETE FROM \(t) WHERE notebook_id=? AND source_id=?")
            bind(s, 1, notebook); bind(s, 2, sourceId); sqlite3_step(s); sqlite3_finalize(s)
        }
    }

    /// Changes the title shown for a source on this phone (every chunk carries the name).
    public func renameSource(notebook: String, sourceId: String, title: String) throws {
        let s = try statement("UPDATE chunks SET filename=? WHERE notebook_id=? AND source_id=?")
        bind(s, 1, title); bind(s, 2, notebook); bind(s, 3, sourceId); sqlite3_step(s); sqlite3_finalize(s)
    }

    // MARK: browsing a notebook's sources

    public struct SourceInfo: Sendable, Identifiable {
        public let id: String; public let notebookId: String; public let name: String; public let type: String
        public let pieces: Int; public let characters: Int; public let created: String
        /// The kept original (a path under the app's files, or an absolute path in a watched folder), when there is one.
        public var file: String? = nil
        public var isLocal: Bool { id.hasPrefix("local:") }
    }

    public func sources(notebook: String) -> [SourceInfo] {
        guard let s = try? statement("""
            SELECT c.source_id, MIN(c.filename), MIN(c.source_type), COUNT(*), SUM(LENGTH(c.text)), COALESCE(MAX(r.created), ''),
                   (SELECT t.file FROM source_text t WHERE t.notebook_id = c.notebook_id AND t.source_id = c.source_id)
            FROM chunks c LEFT JOIN sources r ON r.notebook_id = c.notebook_id AND r.source_id = c.source_id
            WHERE c.notebook_id=? AND c.summary=0 AND c.chunk_index < \(Self.pictureIndex)
            GROUP BY c.source_id ORDER BY COALESCE(MAX(r.created), '') DESC, MIN(c.filename) COLLATE NOCASE
            """) else { return [] }
        defer { sqlite3_finalize(s) }
        bind(s, 1, notebook)
        var out: [SourceInfo] = []
        while sqlite3_step(s) == SQLITE_ROW {
            let file = sqlite3_column_text(s, 6).map { String(cString: $0) }
            out.append(.init(id: String(cString: sqlite3_column_text(s, 0)), notebookId: notebook, name: String(cString: sqlite3_column_text(s, 1)),
                             type: String(cString: sqlite3_column_text(s, 2)), pieces: Int(sqlite3_column_int64(s, 3)), characters: Int(sqlite3_column_int64(s, 4)),
                             created: String(cString: sqlite3_column_text(s, 5)), file: file))
        }
        return out
    }

    // MARK: sources made on this device (library ownership, Phase 3)

    public struct LocalSourceInfo: Sendable {
        public let notebookId: String, sourceId: String, title: String, format: String, text: String
        public let url: String?, file: String?
    }

    /// Every source this device ingested itself (it has the whole text), for re-indexing.
    public func localSources() -> [LocalSourceInfo] {
        guard let s = try? statement("""
            SELECT t.notebook_id, t.source_id, COALESCE((SELECT MIN(filename) FROM chunks c WHERE c.notebook_id = t.notebook_id AND c.source_id = t.source_id), ''),
                   COALESCE(t.format, ''), t.text, t.url, t.file
            FROM source_text t WHERE t.source_id LIKE 'local:%' ORDER BY t.notebook_id, t.source_id
            """) else { return [] }
        defer { sqlite3_finalize(s) }
        var out: [LocalSourceInfo] = []
        while sqlite3_step(s) == SQLITE_ROW {
            out.append(.init(notebookId: String(cString: sqlite3_column_text(s, 0)), sourceId: String(cString: sqlite3_column_text(s, 1)),
                             title: String(cString: sqlite3_column_text(s, 2)), format: String(cString: sqlite3_column_text(s, 3)),
                             text: String(cString: sqlite3_column_text(s, 4)),
                             url: sqlite3_column_text(s, 5).map { String(cString: $0) }, file: sqlite3_column_text(s, 6).map { String(cString: $0) }))
        }
        return out
    }

    /// Drops a source's passages (the text and the row in `sources` stay), before they are made again.
    public func removeChunks(notebook: String, sourceId: String) throws {
        dropMatrix()
        let s = try statement("DELETE FROM chunks WHERE notebook_id=? AND source_id=?")
        bind(s, 1, notebook); bind(s, 2, sourceId); sqlite3_step(s); sqlite3_finalize(s)
    }

    public struct Counts: Sendable { public var notebooks = 0, sources = 0, chunks = 0, localSources = 0, emptyVectors = 0, watchedFolders = 0; public init() {} }

    /// What Settings › Library shows.
    public func counts() -> Counts {
        var c = Counts()
        func one(_ sql: String) -> Int {
            guard let s = try? statement(sql) else { return 0 }
            defer { sqlite3_finalize(s) }
            return sqlite3_step(s) == SQLITE_ROW ? Int(sqlite3_column_int64(s, 0)) : 0
        }
        c.notebooks = one("SELECT COUNT(*) FROM notebooks")
        c.sources = one("SELECT COUNT(*) FROM (SELECT DISTINCT notebook_id, source_id FROM chunks WHERE chunk_index < \(Self.pictureIndex))")
        c.chunks = one("SELECT COUNT(*) FROM chunks")
        c.localSources = one("SELECT COUNT(*) FROM source_text WHERE source_id LIKE 'local:%'")
        c.emptyVectors = one("SELECT COUNT(*) FROM chunks WHERE chunk_index < \(Self.pictureIndex) AND (LENGTH(vector) = 0 OR vector = zeroblob(LENGTH(vector)))")
        c.watchedFolders = one("SELECT COUNT(*) FROM watched_folders")
        return c
    }

    public struct EmptyVector: Sendable { public let notebookId: String, sourceId: String, chunkIndex: Int, text: String }

    /// Passages whose vector is missing or all zeros (a crashed or interrupted embedding), for repair.
    public func emptyVectors() -> [EmptyVector] {
        guard let s = try? statement("SELECT notebook_id, source_id, chunk_index, text FROM chunks WHERE chunk_index < \(Self.pictureIndex) AND (LENGTH(vector) = 0 OR vector = zeroblob(LENGTH(vector)))") else { return [] }
        defer { sqlite3_finalize(s) }
        var out: [EmptyVector] = []
        while sqlite3_step(s) == SQLITE_ROW {
            out.append(.init(notebookId: String(cString: sqlite3_column_text(s, 0)), sourceId: String(cString: sqlite3_column_text(s, 1)),
                             chunkIndex: Int(sqlite3_column_int64(s, 2)), text: String(cString: sqlite3_column_text(s, 3))))
        }
        return out
    }

    public func setVector(notebook: String, sourceId: String, chunkIndex: Int, vector: Data) throws {
        dropMatrix()
        let s = try statement("UPDATE chunks SET vector=? WHERE notebook_id=? AND source_id=? AND chunk_index=?")
        defer { sqlite3_finalize(s) }
        _ = vector.withUnsafeBytes { sqlite3_bind_blob(s, 1, $0.baseAddress, Int32($0.count), Self.transient) }
        bind(s, 2, notebook); bind(s, 3, sourceId); sqlite3_bind_int64(s, 4, Int64(chunkIndex))
        sqlite3_step(s)
    }

    // MARK: watched folders

    public struct WatchedFolder: Sendable, Identifiable {
        public let id: String, notebookId: String, path: String
        public let bookmark: Data?
        public let recursive: Bool
        public let added: String
        public var lastScan: String?, lastReport: String?
        public init(id: String, notebookId: String, path: String, bookmark: Data?, recursive: Bool, added: String, lastScan: String? = nil, lastReport: String? = nil) {
            self.id = id; self.notebookId = notebookId; self.path = path; self.bookmark = bookmark; self.recursive = recursive
            self.added = added; self.lastScan = lastScan; self.lastReport = lastReport
        }
    }

    public func watchedFolders() -> [WatchedFolder] {
        guard let s = try? statement("SELECT id, notebook_id, path, bookmark, recursive, added, last_scan, last_report FROM watched_folders ORDER BY added") else { return [] }
        defer { sqlite3_finalize(s) }
        var out: [WatchedFolder] = []
        while sqlite3_step(s) == SQLITE_ROW {
            var bookmark: Data?
            if let p = sqlite3_column_blob(s, 3) { bookmark = Data(bytes: p, count: Int(sqlite3_column_bytes(s, 3))) }
            out.append(.init(id: String(cString: sqlite3_column_text(s, 0)), notebookId: String(cString: sqlite3_column_text(s, 1)),
                             path: String(cString: sqlite3_column_text(s, 2)), bookmark: bookmark, recursive: sqlite3_column_int(s, 4) != 0,
                             added: String(cString: sqlite3_column_text(s, 5)),
                             lastScan: sqlite3_column_text(s, 6).map { String(cString: $0) }, lastReport: sqlite3_column_text(s, 7).map { String(cString: $0) }))
        }
        return out
    }

    public func addWatchedFolder(_ f: WatchedFolder) throws {
        let s = try statement("INSERT OR REPLACE INTO watched_folders(id, notebook_id, path, bookmark, recursive, added) VALUES (?,?,?,?,?,?)")
        defer { sqlite3_finalize(s) }
        bind(s, 1, f.id); bind(s, 2, f.notebookId); bind(s, 3, f.path)
        if let b = f.bookmark { _ = b.withUnsafeBytes { sqlite3_bind_blob(s, 4, $0.baseAddress, Int32($0.count), Self.transient) } } else { sqlite3_bind_null(s, 4) }
        sqlite3_bind_int(s, 5, f.recursive ? 1 : 0); bind(s, 6, f.added)
        sqlite3_step(s)
    }

    /// Stops watching; the sources already made from the folder stay.
    public func removeWatchedFolder(id: String) throws {
        for t in ["watched_folders", "watched_files"] {
            let s = try statement("DELETE FROM \(t) WHERE \(t == "watched_folders" ? "id" : "folder_id")=?")
            bind(s, 1, id); sqlite3_step(s); sqlite3_finalize(s)
        }
    }

    public func setWatchedScan(id: String, at: String, report: String) throws {
        let s = try statement("UPDATE watched_folders SET last_scan=?, last_report=? WHERE id=?")
        bind(s, 1, at); bind(s, 2, report); bind(s, 3, id); sqlite3_step(s); sqlite3_finalize(s)
    }

    public struct WatchedFile: Sendable { public let mtime: Double, size: Int, sourceId: String? }

    /// The ledger: what the folder held at the last scan, by path.
    public func watchedFiles(folderId: String) -> [String: WatchedFile] {
        guard let s = try? statement("SELECT path, mtime, size, source_id FROM watched_files WHERE folder_id=?") else { return [:] }
        defer { sqlite3_finalize(s) }
        bind(s, 1, folderId)
        var out: [String: WatchedFile] = [:]
        while sqlite3_step(s) == SQLITE_ROW {
            out[String(cString: sqlite3_column_text(s, 0))] = .init(mtime: sqlite3_column_double(s, 1), size: Int(sqlite3_column_int64(s, 2)),
                                                                    sourceId: sqlite3_column_text(s, 3).map { String(cString: $0) })
        }
        return out
    }

    public func recordWatchedFile(folderId: String, path: String, mtime: Double, size: Int, sourceId: String?) throws {
        let s = try statement("INSERT OR REPLACE INTO watched_files(folder_id, path, mtime, size, source_id) VALUES (?,?,?,?,?)")
        defer { sqlite3_finalize(s) }
        bind(s, 1, folderId); bind(s, 2, path); sqlite3_bind_double(s, 3, mtime); sqlite3_bind_int64(s, 4, Int64(size))
        if let sourceId { bind(s, 5, sourceId) } else { sqlite3_bind_null(s, 5) }
        sqlite3_step(s)
    }

    /// Records when the Mac added each source (does not touch the chunks or the hash).
    public func setCreated(notebook: String, dates: [String: String]) throws {
        try exec("BEGIN IMMEDIATE")
        do {
            let s = try statement("UPDATE sources SET created=? WHERE notebook_id=? AND source_id=?")
            defer { sqlite3_finalize(s) }
            for (sid, d) in dates where !d.isEmpty {
                sqlite3_reset(s)
                bind(s, 1, d); bind(s, 2, notebook); bind(s, 3, sid)
                sqlite3_step(s)
            }
            try exec("COMMIT")
        } catch { try? exec("ROLLBACK"); throw error }
    }

    /// One piece of a source (nil when there is none at that index).
    public func chunkText(notebook: String, sourceId: String, index: Int) -> String? {
        guard index >= 0, index < Self.pictureIndex,
              let s = try? statement("SELECT text FROM chunks WHERE notebook_id=? AND source_id=? AND chunk_index=? AND summary=0") else { return nil }
        defer { sqlite3_finalize(s) }
        bind(s, 1, notebook); bind(s, 2, sourceId); sqlite3_bind_int64(s, 3, Int64(index))
        return sqlite3_step(s) == SQLITE_ROW ? String(cString: sqlite3_column_text(s, 0)) : nil
    }

    /// A source's text: the whole of it when the import brought it, otherwise its pieces in order.
    public func sourceText(notebook: String, sourceId: String) -> String {
        if let full = try? statement("SELECT text FROM source_text WHERE notebook_id=? AND source_id=?") {
            defer { sqlite3_finalize(full) }
            bind(full, 1, notebook); bind(full, 2, sourceId)
            if sqlite3_step(full) == SQLITE_ROW {
                let text = String(cString: sqlite3_column_text(full, 0))
                if !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return text }
            }
        }
        guard let s = try? statement("SELECT text FROM chunks WHERE notebook_id=? AND source_id=? AND summary=0 AND chunk_index < \(Self.pictureIndex) ORDER BY chunk_index") else { return "" }
        defer { sqlite3_finalize(s) }
        bind(s, 1, notebook); bind(s, 2, sourceId)
        var parts: [String] = []
        while sqlite3_step(s) == SQLITE_ROW { parts.append(String(cString: sqlite3_column_text(s, 0))) }
        return parts.joined(separator: "\n\n")
    }

    public func chunkCount() -> Int {
        guard let s = try? statement("SELECT COUNT(*) FROM chunks") else { return 0 }
        defer { sqlite3_finalize(s) }
        return sqlite3_step(s) == SQLITE_ROW ? Int(sqlite3_column_int64(s, 0)) : 0
    }

    // MARK: changes (always whole-notebook, atomic)

    public func wipe() throws {
        dropMatrix()
        for t in ["chunks", "sources", "notebooks", "source_text", "note_text", "imported_notes", "topics"] { try exec("DELETE FROM \(t)") }
        try exec("DELETE FROM meta WHERE key LIKE 'personal:%'")        // profile, memory, entities: all of it goes
        try exec("DELETE FROM meta WHERE key LIKE 'imported:%'")
        try exec("DELETE FROM meta WHERE key LIKE 'untagged:%'")
    }

    public func removeNotebooks(except keep: Set<String>) throws {
        dropMatrix()
        for n in notebooks() where !keep.contains(n.id) {
            for t in ["chunks", "sources", "source_text", "imported_notes", "topics"] {
                let s = try statement("DELETE FROM \(t) WHERE notebook_id=?")
                bind(s, 1, n.id); sqlite3_step(s); sqlite3_finalize(s)
            }
            let s = try statement("DELETE FROM notebooks WHERE id=?")
            bind(s, 1, n.id); sqlite3_step(s); sqlite3_finalize(s)
        }
        // The Mac's profile, core memory, entities and relationships are kept per notebook (meta "personal:<id>").
        // They go with the notebook, and any left over from an earlier sync of one that is now off-limits too.
        let keys = try statement("SELECT key FROM meta WHERE key LIKE 'personal:%'")
        var stale: [String] = []
        while sqlite3_step(keys) == SQLITE_ROW {
            let key = String(cString: sqlite3_column_text(keys, 0))
            if !keep.contains(String(key.dropFirst("personal:".count))) { stale.append(key) }
        }
        sqlite3_finalize(keys)
        for key in stale {
            let d = try statement("DELETE FROM meta WHERE key=?")
            bind(d, 1, key); sqlite3_step(d); sqlite3_finalize(d)
        }
    }

    /// Apply one notebook's changes in a single transaction: drop `removed` and every source being
    /// re-sent, insert the new chunks, record the sources' hashes and the notebook's digest.
    public func apply(notebook: String, title: String, digest: String, removed: [String],
               resent: [String], chunks: [LibraryChunk], hashes: [String: String]) throws {
        dropMatrix()
        try exec("BEGIN IMMEDIATE")
        do {
            for sid in removed + resent {
                for t in ["chunks", "sources", "source_text"] {
                    let s = try statement("DELETE FROM \(t) WHERE notebook_id=? AND source_id=?")
                    bind(s, 1, notebook); bind(s, 2, sid); sqlite3_step(s); sqlite3_finalize(s)
                }
            }
            let insert = try statement("""
                INSERT OR REPLACE INTO chunks(notebook_id, source_id, chunk_index, text, summary, filename, source_type, vector)
                VALUES (?,?,?,?,?,?,?,?)
                """)
            defer { sqlite3_finalize(insert) }
            for c in chunks {
                sqlite3_reset(insert)
                bind(insert, 1, c.notebookId); bind(insert, 2, c.sourceId)
                sqlite3_bind_int64(insert, 3, Int64(c.chunkIndex))
                bind(insert, 4, c.text); sqlite3_bind_int(insert, 5, c.summary ? 1 : 0)
                bind(insert, 6, c.filename); bind(insert, 7, c.sourceType)
                _ = c.vector.withUnsafeBytes { sqlite3_bind_blob(insert, 8, $0.baseAddress, Int32($0.count), Self.transient) }
                guard sqlite3_step(insert) == SQLITE_DONE else { throw LibraryError.sqlite(String(cString: sqlite3_errmsg(db))) }
            }
            for (sid, h) in hashes {
                let s = try statement("INSERT OR REPLACE INTO sources(notebook_id, source_id, hash) VALUES (?,?,?)")
                bind(s, 1, notebook); bind(s, 2, sid); bind(s, 3, h); sqlite3_step(s); sqlite3_finalize(s)
            }
            // "partial" marks a notebook whose copy is not finished: a notebook that already has a digest keeps it
            // (it is still usable), a new one stays hidden until its last batch writes the real digest.
            let s = try statement("""
                INSERT INTO notebooks(id, title, digest) VALUES (?,?,?)
                ON CONFLICT(id) DO UPDATE SET title=excluded.title,
                  digest=CASE WHEN excluded.digest='partial' THEN notebooks.digest ELSE excluded.digest END
                """)
            bind(s, 1, notebook); bind(s, 2, title); bind(s, 3, digest); sqlite3_step(s); sqlite3_finalize(s)
            try exec("COMMIT")
        } catch {
            try? exec("ROLLBACK")
            throw error
        }
    }

    // MARK: search

    /// Build the in-memory vector block for a notebook now, so its first question does not pay for it.
    public func warmUp(notebook: String?) { if let notebook { try? loadMatrix(notebook) } }

    /// The notebook whose best chunk matches `query` most closely. Loads one notebook at a time so memory never
    /// holds more than the largest one, and leaves nothing loaded afterwards.
    public func closestNotebook(to query: [Float]) throws -> String? {
        guard query.count == Self.dims else { return nil }
        var best: (id: String, score: Float)?
        for n in notebooks() {
            try loadMatrix(n.id)
            let rows = rowKeys.count
            guard rows > 0 else { continue }
            var scores = [Float](repeating: 0, count: rows)
            matrix.withUnsafeBufferPointer { m in
                vDSP_mmul(m.baseAddress!, 1, query, 1, &scores, 1, vDSP_Length(rows), 1, vDSP_Length(Self.dims))
            }
            if let top = scores.max(), top > (best?.score ?? -Float.infinity) { best = (n.id, top) }
        }
        dropMatrix()
        return best?.id
    }

    // MARK: BM25, exactly as the Mac's rank_bm25 BM25Okapi (lower-cased whitespace tokens)

    private struct BM25Index {
        var postings: [String: [(doc: Int32, tf: Int32)]] = [:]
        var docLength: [Int32] = []
        var idf: [String: Double] = [:]
        var averageLength = 0.0
    }
    private var bm25: [String: BM25Index] = [:]

    private func bm25Index(for notebook: String, epsilon: Double) throws -> BM25Index {
        if let hit = bm25[notebook] { return hit }
        let s = try statement("SELECT text FROM chunks WHERE notebook_id=? ORDER BY source_id, chunk_index")
        defer { sqlite3_finalize(s) }
        bind(s, 1, notebook)
        var index = BM25Index()
        var docFreq: [String: Int] = [:]
        var doc: Int32 = 0
        while sqlite3_step(s) == SQLITE_ROW {
            let tokens = String(cString: sqlite3_column_text(s, 0)).lowercased()
                .split(whereSeparator: \.isWhitespace).map(String.init)
            var tf: [String: Int32] = [:]
            for t in tokens { tf[t, default: 0] += 1 }
            for (t, n) in tf { index.postings[t, default: []].append((doc, n)); docFreq[t, default: 0] += 1 }
            index.docLength.append(Int32(tokens.count))
            doc += 1
        }
        let n = Double(doc)
        index.averageLength = n > 0 ? Double(index.docLength.reduce(0) { $0 + Int($1) }) / n : 0
        var idfSum = 0.0
        var negative: [String] = []
        for (t, freq) in docFreq {
            let v = log(n - Double(freq) + 0.5) - log(Double(freq) + 0.5)
            index.idf[t] = v
            idfSum += v
            if v < 0 { negative.append(t) }
        }
        let floor = epsilon * (docFreq.isEmpty ? 0 : idfSum / Double(docFreq.count))
        for t in negative { index.idf[t] = floor }
        bm25[notebook] = index
        return index
    }

    /// The `limit` best documents for the query: (document index within the notebook, score), best first.
    private func bm25Top(_ index: BM25Index, query: String, k1: Double, b: Double, limit: Int) -> [(doc: Int, score: Double)] {
        var scores: [Int32: Double] = [:]
        for term in query.lowercased().split(whereSeparator: \.isWhitespace).map(String.init) {
            guard let idf = index.idf[term], let list = index.postings[term] else { continue }
            for (doc, tf) in list {
                let f = Double(tf)
                let norm = f + k1 * (1 - b + b * Double(index.docLength[Int(doc)]) / max(index.averageLength, 1e-9))
                scores[doc, default: 0] += idf * (f * (k1 + 1) / norm)
            }
        }
        return scores.sorted { $0.value != $1.value ? $0.value > $1.value : $0.key < $1.key }.prefix(limit).map { (Int($0.key), $0.value) }
    }

    public struct SearchParams: Sendable {
        public var vectorCandidates = 48
        public var bm25Candidates = 48
        public var hybridK = 24
        public var rrfK = 60.0
        public var missingRank = 1000.0
        public var k1 = 1.5, b = 0.75, epsilon = 0.25
        public init() {}
        public init(vectorCandidates: Int = 48, bm25Candidates: Int = 48, hybridK: Int = 24, rrfK: Double = 60.0,
                    missingRank: Double = 1000.0, k1: Double = 1.5, b: Double = 0.75, epsilon: Double = 0.25) {
            self.vectorCandidates = vectorCandidates; self.bm25Candidates = bm25Candidates; self.hybridK = hybridK
            self.rrfK = rrfK; self.missingRank = missingRank; self.k1 = k1; self.b = b; self.epsilon = epsilon
        }
    }

    /// Hybrid retrieval exactly as the Mac's `hybrid_search`: the nearest vectors and the best BM25 documents
    /// (each `…Candidates` long), fused by Reciprocal Rank Fusion (0-based ranks, `missingRank` when absent), the
    /// best `hybridK` returned. `queryText` nil means vectors only (used to find a question's notebook).
    public func search(query: [Float], queryText: String? = nil, params: SearchParams = SearchParams(), notebook: String? = nil)
        throws -> (hits: [LibraryHit], scanned: Int, seconds: Double) {
        let started = Date()
        guard let notebook else { return ([], 0, 0) }          // one notebook at a time (see closestNotebook for "which one")
        try loadMatrix(notebook)
        let range = 0..<rowKeys.count
        let rows = range.count
        guard rows > 0, query.count == Self.dims else { return ([], 0, Date().timeIntervalSince(started)) }
        var scores = [Float](repeating: 0, count: rows)
        matrix.withUnsafeBufferPointer { m in
            vDSP_mmul(m.baseAddress! + range.lowerBound * Self.dims, 1, query, 1, &scores, 1, vDSP_Length(rows), 1, vDSP_Length(Self.dims))
        }
        let order = (0..<rows).sorted { scores[$0] > scores[$1] }
        var vectorRank: [Int: Double] = [:]
        for (rank, row) in order.prefix(params.vectorCandidates).enumerated() { vectorRank[row] = Double(rank) }
        var keywordRank: [Int: Double] = [:]
        if let queryText {
            let index = try bm25Index(for: notebook, epsilon: params.epsilon)
            for (rank, hit) in bm25Top(index, query: queryText, k1: params.k1, b: params.b, limit: params.bm25Candidates).enumerated() {
                keywordRank[hit.doc] = Double(rank)
            }
        }
        var fused: [(row: Int, rrf: Double)] = []
        for row in Set(vectorRank.keys).union(keywordRank.keys) {
            let v = vectorRank[row] ?? params.missingRank, k = keywordRank[row] ?? params.missingRank
            fused.append((row, 1 / (params.rrfK + v) + 1 / (params.rrfK + k)))
        }
        fused.sort { $0.rrf != $1.rrf ? $0.rrf > $1.rrf : $0.row < $1.row }      // ties: stable and repeatable (the Mac's order there is random)
        let best = fused.first?.rrf ?? 1
        let titles = Dictionary(uniqueKeysWithValues: notebooks().map { ($0.id, $0.title) })
        var hits: [LibraryHit] = []
        for item in fused.prefix(params.hybridK) {
            let key = rowKeys[item.row]
            let t = try statement("SELECT text, filename, source_type FROM chunks WHERE notebook_id=? AND source_id=? AND chunk_index=?")
            defer { sqlite3_finalize(t) }
            bind(t, 1, notebook); bind(t, 2, key.src); sqlite3_bind_int64(t, 3, Int64(key.idx))
            guard sqlite3_step(t) == SQLITE_ROW else { continue }
            hits.append(.init(notebookId: notebook, notebookTitle: titles[notebook] ?? notebook, sourceId: key.src, chunkIndex: key.idx,
                              filename: String(cString: sqlite3_column_text(t, 1)), text: String(cString: sqlite3_column_text(t, 0)),
                              score: scores[item.row], rrf: Float(item.rrf / best),
                              sourceType: String(cString: sqlite3_column_text(t, 2))))
        }
        return (hits, rows, Date().timeIntervalSince(started))
    }
}
