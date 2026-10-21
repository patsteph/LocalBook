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
    var rrf: Float = 0              // Reciprocal Rank Fusion score divided by the best one in the pool (0…1)
    var sourceType: String = ""
}

struct LibraryNotebookInfo: Sendable, Identifiable {
    let id: String
    let title: String
    let digest: String
    let chunks: Int
    var section: String? = nil       // the desktop's section for it, if any
    var color: String? = nil         // the desktop's notebook color (hex)
    var sectionCollapsed = false     // whether the desktop shows its section folded
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
        ] { try exec(sql) }
        // Keyword ranking is exact BM25 computed in memory (see bm25Index); the SQLite full-text table an earlier
        // build kept beside the chunks is not used any more, so drop it and its space.
        try? exec("DROP TABLE IF EXISTS chunks_fts")
        // The desktop's order and sections for notebooks (older stores lack the columns).
        try? exec("ALTER TABLE notebooks ADD COLUMN position INTEGER NOT NULL DEFAULT 0")
        try? exec("ALTER TABLE notebooks ADD COLUMN section TEXT")
        try? exec("ALTER TABLE notebooks ADD COLUMN color TEXT")
        try? exec("ALTER TABLE notebooks ADD COLUMN collapsed INTEGER NOT NULL DEFAULT 0")
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
    func setLayout(_ items: [(id: String, position: Int, section: String?, color: String?, collapsed: Bool)]) throws {
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

    // MARK: notes made on this phone (source ids start with LocalNotes.prefix; sync never touches them)

    struct LocalNote: Sendable, Identifiable { let id: String; let notebookId: String; let title: String; let text: String; let kind: String; var hasPicture = false }

    func addLocalNote(notebook: String, sourceId: String, title: String, kind: String, chunks: [LibraryChunk], hash: String) throws {
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
                bind(insert, 1, c.notebookId); bind(insert, 2, c.sourceId)
                sqlite3_bind_int64(insert, 3, Int64(c.chunkIndex))
                bind(insert, 4, c.text); sqlite3_bind_int(insert, 5, 0)
                bind(insert, 6, c.filename); bind(insert, 7, kind)
                c.vector.withUnsafeBytes { sqlite3_bind_blob(insert, 8, $0.baseAddress, Int32($0.count), Self.transient) }
                guard sqlite3_step(insert) == SQLITE_DONE else { throw LibraryError.sqlite(String(cString: sqlite3_errmsg(db))) }
            }
            let s = try statement("INSERT OR REPLACE INTO sources(notebook_id, source_id, hash) VALUES (?,?,?)")
            bind(s, 1, notebook); bind(s, 2, sourceId); bind(s, 3, hash); sqlite3_step(s); sqlite3_finalize(s)
            try exec("COMMIT")
        } catch { try? exec("ROLLBACK"); throw error }
    }

    /// A note's picture is one extra chunk at this index, apart from its text chunks (0…).
    static let pictureIndex = 1000

    func localNotes() -> [LocalNote] {
        guard let s = try? statement("""
            SELECT notebook_id, source_id, filename,
                   COALESCE(GROUP_CONCAT(CASE WHEN chunk_index < \(Self.pictureIndex) THEN text END, char(10)), ''),
                   MIN(source_type), MAX(chunk_index >= \(Self.pictureIndex))
            FROM (SELECT * FROM chunks WHERE source_id LIKE 'local:%' ORDER BY notebook_id, source_id, chunk_index)
            GROUP BY notebook_id, source_id ORDER BY source_id DESC
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
    func removeLocalNoteText(notebook: String, sourceId: String) throws {
        dropMatrix()
        let s = try statement("DELETE FROM chunks WHERE notebook_id=? AND source_id=? AND chunk_index < ?")
        bind(s, 1, notebook); bind(s, 2, sourceId); sqlite3_bind_int64(s, 3, Int64(Self.pictureIndex)); sqlite3_step(s); sqlite3_finalize(s)
    }

    func removeLocalNote(notebook: String, sourceId: String) throws {
        dropMatrix()
        for t in ["chunks", "sources"] {
            let s = try statement("DELETE FROM \(t) WHERE notebook_id=? AND source_id=?")
            bind(s, 1, notebook); bind(s, 2, sourceId); sqlite3_step(s); sqlite3_finalize(s)
        }
    }

    func chunkCount() -> Int {
        guard let s = try? statement("SELECT COUNT(*) FROM chunks") else { return 0 }
        defer { sqlite3_finalize(s) }
        return sqlite3_step(s) == SQLITE_ROW ? Int(sqlite3_column_int64(s, 0)) : 0
    }

    // MARK: changes (always whole-notebook, atomic)

    func wipe() throws {
        dropMatrix()
        for t in ["chunks", "sources", "notebooks"] { try exec("DELETE FROM \(t)") }
        try exec("DELETE FROM meta WHERE key LIKE 'personal:%'")        // profile, memory, entities: all of it goes
    }

    func removeNotebooks(except keep: Set<String>) throws {
        dropMatrix()
        for n in notebooks() where !keep.contains(n.id) {
            for t in ["chunks", "sources"] {
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
    func apply(notebook: String, title: String, digest: String, removed: [String],
               resent: [String], chunks: [LibraryChunk], hashes: [String: String]) throws {
        dropMatrix()
        try exec("BEGIN IMMEDIATE")
        do {
            for sid in removed + resent {
                for t in ["chunks", "sources"] {
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

    /// Build the in-memory vector block for a notebook now, so its first question does not pay for it.
    func warmUp(notebook: String?) { if let notebook { try? loadMatrix(notebook) } }

    /// The notebook whose best chunk matches `query` most closely. Loads one notebook at a time so memory never
    /// holds more than the largest one, and leaves nothing loaded afterwards.
    func closestNotebook(to query: [Float]) throws -> String? {
        guard query.count == Self.dims else { return nil }
        var best: (id: String, score: Float)?
        for n in notebooks() {
            try loadMatrix(n.id)
            let rows = rowKeys.count
            guard rows > 0 else { continue }
            var scores = [Float](repeating: 0, count: rows)
            matrix.withUnsafeBufferPointer { m in
                cblas_sgemv(CblasRowMajor, CblasNoTrans, Int32(rows), Int32(Self.dims), 1, m.baseAddress!, Int32(Self.dims), query, 1, 0, &scores, 1)
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

    struct SearchParams: Sendable {
        var vectorCandidates = 48
        var bm25Candidates = 48
        var hybridK = 24
        var rrfK = 60.0
        var missingRank = 1000.0
        var k1 = 1.5, b = 0.75, epsilon = 0.25
    }

    /// Hybrid retrieval exactly as the Mac's `hybrid_search`: the nearest vectors and the best BM25 documents
    /// (each `…Candidates` long), fused by Reciprocal Rank Fusion (0-based ranks, `missingRank` when absent), the
    /// best `hybridK` returned. `queryText` nil means vectors only (used to find a question's notebook).
    func search(query: [Float], queryText: String? = nil, params: SearchParams = SearchParams(), notebook: String? = nil)
        throws -> (hits: [LibraryHit], scanned: Int, seconds: Double) {
        let started = Date()
        guard let notebook else { return ([], 0, 0) }          // one notebook at a time (see closestNotebook for "which one")
        try loadMatrix(notebook)
        let range = 0..<rowKeys.count
        let rows = range.count
        guard rows > 0, query.count == Self.dims else { return ([], 0, Date().timeIntervalSince(started)) }
        var scores = [Float](repeating: 0, count: rows)
        matrix.withUnsafeBufferPointer { m in
            cblas_sgemv(CblasRowMajor, CblasNoTrans, Int32(rows), Int32(Self.dims), 1,
                        m.baseAddress! + range.lowerBound * Self.dims, Int32(Self.dims), query, 1, 0, &scores, 1)
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
