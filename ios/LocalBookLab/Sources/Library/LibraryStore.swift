import Foundation
import Accelerate
import SQLite3

/// The phone's copy of the Mac's library: chunk text and the Mac's own vectors (float16). Read-only
/// from the phone's point of view — it is replaced by sync, never edited. At this size (about 11,000
/// chunks) a plain scan over the vectors answers a question in milliseconds, so there is no vector index.
struct LibraryChunk: Sendable {
    let notebookId: String
    let sourceId: String
    let chunkIndex: Int
    let text: String
    let summary: Bool
    let filename: String
    let sourceType: String
    let vector: Data            // 768 × float16, little endian
}

struct LibraryHit: Sendable, Identifiable {
    var id: String { "\(notebookId)/\(sourceId)/\(chunkIndex)" }
    let notebookId: String
    let notebookTitle: String
    let sourceId: String
    let chunkIndex: Int
    let filename: String
    let text: String
    let score: Float
}

struct LibraryNotebookInfo: Sendable, Identifiable {
    let id: String
    let title: String
    let digest: String
    let chunks: Int
}

enum LibraryError: LocalizedError {
    case sqlite(String)
    case embedderMismatch(String)
    case notPaired
    case badResponse(String)
    var errorDescription: String? {
        switch self {
        case .sqlite(let m): "Library database: \(m)"
        case .embedderMismatch(let m): m
        case .notPaired: "Pair with a Mac first (row 3)."
        case .badResponse(let m): "Unexpected reply from the Mac: \(m)"
        }
    }
}

actor LibraryStore {
    static let dims = 768
    private var db: OpaquePointer?
    /// Every vector as Float32 in one block (about 34 MB for 11,000 chunks), ordered by notebook so a
    /// notebook is one contiguous range. Built on the first search; dropped whenever the data changes.
    private var matrix: [Float] = []
    private var rowKeys: [(nb: String, src: String, idx: Int)] = []
    private var ranges: [String: Range<Int>] = [:]
    private var matrixReady = false

    private func dropMatrix() { matrix = []; rowKeys = []; ranges = [:]; matrixReady = false }

    private func buildMatrix() throws {
        guard !matrixReady else { return }
        let n = chunkCount()
        var m = [Float](repeating: 0, count: n * Self.dims)
        var keys: [(nb: String, src: String, idx: Int)] = []
        keys.reserveCapacity(n)
        let s = try statement("SELECT notebook_id, source_id, chunk_index, vector FROM chunks ORDER BY notebook_id, source_id, chunk_index")
        defer { sqlite3_finalize(s) }
        var row = 0
        var ranges: [String: Range<Int>] = [:]
        while sqlite3_step(s) == SQLITE_ROW, row < n {
            guard let blob = sqlite3_column_blob(s, 3), sqlite3_column_bytes(s, 3) == Self.dims * 2 else { continue }
            let halves = blob.assumingMemoryBound(to: Float16.self)
            let base = row * Self.dims
            for i in 0..<Self.dims { m[base + i] = Float(halves[i]) }
            let nb = String(cString: sqlite3_column_text(s, 0))
            keys.append((nb, String(cString: sqlite3_column_text(s, 1)), Int(sqlite3_column_int64(s, 2))))
            if let r = ranges[nb] { ranges[nb] = r.lowerBound..<(row + 1) } else { ranges[nb] = row..<(row + 1) }
            row += 1
        }
        matrix = m; rowKeys = keys; self.ranges = ranges; matrixReady = true
    }

    init(path: URL? = nil) throws {
        let url = path ?? Self.defaultURL()
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        var handle: OpaquePointer?
        guard sqlite3_open_v2(url.path, &handle, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_FULLMUTEX, nil) == SQLITE_OK else {
            throw LibraryError.sqlite("could not open \(url.lastPathComponent)")
        }
        db = handle
    }

    /// Create the tables (idempotent). Separate from `init` because an actor's init cannot call its own methods.
    func prepare() throws {
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
            // Keyword index (BM25) beside the vectors: the Mac ranks by BOTH and fuses them, so must the phone.
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
              text, notebook_id UNINDEXED, source_id UNINDEXED, chunk_index UNINDEXED, tokenize='porter unicode61')
            """,
        ] { try exec(sql) }
        // A library synced before the keyword index existed: fill it from the stored chunks, once.
        if let s = try? statement("SELECT (SELECT COUNT(*) FROM chunks_fts), (SELECT COUNT(*) FROM chunks)") {
            defer { sqlite3_finalize(s) }
            if sqlite3_step(s) == SQLITE_ROW, sqlite3_column_int64(s, 0) == 0, sqlite3_column_int64(s, 1) > 0 {
                try exec("INSERT INTO chunks_fts(text, notebook_id, source_id, chunk_index) SELECT text, notebook_id, source_id, chunk_index FROM chunks")
            }
        }
    }

    // No deinit: the store lives as long as the app, and SQLite closes the file with the process.

    static func defaultURL() -> URL {
        URL.applicationSupportDirectory.appending(path: "Library/library.sqlite")
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

    func meta(_ key: String) -> String? {
        guard let s = try? statement("SELECT value FROM meta WHERE key=?") else { return nil }
        defer { sqlite3_finalize(s) }
        bind(s, 1, key)
        return sqlite3_step(s) == SQLITE_ROW ? String(cString: sqlite3_column_text(s, 0)) : nil
    }

    func setMeta(_ key: String, _ value: String) throws {
        let s = try statement("INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)")
        defer { sqlite3_finalize(s) }
        bind(s, 1, key); bind(s, 2, value)
        guard sqlite3_step(s) == SQLITE_DONE else { throw LibraryError.sqlite(String(cString: sqlite3_errmsg(db))) }
    }

    // MARK: what is here

    func notebooks() -> [LibraryNotebookInfo] {
        guard let s = try? statement("""
            SELECT n.id, n.title, n.digest, (SELECT COUNT(*) FROM chunks c WHERE c.notebook_id = n.id)
            FROM notebooks n ORDER BY n.title COLLATE NOCASE
            """) else { return [] }
        defer { sqlite3_finalize(s) }
        var out: [LibraryNotebookInfo] = []
        while sqlite3_step(s) == SQLITE_ROW {
            out.append(.init(id: String(cString: sqlite3_column_text(s, 0)), title: String(cString: sqlite3_column_text(s, 1)),
                             digest: String(cString: sqlite3_column_text(s, 2)), chunks: Int(sqlite3_column_int64(s, 3))))
        }
        return out
    }

    func sourceHashes(notebook: String) -> [String: String] {
        guard let s = try? statement("SELECT source_id, hash FROM sources WHERE notebook_id=?") else { return [:] }
        defer { sqlite3_finalize(s) }
        bind(s, 1, notebook)
        var out: [String: String] = [:]
        while sqlite3_step(s) == SQLITE_ROW {
            out[String(cString: sqlite3_column_text(s, 0))] = String(cString: sqlite3_column_text(s, 1))
        }
        return out
    }

    func chunkCount() -> Int {
        guard let s = try? statement("SELECT COUNT(*) FROM chunks") else { return 0 }
        defer { sqlite3_finalize(s) }
        return sqlite3_step(s) == SQLITE_ROW ? Int(sqlite3_column_int64(s, 0)) : 0
    }

    // MARK: changes (always whole-notebook, atomic)

    func wipe() throws {
        dropMatrix()
        for t in ["chunks", "sources", "notebooks", "chunks_fts"] { try exec("DELETE FROM \(t)") }
    }

    func removeNotebooks(except keep: Set<String>) throws {
        dropMatrix()
        for n in notebooks() where !keep.contains(n.id) {
            for t in ["chunks", "sources", "chunks_fts"] {
                let s = try statement("DELETE FROM \(t) WHERE notebook_id=?")
                bind(s, 1, n.id); sqlite3_step(s); sqlite3_finalize(s)
            }
            let s = try statement("DELETE FROM notebooks WHERE id=?")
            bind(s, 1, n.id); sqlite3_step(s); sqlite3_finalize(s)
        }
    }

    /// Apply one notebook's changes in a single transaction: drop `removed` and every source being
    /// re-sent, insert the new chunks, record the sources' hashes and the notebook's digest.
    func apply(notebook: String, title: String, digest: String, removed: [String],
               resent: [String], chunks: [LibraryChunk], hashes: [String: String]) throws {
        dropMatrix()
        try exec("BEGIN IMMEDIATE")
        do {
            for sid in removed + resent {
                for t in ["chunks", "sources", "chunks_fts"] {
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
                c.vector.withUnsafeBytes { sqlite3_bind_blob(insert, 8, $0.baseAddress, Int32($0.count), Self.transient) }
                guard sqlite3_step(insert) == SQLITE_DONE else { throw LibraryError.sqlite(String(cString: sqlite3_errmsg(db))) }
            }
            let fts = try statement("INSERT INTO chunks_fts(text, notebook_id, source_id, chunk_index) VALUES (?,?,?,?)")
            defer { sqlite3_finalize(fts) }
            for c in chunks {
                sqlite3_reset(fts)
                bind(fts, 1, c.text); bind(fts, 2, c.notebookId); bind(fts, 3, c.sourceId)
                sqlite3_bind_int64(fts, 4, Int64(c.chunkIndex))
                guard sqlite3_step(fts) == SQLITE_DONE else { throw LibraryError.sqlite(String(cString: sqlite3_errmsg(db))) }
            }
            for (sid, h) in hashes {
                let s = try statement("INSERT OR REPLACE INTO sources(notebook_id, source_id, hash) VALUES (?,?,?)")
                bind(s, 1, notebook); bind(s, 2, sid); bind(s, 3, h); sqlite3_step(s); sqlite3_finalize(s)
            }
            let s = try statement("INSERT OR REPLACE INTO notebooks(id, title, digest) VALUES (?,?,?)")
            bind(s, 1, notebook); bind(s, 2, title); bind(s, 3, digest); sqlite3_step(s); sqlite3_finalize(s)
            try exec("COMMIT")
        } catch {
            try? exec("ROLLBACK")
            throw error
        }
    }

    // MARK: search

    private static let stopwords: Set<String> = ["a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "for", "from", "how",
        "in", "is", "it", "of", "on", "or", "the", "to", "was", "what", "when", "where", "which", "who", "why", "with", "can", "you"]

    /// Keyword ranking (BM25) inside one notebook: the best `limit` chunks for the question's content words.
    private func keywordRank(_ text: String, notebook: String?, limit: Int) -> [(nb: String, src: String, idx: Int)] {
        let words = text.lowercased().split(whereSeparator: { !$0.isLetter && !$0.isNumber })
            .map(String.init).filter { $0.count > 1 && !Self.stopwords.contains($0) }
        guard !words.isEmpty else { return [] }
        let match = words.map { "\"\($0)\"" }.joined(separator: " OR ")
        let sql = "SELECT notebook_id, source_id, chunk_index FROM chunks_fts WHERE chunks_fts MATCH ?"
            + (notebook == nil ? "" : " AND notebook_id = ?") + " ORDER BY bm25(chunks_fts) LIMIT \(limit)"
        guard let s = try? statement(sql) else { return [] }
        defer { sqlite3_finalize(s) }
        bind(s, 1, match)
        if let notebook { bind(s, 2, notebook) }
        var out: [(nb: String, src: String, idx: Int)] = []
        while sqlite3_step(s) == SQLITE_ROW {
            out.append((String(cString: sqlite3_column_text(s, 0)), String(cString: sqlite3_column_text(s, 1)),
                        Int(sqlite3_column_int64(s, 2))))
        }
        return out
    }

    /// Hybrid retrieval like the Mac's: the Mac's vectors (one Accelerate matrix-vector product) and a
    /// BM25 keyword ranking, fused by Reciprocal Rank Fusion. `queryText` nil means vectors only.
    func search(query: [Float], queryText: String? = nil, topK: Int, notebook: String? = nil)
        throws -> (hits: [LibraryHit], scanned: Int, seconds: Double) {
        let started = Date()
        try buildMatrix()
        let range = notebook.flatMap { ranges[$0] } ?? (notebook == nil ? 0..<rowKeys.count : 0..<0)
        let rows = range.count
        guard rows > 0, query.count == Self.dims else { return ([], 0, Date().timeIntervalSince(started)) }
        var scores = [Float](repeating: 0, count: rows)
        matrix.withUnsafeBufferPointer { m in
            cblas_sgemv(CblasRowMajor, CblasNoTrans, Int32(rows), Int32(Self.dims), 1,
                        m.baseAddress! + range.lowerBound * Self.dims, Int32(Self.dims), query, 1, 0, &scores, 1)
        }
        let order = (0..<rows).sorted { scores[$0] > scores[$1] }
        let candidates = 50
        // Reciprocal Rank Fusion, k = 60: score = Σ 1 / (60 + rank) over each ranking the chunk appears in.
        struct Key: Hashable { let nb: String; let src: String; let idx: Int }
        var fused: [Key: Float] = [:]
        var dense: [Key: Float] = [:]
        for (rank, o) in order.prefix(candidates).enumerated() {
            let r = rowKeys[range.lowerBound + o]
            let k = Key(nb: r.nb, src: r.src, idx: r.idx)
            fused[k, default: 0] += 1 / Float(60 + rank + 1)
            dense[k] = scores[o]
        }
        if let queryText {
            for (rank, r) in keywordRank(queryText, notebook: notebook, limit: candidates).enumerated() {
                fused[Key(nb: r.nb, src: r.src, idx: r.idx), default: 0] += 1 / Float(60 + rank + 1)
            }
        }
        let titles = Dictionary(uniqueKeysWithValues: notebooks().map { ($0.id, $0.title) })
        var hits: [LibraryHit] = []
        for (key, _) in fused.sorted(by: { $0.value > $1.value }).prefix(topK) {
            let t = try statement("SELECT text, filename, vector FROM chunks WHERE notebook_id=? AND source_id=? AND chunk_index=?")
            defer { sqlite3_finalize(t) }
            bind(t, 1, key.nb); bind(t, 2, key.src); sqlite3_bind_int64(t, 3, Int64(key.idx))
            guard sqlite3_step(t) == SQLITE_ROW else { continue }
            var sim = dense[key] ?? 0
            if dense[key] == nil, let blob = sqlite3_column_blob(t, 2), sqlite3_column_bytes(t, 2) == Self.dims * 2 {
                let h = blob.assumingMemoryBound(to: Float16.self)
                sim = 0
                for i in 0..<Self.dims { sim += Float(h[i]) * query[i] }
            }
            hits.append(.init(notebookId: key.nb, notebookTitle: titles[key.nb] ?? key.nb, sourceId: key.src, chunkIndex: key.idx,
                              filename: String(cString: sqlite3_column_text(t, 1)), text: String(cString: sqlite3_column_text(t, 0)),
                              score: sim))
        }
        return (hits, rows, Date().timeIntervalSince(started))
    }
}
