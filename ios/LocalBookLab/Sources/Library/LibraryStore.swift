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

    private func dropMatrix() { matrix = []; rowKeys = []; ranges = [:]; matrixReady = false; bm25 = [:] }

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

    /// Build the in-memory vector block now, so the first question does not pay for it (about 0.6 s).
    func warmUp() { try? buildMatrix() }

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
        return scores.sorted { $0.value > $1.value }.prefix(limit).map { (Int($0.key), $0.value) }
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
        var vectorRank: [Int: Double] = [:]
        for (rank, row) in order.prefix(params.vectorCandidates).enumerated() { vectorRank[row] = Double(rank) }
        var keywordRank: [Int: Double] = [:]
        if let queryText, notebook != nil {
            let index = try bm25Index(for: notebook!, epsilon: params.epsilon)
            for (rank, hit) in bm25Top(index, query: queryText, k1: params.k1, b: params.b, limit: params.bm25Candidates).enumerated() {
                keywordRank[hit.doc] = Double(rank)
            }
        }
        var fused: [(row: Int, rrf: Double)] = []
        for row in Set(vectorRank.keys).union(keywordRank.keys) {
            let v = vectorRank[row] ?? params.missingRank, k = keywordRank[row] ?? params.missingRank
            fused.append((row, 1 / (params.rrfK + v) + 1 / (params.rrfK + k)))
        }
        fused.sort { $0.rrf > $1.rrf }
        let best = fused.first?.rrf ?? 1
        let titles = Dictionary(uniqueKeysWithValues: notebooks().map { ($0.id, $0.title) })
        var hits: [LibraryHit] = []
        for item in fused.prefix(params.hybridK) {
            let key = rowKeys[range.lowerBound + item.row]
            let t = try statement("SELECT text, filename FROM chunks WHERE notebook_id=? AND source_id=? AND chunk_index=?")
            defer { sqlite3_finalize(t) }
            bind(t, 1, key.nb); bind(t, 2, key.src); sqlite3_bind_int64(t, 3, Int64(key.idx))
            guard sqlite3_step(t) == SQLITE_ROW else { continue }
            hits.append(.init(notebookId: key.nb, notebookTitle: titles[key.nb] ?? key.nb, sourceId: key.src, chunkIndex: key.idx,
                              filename: String(cString: sqlite3_column_text(t, 1)), text: String(cString: sqlite3_column_text(t, 0)),
                              score: scores[item.row], rrf: Float(item.rrf / best)))
        }
        return (hits, rows, Date().timeIntervalSince(started))
    }
}
