import CryptoKit
import Foundation
import SQLite3

/// What a paired phone may read of this library, in the shapes the phone's `LibrarySync` already understands (the
/// Python Mac's `phone_library.py`): each source's digest, a notebook's digest over them, and pages of chunks with
/// their float16 vectors in (source_id, chunk_index) order.
extension LibraryStore {
    /// A notebook kept on this Mac only: it is not in the phone's manifest, and a phone may not edit it.
    static let keepLocalPrefix = "keep_local:"

    public func keptLocal() -> Set<String> {
        Set(metaKeys(prefix: Self.keepLocalPrefix).map { String($0.dropFirst(Self.keepLocalPrefix.count)) })
    }

    public func isKeptLocal(_ notebook: String) -> Bool { meta(Self.keepLocalPrefix + notebook) != nil }

    public func setKeptLocal(_ notebook: String, _ local: Bool) throws {
        if local { try setMeta(Self.keepLocalPrefix + notebook, "1") } else { try deleteMeta(Self.keepLocalPrefix + notebook) }
    }

    public struct HostSource: Sendable, Equatable {
        public let sourceId: String, filename: String, sourceType: String
        public let chunks: Int
        /// sha256 over "index:sha256(text)[:16];" for every row in index order, first 24 hex digits (the old Mac's rule).
        public let hash: String
        public let created: String?
    }

    /// Every source of a notebook with its digest, in source id order.
    public func hostSources(notebook: String) -> [HostSource] {
        guard let s = try? statement("""
            SELECT c.source_id, c.chunk_index, c.text, c.filename, c.source_type, r.created
            FROM chunks c LEFT JOIN sources r ON r.notebook_id = c.notebook_id AND r.source_id = c.source_id
            WHERE c.notebook_id=? ORDER BY c.source_id, c.chunk_index
            """) else { return [] }
        defer { sqlite3_finalize(s) }
        bind(s, 1, notebook)
        var out: [HostSource] = []
        var current: (id: String, filename: String, type: String, created: String?, count: Int, hasher: SHA256)?
        func close() {
            guard let c = current else { return }
            let digest = c.hasher.finalize().map { String(format: "%02x", $0) }.joined()
            out.append(.init(sourceId: c.id, filename: c.filename, sourceType: c.type, chunks: c.count, hash: String(digest.prefix(24)), created: c.created))
        }
        while sqlite3_step(s) == SQLITE_ROW {
            let sid = String(cString: sqlite3_column_text(s, 0))
            let index = Int(sqlite3_column_int64(s, 1))
            let text = String(cString: sqlite3_column_text(s, 2))
            if current?.id != sid {
                close()
                let created = sqlite3_column_text(s, 5).map { String(cString: $0) }
                current = (sid, String(cString: sqlite3_column_text(s, 3)), String(cString: sqlite3_column_text(s, 4)),
                           created?.isEmpty == false ? created : nil, 0, SHA256())
            }
            current?.count += 1
            let rowHash = String(SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined().prefix(16))
            current?.hasher.update(data: Data("\(index):\(rowHash);".utf8))
        }
        close()
        return out
    }

    /// The notebook's digest over its sources' digests: sha256 of "id:hash;" in id order, 24 hex digits.
    public nonisolated static func hostDigest(_ sources: [HostSource]) -> String {
        let joined = sources.sorted { $0.sourceId < $1.sourceId }.map { "\($0.sourceId):\($0.hash);" }.joined()
        return String(SHA256.hash(data: Data(joined.utf8)).map { String(format: "%02x", $0) }.joined().prefix(24))
    }

    public struct HostRow: Sendable {
        public let sourceId: String, chunkIndex: Int, text: String, summary: Bool, filename: String, sourceType: String
        public let vector: Data
    }

    /// One page of a notebook's chunks in (source_id, chunk_index) order, after the given row, for the given sources
    /// (nil: all). `more` says whether another page follows.
    public func hostRows(notebook: String, sources: [String]?, after: (sourceId: String, chunkIndex: Int)?, limit: Int) -> (rows: [HostRow], more: Bool) {
        let limit = max(1, min(limit, 400))
        var sql = "SELECT source_id, chunk_index, text, summary, filename, source_type, vector FROM chunks WHERE notebook_id=?"
        if sources != nil { sql += " AND source_id IN (SELECT value FROM json_each(?))" }
        if after != nil { sql += " AND (source_id > ? OR (source_id = ? AND chunk_index > ?))" }
        sql += " ORDER BY source_id, chunk_index LIMIT ?"
        guard let s = try? statement(sql) else { return ([], false) }
        defer { sqlite3_finalize(s) }
        var i: Int32 = 1
        bind(s, i, notebook); i += 1
        if let sources {
            let json = (try? JSONSerialization.data(withJSONObject: sources)).map { String(decoding: $0, as: UTF8.self) } ?? "[]"
            bind(s, i, json); i += 1
        }
        if let after {
            bind(s, i, after.sourceId); bind(s, i + 1, after.sourceId); sqlite3_bind_int64(s, i + 2, Int64(after.chunkIndex)); i += 3
        }
        sqlite3_bind_int64(s, i, Int64(limit + 1))
        var out: [HostRow] = []
        while sqlite3_step(s) == SQLITE_ROW {
            var vector = Data()
            if let blob = sqlite3_column_blob(s, 6) { vector = Data(bytes: blob, count: Int(sqlite3_column_bytes(s, 6))) }
            out.append(.init(sourceId: String(cString: sqlite3_column_text(s, 0)), chunkIndex: Int(sqlite3_column_int64(s, 1)),
                             text: String(cString: sqlite3_column_text(s, 2)), summary: sqlite3_column_int(s, 3) != 0,
                             filename: String(cString: sqlite3_column_text(s, 4)), sourceType: String(cString: sqlite3_column_text(s, 5)),
                             vector: vector))
        }
        let more = out.count > limit
        if more { out.removeLast() }
        return (out, more)
    }

    /// How many sources a notebook has, for the topics reply.
    public func hostSourceCount(notebook: String) -> Int {
        guard let s = try? statement("SELECT COUNT(DISTINCT source_id) FROM chunks WHERE notebook_id=?") else { return 0 }
        defer { sqlite3_finalize(s) }
        bind(s, 1, notebook)
        return sqlite3_step(s) == SQLITE_ROW ? Int(sqlite3_column_int64(s, 0)) : 0
    }
}
