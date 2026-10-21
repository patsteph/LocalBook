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
        Task { await flushNotes() }
    }

    func localNotes() async -> [LibraryStore.LocalNote] { await store?.localNotes() ?? [] }

    func removeNote(_ note: LibraryStore.LocalNote) async {
        try? await store?.removeLocalNote(notebook: note.notebookId, sourceId: note.id)
        await refresh()
    }
}

extension LibraryModel {
    private static func sentKey(_ id: String) -> String { "note_sent:" + id }

    /// Where a note stands: not yet on the Mac, sent (waiting to come back through sync), or nothing recorded.
    func noteSentSourceId(_ id: String) async -> String? {
        guard let v = await store?.meta(Self.sentKey(id)), !v.isEmpty else { return nil }
        return v
    }

    func unsentNoteCount() async -> Int {
        var n = 0
        for note in await localNotes() where await noteSentSourceId(note.id) == nil { n += 1 }
        return n
    }

    /// Send notes made here to the Mac (it indexes them like its own), pull them back with a sync, and drop the
    /// phone-only copies once the Mac's version has arrived. Quietly leaves them in place when the Mac is out of reach.
    func flushNotes() async {
        guard let store, let mac = MacLink.paired, !flushingNotes else { return }
        flushingNotes = true
        defer { flushingNotes = false }
        var sentAny = false
        noteSendProblem = nil
        for note in await store.localNotes() where await noteSentSourceId(note.id) == nil {
            do {
                let data = try await MacLink.signedCall(mac, path: "/phone/library/note", json: [
                    "notebook_id": note.notebookId, "client_id": note.id, "title": note.title, "text": note.text, "kind": note.kind,
                ], timeout: 30)
                let sid = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["source_id"] as? String
                guard let sid, !sid.isEmpty else { noteSendProblem = "The Mac's reply wasn't understood."; break }
                try await store.setMeta(Self.sentKey(note.id), sid)
                sentAny = true
            } catch {
                noteSendProblem = "Waiting for the Mac: \(error.localizedDescription)"
                break
            }
        }
        if sentAny { await sync() }
        for note in await store.localNotes() {
            guard let sid = await noteSentSourceId(note.id) else { continue }
            if await store.sourceHashes(notebook: note.notebookId)[sid] != nil {
                try? await store.removeLocalNote(notebook: note.notebookId, sourceId: note.id)
                try? await store.setMeta(Self.sentKey(note.id), "")
            }
        }
        await refresh()
    }
}
