import Foundation

/// Notes (typed, dictated, or read from a photo) made on this phone and kept in a notebook beside the Mac's sources.
/// Their source ids start with `prefix`, so a sync from the Mac never removes or rewrites them.
enum LocalNotes {
    static let prefix = "local:"

    /// Paragraph-aware pieces of about `limit` characters, the size the Mac's own chunks are near.
    static func split(_ text: String, limit: Int = 900) -> [String] {
        var out: [String] = []
        var current = ""
        for para in text.components(separatedBy: "\n\n") {
            let p = para.trimmingCharacters(in: .whitespacesAndNewlines)
            if p.isEmpty { continue }
            if p.count > limit {                                        // one long paragraph: cut at sentence ends
                if !current.isEmpty { out.append(current); current = "" }
                var piece = ""
                for sentence in p.split(omittingEmptySubsequences: true, whereSeparator: { ".!?\n".contains($0) }) {
                    if piece.count + sentence.count > limit, !piece.isEmpty { out.append(piece); piece = "" }
                    piece += sentence + ". "
                }
                if !piece.isEmpty { out.append(piece.trimmingCharacters(in: .whitespaces)) }
            } else if current.count + p.count + 2 > limit, !current.isEmpty {
                out.append(current); current = p
            } else {
                current = current.isEmpty ? p : current + "\n\n" + p
            }
        }
        if !current.isEmpty { out.append(current) }
        return out
    }
}

extension LibraryModel {
    /// Add a note to a notebook on this phone. It is searched and cited like any source, straight away.
    func addNote(notebook: String, title: String, text: String, kind: String = "note") async throws {
        let body = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let store, !body.isEmpty else { return }
        let name = title.trimmingCharacters(in: .whitespaces).isEmpty
            ? String(body.prefix(40)).replacingOccurrences(of: "\n", with: " ") : title.trimmingCharacters(in: .whitespaces)
        let id = LocalNotes.prefix + UUID().uuidString.lowercased()
        var chunks: [LibraryChunk] = []
        for (i, piece) in LocalNotes.split(body).enumerated() {
            let v = try await documentVector(piece)
            let half = v.map { Float16($0) }
            chunks.append(.init(notebookId: notebook, sourceId: id, chunkIndex: i, text: piece, summary: false,
                                filename: name, sourceType: kind, vector: half.withUnsafeBytes { Data($0) }))
        }
        try await store.addLocalNote(notebook: notebook, sourceId: id, title: name, kind: kind, chunks: chunks, hash: "local")
        await refresh()
    }

    func localNotes() async -> [LibraryStore.LocalNote] { await store?.localNotes() ?? [] }

    func removeNote(_ note: LibraryStore.LocalNote) async {
        try? await store?.removeLocalNote(notebook: note.notebookId, sourceId: note.id)
        await refresh()
    }
}
