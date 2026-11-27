import Foundation
import SQLite3

extension LibraryStore {
    /// One chunk with its vector as Float32, for clustering a notebook's topics.
    public struct ChunkVector: Sendable {
        public let sourceId: String
        public let chunkIndex: Int
        public let filename: String
        public let text: String
        public let vector: [Float]
        public var id: String { "\(sourceId):\(chunkIndex)" }
    }

    /// Every ordinary chunk of a notebook (summaries left out) with its vector, in source order. A big notebook is
    /// thousands of rows and about 3 KB each: fine for a one-off clustering, not for every keystroke.
    public func chunkVectors(notebook: String, limit: Int = 6_000) -> [ChunkVector] {
        guard let s = try? statement("SELECT source_id, chunk_index, filename, text, vector FROM chunks WHERE notebook_id=? AND summary=0 ORDER BY source_id, chunk_index LIMIT ?") else { return [] }
        defer { sqlite3_finalize(s) }
        bind(s, 1, notebook)
        sqlite3_bind_int64(s, 2, Int64(limit))
        var out: [ChunkVector] = []
        while sqlite3_step(s) == SQLITE_ROW {
            guard let blob = sqlite3_column_blob(s, 4), sqlite3_column_bytes(s, 4) == Self.dims * 2 else { continue }
            let halves = blob.assumingMemoryBound(to: Float16.self)
            var v = [Float](repeating: 0, count: Self.dims)
            for i in 0..<Self.dims { v[i] = Float(halves[i]) }
            out.append(ChunkVector(sourceId: String(cString: sqlite3_column_text(s, 0)), chunkIndex: Int(sqlite3_column_int64(s, 1)),
                                   filename: String(cString: sqlite3_column_text(s, 2)), text: String(cString: sqlite3_column_text(s, 3)), vector: v))
        }
        return out
    }

    /// The texts of a notebook's chunks in reading order, each with its source, for the insights that read the whole
    /// notebook (timeline, entities, claims) rather than search for it.
    public func chunkTexts(notebook: String, limit: Int = 4_000) -> [(sourceId: String, filename: String, text: String)] {
        guard let s = try? statement("SELECT source_id, filename, text FROM chunks WHERE notebook_id=? AND summary=0 ORDER BY source_id, chunk_index LIMIT ?") else { return [] }
        defer { sqlite3_finalize(s) }
        bind(s, 1, notebook)
        sqlite3_bind_int64(s, 2, Int64(limit))
        var out: [(String, String, String)] = []
        while sqlite3_step(s) == SQLITE_ROW {
            out.append((String(cString: sqlite3_column_text(s, 0)), String(cString: sqlite3_column_text(s, 1)), String(cString: sqlite3_column_text(s, 2))))
        }
        return out
    }
}
