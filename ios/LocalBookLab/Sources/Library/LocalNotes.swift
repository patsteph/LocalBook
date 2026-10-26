import CoreGraphics
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
    /// Where a note's picture is kept on this phone.
    static func photoURL(_ sourceId: String) -> URL {
        URL.applicationSupportDirectory.appending(path: "LocalBookLab/Photos/\(sourceId.replacingOccurrences(of: ":", with: "_")).jpg")
    }

    /// Add a note to a notebook on this phone. It is searched and cited like any source, straight away.
    /// With a picture, the picture is kept and, if picture search is installed, embedded so a typed question can find it.
    func addNote(notebook: String, title: String, text: String, kind: String = "note", photo: (image: CGImage, jpeg: Data)? = nil, replacing: String? = nil) async throws {
        let body = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let store, !body.isEmpty || photo != nil else { return }
        let cleanTitle = title.trimmingCharacters(in: .whitespaces)
        let name = !cleanTitle.isEmpty ? cleanTitle
            : (body.isEmpty ? "Photo" : String(body.prefix(40)).replacingOccurrences(of: "\n", with: " "))
        let id = replacing ?? (LocalNotes.prefix + UUID().uuidString.lowercased())
        if replacing != nil { try await store.removeLocalNoteText(notebook: notebook, sourceId: id) }
        var chunks: [LibraryChunk] = []
        for (i, piece) in LocalNotes.split(body).enumerated() {
            let v = try await documentVector(piece)
            let half = v.map { Float16($0) }
            chunks.append(.init(notebookId: notebook, sourceId: id, chunkIndex: i, text: piece, summary: false,
                                filename: name, sourceType: kind, vector: half.withUnsafeBytes { Data($0) }))
        }
        if let photo {
            let url = Self.photoURL(id)
            try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try photo.jpeg.write(to: url)
            if let vision, let v = try? await vision.embed(photo.image) {          // picture search: the photo's own vector
                let half = v.map { Float16($0) }
                chunks.append(.init(notebookId: notebook, sourceId: id, chunkIndex: LibraryStore.pictureIndex,
                                    text: body.isEmpty ? "Photo: \(name)" : "Photo: \(name). \(body.prefix(200))", summary: false,
                                    filename: name, sourceType: "image", vector: half.withUnsafeBytes { Data($0) }))
            }
        }
        guard !chunks.isEmpty else { throw LibraryError.badResponse("Nothing to save: picture search isn't installed and the photo had no text.") }
        try await store.addLocalNote(notebook: notebook, sourceId: id, title: name, kind: kind, chunks: chunks, hash: "local")
        await refresh()
        Task { await flushNotes() }
    }

    // MARK: picture search (the image encoder)

    /// Load the image encoder if it is already on the phone (no download).
    func loadVisionIfInstalled() async {
        guard vision == nil, EmbeddingModelStore.visionInstalled, let engine else { return }
        do {
            let loc = try await EmbeddingModelStore.prepareVision { _ in }
            vision = try VisionEncoder(location: loc, engine: engine)
            visionState = .ready
        } catch { visionState = .failed(error.localizedDescription) }
    }

    /// Download (about 295 MB) and load the image encoder. Only called when the user asks.
    func installVision() async {
        guard let engine else { visionState = .failed("Search isn't ready yet."); return }
        visionState = .downloading(0)
        do {
            let loc = try await EmbeddingModelStore.prepareVision { p in Task { @MainActor in self.visionState = .downloading(p) } }
            vision = try VisionEncoder(location: loc, engine: engine)
            visionState = .ready
        } catch { visionState = .failed(error.localizedDescription) }
    }

    func removeVision() {
        vision = nil
        EmbeddingModelStore.removeVision()
        visionState = .none
    }

    func localNotes() async -> [LibraryStore.LocalNote] { await store?.localNotes() ?? [] }

    func sources(in notebook: String) async -> [LibraryStore.SourceInfo] { await store?.sources(notebook: notebook) ?? [] }
    func sourceText(notebook: String, sourceId: String) async -> String { await store?.sourceText(notebook: notebook, sourceId: sourceId) ?? "" }

    func removeNote(_ note: LibraryStore.LocalNote) async {
        try? await store?.removeLocalNote(notebook: note.notebookId, sourceId: note.id)
        try? FileManager.default.removeItem(at: Self.photoURL(note.id))
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
    /// Keeps going while there is something to send (notes added meanwhile) or the Mac is still indexing what it got.
    func flushNotes() async {
        guard let store, let mac = MacLink.paired, !flushingNotes else { return }
        flushingNotes = true
        defer { flushingNotes = false }
        noteSendProblem = nil
        var waits = 0
        while true {
            var sentAny = false
            for note in await store.localNotes() where await noteSentSourceId(note.id) == nil {
                if note.text.isEmpty { continue }                       // a picture with no text has nothing to send yet
                var sid: String?
                var attempt = 0
                while sid == nil {
                    do {
                        var payload: [String: Any] = [
                            "notebook_id": note.notebookId, "client_id": note.id, "title": note.title, "text": note.text, "kind": note.kind,
                        ]
                        if note.hasPicture, let jpeg = try? Data(contentsOf: Self.photoURL(note.id)) {
                            payload["photo"] = jpeg.base64EncodedString()           // the Mac keeps the picture itself too
                        }
                        let data = try await MacLink.signedCall(mac, path: "/phone/library/note", json: payload, timeout: 120)
                        sid = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["source_id"] as? String
                        if sid?.isEmpty != false { noteSendProblem = "The Mac's reply wasn't understood."; await refresh(); return }
                    } catch {
                        // The Mac may be busy indexing the last note. Asking again is safe: the same note id gets the same note.
                        attempt += 1
                        noteSendProblem = attempt < 5 ? "The Mac is busy; trying again (\(attempt) of 5)…"
                                                      : "Waiting for the Mac: \(error.localizedDescription)"
                        await refresh()
                        if attempt >= 5 { return }
                        try? await Task.sleep(for: .seconds(15))
                    }
                }
                noteSendProblem = nil
                try? await store.setMeta(Self.sentKey(note.id), sid!)
                sentAny = true
            }
            if sentAny { await sync() }
            var pending = 0                                             // sent, but the Mac has not finished indexing it yet
            var unsent = 0
            for note in await store.localNotes() where !note.text.isEmpty {
                guard let sid = await noteSentSourceId(note.id) else { unsent += 1; continue }
                if await store.sourceHashes(notebook: note.notebookId)[sid] != nil {
                    if note.hasPicture { try? await store.removeLocalNoteText(notebook: note.notebookId, sourceId: note.id) }   // keep the picture
                    else {
                        try? await store.removeLocalNote(notebook: note.notebookId, sourceId: note.id)
                        try? await store.setMeta(Self.sentKey(note.id), "")
                    }
                } else { pending += 1 }
            }
            await refresh()
            if unsent > 0 { continue }
            guard pending > 0, waits < 30 else { return }               // look again every 20 s for up to 10 minutes
            waits += 1
            try? await Task.sleep(for: .seconds(20))
            await sync()
        }
    }
}
