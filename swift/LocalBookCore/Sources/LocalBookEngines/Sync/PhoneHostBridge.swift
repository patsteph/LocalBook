import CryptoKit
import Foundation
import ImageIO
import LocalBookCore

/// The host's routes that change this library or read what Studio made, answered with the same code the Mac's own
/// window runs (`LibraryModel`'s edits, `addNote`, `StudioStore`), so a phone can do nothing the owner could not do
/// here, and it is done the same way. Shapes follow the Python Mac's `phone_notes.py`, `phone_edits.py` and
/// `phone_studio.py`, which is what the shipped iPhone app expects.
@MainActor
public final class PhoneHostBridge: PhoneHostDelegate {
    private let library: LibraryModel

    public init(library: LibraryModel) { self.library = library }

    public func fallbackRecipe() async -> Data? {
        guard let url = Bundle.module.url(forResource: "phone_recipe", withExtension: "json", subdirectory: "Resources")
                ?? Bundle.module.url(forResource: "phone_recipe", withExtension: "json") else { return nil }
        return try? Data(contentsOf: url)
    }

    public func handle(path: String, body: Data, from phone: PairedPhone) async throws -> Data? {
        let b = (try? JSONSerialization.jsonObject(with: body) as? [String: Any]) ?? [:]
        func text(_ key: String) -> String { (b[key] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "" }
        let reply: [String: Any]?
        switch path {
        case "/phone/library/note": reply = try await addNote(b)
        case "/phone/library/notebook/edit": reply = try await notebookEdit(b)
        case "/phone/library/source/delete": reply = try await sourceDelete(notebook: text("notebook_id"), sourceId: text("source_id"))
        case "/phone/library/source/move": reply = try await sourceMove(notebook: text("notebook_id"), sourceId: text("source_id"), target: text("target_notebook_id"))
        case "/phone/library/source/rename": reply = try await sourceRename(notebook: text("notebook_id"), sourceId: text("source_id"), title: text("title"))
        case "/phone/library/studio/put": reply = try await studioPut(b)
        case "/phone/library/studio/audio": reply = try await studioAudio(b)
        case "/phone/library/studio/delete": reply = try await studioDelete(text("id"))
        case "/phone/library/studio/list": reply = ["items": await studioListing()]
        case "/phone/library/studio/get": reply = ["items": await studioItems(b["items"] as? [[String: Any]] ?? [])]
        case "/phone/library/studio/file": reply = try await studioFile(kind: text("kind"), id: text("id"), offset: (b["offset"] as? Int) ?? 0)
        default: reply = nil
        }
        guard let reply else { return nil }
        return try JSONSerialization.data(withJSONObject: reply)
    }

    // MARK: notes from the phone

    private static let maxNoteChars = 200_000
    private static let maxPhotoBytes = 6_000_000

    /// Indexed here like a note typed on this Mac; the phone receives it back through its next sync. The phone's
    /// client id makes the call idempotent: the same id again returns the same note.
    private func addNote(_ b: [String: Any]) async throws -> [String: Any] {
        let notebook = (b["notebook_id"] as? String) ?? ""
        let clientId = (b["client_id"] as? String) ?? ""
        let noteText = ((b["text"] as? String) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clientId.isEmpty, clientId.count <= 100 else { throw HostRefusal(400, "a client id is required") }
        guard !noteText.isEmpty, noteText.count <= Self.maxNoteChars else { throw HostRefusal(400, "note text of 1 to \(Self.maxNoteChars) characters is required") }
        guard let store = library.store else { throw HostRefusal(503, "the library is not open") }
        var photo: (image: CGImage, jpeg: Data)?
        if let b64 = b["photo"] as? String, !b64.isEmpty {
            guard let jpeg = Data(base64Encoded: b64) else { throw HostRefusal(400, "the photo is not valid base64") }
            guard jpeg.count <= Self.maxPhotoBytes, jpeg.starts(with: [0xFF, 0xD8]) else { throw HostRefusal(400, "the photo must be a JPEG of at most 6 MB") }
            if let source = CGImageSourceCreateWithData(jpeg as CFData, nil), let image = CGImageSourceCreateImageAtIndex(source, 0, nil) {
                photo = (image, jpeg)
            }
        }
        let ledgerKey = "phone_note:\(notebook):\(clientId)"
        if let existing = await store.meta(ledgerKey), !existing.isEmpty { return ["source_id": existing, "duplicate": true] }
        let sourceId = LocalNotes.prefix + "phone-" + UUID().uuidString.lowercased()
        let title = ((b["title"] as? String) ?? "").trimmingCharacters(in: .whitespaces)
        let kind = ((b["kind"] as? String).flatMap { $0.isEmpty ? nil : $0 }) ?? "note"
        do {
            try await library.addNote(notebook: notebook, title: String(title.prefix(200)), text: noteText, kind: kind, photo: photo, replacing: sourceId)
        } catch {
            throw HostRefusal(500, "the note could not be indexed: \(error.localizedDescription)")
        }
        try? await store.setMeta(ledgerKey, sourceId)
        SoakTrace.log("host: note from the phone indexed as \(sourceId.prefix(18)) in \(notebook.prefix(8))")
        return ["source_id": sourceId, "duplicate": false]
    }

    // MARK: the notebook list

    /// One route for every list action, as on the Python Mac. Sections have no ids on this Mac, so a section's id
    /// IS its name here; the phone treats ids as opaque, so nothing on it changes.
    private func notebookEdit(_ b: [String: Any]) async throws -> [String: Any] {
        func text(_ key: String) -> String { (b[key] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "" }
        let action = text("action"), nb = text("notebook_id"), title = text("title")
        func run(_ act: String, _ fields: [String: Any]) async throws -> [String: Any] {
            var made: [String: Any] = [:]
            if let problem = await library.editNotebooks(act, fields, made: { made = $0 }) { throw HostRefusal(400, problem) }
            return made
        }
        switch action {
        case "create":
            guard !title.isEmpty else { throw HostRefusal(400, "a title is required") }
            var fields: [String: Any] = ["title": String(title.prefix(200))]
            if let c = b["color"] as? String, !c.isEmpty { fields["color"] = c }
            let made = try await run("create", fields)
            return ["id": made["id"] ?? "" as Any, "title": title, "color": fields["color"] ?? NSNull() as Any]
        case "rename":
            guard !title.isEmpty else { throw HostRefusal(400, "a title is required") }
            try await known(nb)
            _ = try await run("rename", ["notebook_id": nb, "title": String(title.prefix(200))])
            return ["id": nb, "title": title]
        case "color":
            let color = text("color")
            guard color.range(of: #"^#[0-9A-Fa-f]{6}$"#, options: .regularExpression) != nil else { throw HostRefusal(400, "a colour like #3B82F6 is required") }
            try await known(nb)
            _ = try await run("color", ["notebook_id": nb, "color": color])
            return ["id": nb, "color": color]
        case "delete":
            try await known(nb)
            _ = try await run("delete", ["notebook_id": nb])
            return ["id": nb, "deleted": true]
        case "section":
            try await known(nb)
            let name = (b["section_id"] as? String) ?? ""
            _ = try await run("section", ["notebook_id": nb, "section": name])
            return ["id": nb, "section_id": name.isEmpty ? NSNull() as Any : name as Any]
        case "sections":
            return ["sections": await library.sections().map { ["id": $0.id, "name": $0.name] }]
        case "section_create":
            let name = text("name")
            guard !name.isEmpty else { throw HostRefusal(400, "a name is required") }
            if !(await library.sections().contains { $0.name == name }) { _ = try await run("section_create", ["name": String(name.prefix(100))]) }
            return ["id": name, "name": name]
        case "section_rename":
            let old = text("section_id"), name = text("name")
            guard !old.isEmpty, !name.isEmpty else { throw HostRefusal(400, "a section id and a name are required") }
            _ = try await run("section_rename", ["section_id": old, "name": String(name.prefix(100))])
            return ["id": name, "name": name]
        case "section_delete":
            let name = text("section_id")
            guard !name.isEmpty else { throw HostRefusal(400, "not a section id") }
            _ = try await run("section_delete", ["section_id": name])
            return ["id": name, "deleted": true]
        default:
            throw HostRefusal(400, "unknown action")
        }
    }

    private func known(_ notebook: String) async throws {
        guard !notebook.isEmpty, library.notebooks.contains(where: { $0.id == notebook }) else { throw HostRefusal(404, "no such notebook") }
    }

    // MARK: sources

    private func source(_ notebook: String, _ sourceId: String) async throws -> LibraryStore.SourceInfo {
        guard sourceId.range(of: #"^[A-Za-z0-9_:\-]{1,120}$"#, options: .regularExpression) != nil else { throw HostRefusal(400, "not a source id") }
        try await known(notebook)
        guard let s = await library.sources(in: notebook).first(where: { $0.id == sourceId }) else { throw HostRefusal(404, "no such source") }
        return s
    }

    private func sourceDelete(notebook: String, sourceId: String) async throws -> [String: Any] {
        let s = try await source(notebook, sourceId)
        if let problem = await library.deleteSource(s) { throw HostRefusal(500, problem) }
        return ["source_id": sourceId, "deleted": true, "notes": NSNull()]
    }

    private func sourceMove(notebook: String, sourceId: String, target: String) async throws -> [String: Any] {
        let s = try await source(notebook, sourceId)
        try await known(target)
        if let problem = await library.moveSource(s, to: target) { throw HostRefusal(500, problem) }
        return ["source_id": sourceId, "target_notebook_id": target, "reindexed": false, "problem": NSNull()]
    }

    private func sourceRename(notebook: String, sourceId: String, title: String) async throws -> [String: Any] {
        let clean = String(title.split(whereSeparator: \.isWhitespace).joined(separator: " ").prefix(200))
        guard !clean.isEmpty else { throw HostRefusal(400, "a title is required") }
        let s = try await source(notebook, sourceId)
        if let problem = await library.renameSource(s, to: clean) { throw HostRefusal(500, problem) }
        return ["source_id": sourceId, "filename": clean]
    }

    // MARK: Studio, from the phone

    private static let idRule = #"^[A-Za-z0-9-]{8,64}$"#
    private static let maxStudioText = 400_000
    private static let maxAudioBytes = 120_000_000

    private func checkItem(_ id: String, notebook: String) async throws {
        guard id.range(of: Self.idRule, options: .regularExpression) != nil else { throw HostRefusal(400, "bad item id") }
        try await known(notebook)
    }

    private func studioPut(_ b: [String: Any]) async throws -> [String: Any] {
        let kind = (b["kind"] as? String) ?? "", id = (b["id"] as? String) ?? "", notebook = (b["notebook_id"] as? String) ?? ""
        try await checkItem(id, notebook: notebook)
        if StudioStore.all(notebook: nil).contains(where: { $0.id == id }) { return ["ok": true, "id": id] }     // the same id again changes nothing
        let title = ((b["title"] as? String) ?? "").trimmingCharacters(in: .whitespaces)
        let created = ((b["created"] as? String).flatMap(StudioPull.parseDate)) ?? Date()
        let encoder = JSONEncoder()
        var item = StudioItem(id: id, notebookId: notebook, kind: .doc, title: title, created: created, body: "")
        item.origin = "phone"
        switch kind {
        case "doc":
            guard let content = b["body"] as? String, !content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty, content.count <= Self.maxStudioText else { throw HostRefusal(400, "empty or too long") }
            item.kind = .doc; item.body = content
        case "cards":
            let cards = (b["body"] as? [[String: Any]] ?? []).compactMap { c -> StudioCard? in
                guard let front = (c["front"] as? String)?.trimmingCharacters(in: .whitespaces), !front.isEmpty else { return nil }
                return StudioCard(front: front, back: ((c["back"] as? String) ?? "").trimmingCharacters(in: .whitespaces))
            }
            guard !cards.isEmpty, let data = try? encoder.encode(cards) else { throw HostRefusal(400, "empty or too long") }
            item.kind = .cards; item.body = String(decoding: data, as: UTF8.self)
        case "quiz":
            let questions = (b["body"] as? [[String: Any]] ?? []).compactMap { q -> StudioQuestion? in
                guard let choices = (q["choices"] as? [Any])?.map({ "\($0)" }), !choices.isEmpty,
                      let answer = q["answer"] as? Int, (0..<choices.count).contains(answer) else { return nil }
                return StudioQuestion(question: (q["question"] as? String) ?? "", choices: choices, answer: answer, why: q["why"] as? String)
            }
            guard !questions.isEmpty, let data = try? encoder.encode(questions) else { throw HostRefusal(400, "no usable questions") }
            item.kind = .quiz; item.body = String(decoding: data, as: UTF8.self)
        default:
            throw HostRefusal(400, "unknown kind")
        }
        StudioStore.save(item)
        SoakTrace.log("host: Studio \(kind) from the phone saved (\(id.prefix(8)))")
        return ["ok": true, "id": id]
    }

    /// One piece of an audio overview; the item appears when the last piece verifies. Resumable: the reply says where
    /// to continue from, which is what is really on disk.
    private func studioAudio(_ b: [String: Any]) async throws -> [String: Any] {
        let id = (b["id"] as? String) ?? "", notebook = (b["notebook_id"] as? String) ?? ""
        try await checkItem(id, notebook: notebook)
        let total = (b["total"] as? Int) ?? 0
        guard total > 0, total <= Self.maxAudioBytes else { throw HostRefusal(400, "bad audio size") }
        guard let piece = Data(base64Encoded: (b["data"] as? String) ?? "") else { throw HostRefusal(400, "bad audio data") }
        let file = "\(id).m4a"
        let final = StudioStore.audioURL(file)
        let part = StudioStore.audioURL(file + ".part")
        if StudioStore.all(notebook: nil).contains(where: { $0.id == id }) { return ["next": total, "done": true] }
        let have = (try? FileManager.default.attributesOfItem(atPath: part.path)[.size] as? Int) ?? 0
        let offset = (b["offset"] as? Int) ?? 0
        if offset != have { return ["next": have, "done": false] }
        if have == 0 { FileManager.default.createFile(atPath: part.path, contents: nil) }
        do {
            let handle = try FileHandle(forWritingTo: part)
            defer { try? handle.close() }
            try handle.seekToEnd()
            try handle.write(contentsOf: piece)
        } catch { throw HostRefusal(500, "could not keep the audio: \(error.localizedDescription)") }
        let size = have + piece.count
        if size < total { return ["next": size, "done": false] }
        let bytes = (try? Data(contentsOf: part)) ?? Data()
        let digest = SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
        guard digest == ((b["sha256"] as? String) ?? "").lowercased() else {
            try? FileManager.default.removeItem(at: part)
            throw HostRefusal(409, "\(file): hash mismatch — discarded, will retry")
        }
        try? FileManager.default.removeItem(at: final)
        do { try FileManager.default.moveItem(at: part, to: final) } catch { throw HostRefusal(500, error.localizedDescription) }
        let duration = (b["duration"] as? Double) ?? 0
        let script = AudioScript(file: file, duration: duration, lines: StudioPull.parseScript((b["script"] as? String) ?? "", duration: duration))
        let title = ((b["title"] as? String) ?? "").trimmingCharacters(in: .whitespaces)
        var item = StudioItem(id: id, notebookId: notebook, kind: .audio, title: title.isEmpty ? "Audio overview" : title,
                              created: ((b["created"] as? String).flatMap(StudioPull.parseDate)) ?? Date(),
                              body: String(decoding: (try? JSONEncoder().encode(script)) ?? Data("{}".utf8), as: UTF8.self))
        item.origin = "phone"; item.mediaFile = file; item.mediaBytes = size
        StudioStore.save(item)
        SoakTrace.log("host: audio overview from the phone saved (\(id.prefix(8)), \(size) bytes)")
        return ["next": size, "done": true]
    }

    private func studioDelete(_ id: String) async throws -> [String: Any] {
        guard id.range(of: Self.idRule, options: .regularExpression) != nil else { throw HostRefusal(400, "bad item id") }
        var removed = 0
        for item in StudioStore.all(notebook: nil) where item.id == id { StudioStore.delete(item); removed += 1 }
        try? FileManager.default.removeItem(at: StudioStore.audioURL("\(id).m4a.part"))
        return ["ok": true, "removed": removed]
    }

    // MARK: Studio, to the phone (diff-only: a small list, then only what the phone lacks)

    private static let stamp: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter(); f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]; return f
    }()

    private func visibleNotebooks() async -> Set<String> {
        guard let store = library.store else { return [] }
        let kept = await store.keptLocal()
        return Set(library.notebooks.map(\.id)).subtracting(kept)
    }

    private func mediaBytes(_ item: StudioItem) -> Int? {
        guard let file = item.mediaFile else { return nil }
        return (try? FileManager.default.attributesOfItem(atPath: StudioStore.audioURL(file).path)[.size] as? Int)
    }

    /// Every item of a shared notebook that the phone can show: id, kind, title, when, a revision, bytes for media.
    private func studioListing() async -> [[String: Any]] {
        let visible = await visibleNotebooks()
        var out: [[String: Any]] = []
        for item in StudioStore.all(notebook: nil) where visible.contains(item.notebookId) {
            let created = Self.stamp.string(from: item.created)
            var row: [String: Any] = ["id": item.id, "notebook_id": item.notebookId, "created": created, "rev": item.rev ?? created]
            switch item.kind {
            case .doc: row["kind"] = "doc"; row["title"] = item.title
            case .cards: row["kind"] = "doc"; row["title"] = item.title.lowercased().hasPrefix("flashcards") ? item.title : "Flashcards: \(item.title)"
            case .quiz: row["kind"] = "quiz"; row["title"] = item.title.lowercased().hasPrefix("quiz") ? item.title : "Quiz: \(item.title)"
            case .visual: row["kind"] = "visual"; row["title"] = item.title
            case .audio, .video:
                guard let bytes = mediaBytes(item) else { continue }             // a file still being made is not offered
                row["kind"] = item.kind == .audio ? "audio" : "video"; row["title"] = item.title; row["bytes"] = bytes
            }
            out.append(row)
        }
        return out
    }

    private func studioItems(_ wanted: [[String: Any]]) async -> [[String: Any]] {
        let visible = await visibleNotebooks()
        let all = Dictionary(StudioStore.all(notebook: nil).map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        var out: [[String: Any]] = []
        for w in wanted.prefix(25) {
            guard let id = w["id"] as? String, let item = all[id], visible.contains(item.notebookId) else { continue }
            switch item.kind {
            case .doc:
                out.append(["kind": "doc", "id": id, "content": item.body])
            case .cards:
                let cards = (try? JSONDecoder().decode([StudioCard].self, from: Data(item.body.utf8))) ?? []
                let text = cards.map { "**\($0.front)**\n\n\($0.back)" }.joined(separator: "\n\n")
                out.append(["kind": "doc", "id": id, "content": text])
            case .quiz:
                let qs = (try? JSONDecoder().decode([StudioQuestion].self, from: Data(item.body.utf8))) ?? []
                out.append(["kind": "quiz", "id": id, "questions": qs.map { q -> [String: Any] in
                    ["question": q.question, "choices": q.choices, "answer": q.answer, "why": q.why.map { $0 as Any } ?? NSNull() as Any]
                }])
            case .visual:
                guard let spec = try? JSONDecoder().decode(VisualSpec.self, from: Data(item.body.utf8)) else { continue }
                out.append(["kind": "visual", "id": id, "svg": VisualSlide.svg(for: spec)])
            case .audio:
                guard let file = item.mediaFile, let bytes = mediaBytes(item) else { continue }
                let script = try? JSONDecoder().decode(AudioScript.self, from: Data(item.body.utf8))
                let lines = (script?.lines ?? []).map { "\(StudioEngine.hostNames[min($0.host, 1)]): \($0.text)" }.joined(separator: "\n")
                out.append(["kind": "audio", "id": id, "script": lines, "duration": script?.duration ?? 0, "path": "audio/\(file)", "bytes": bytes])
            case .video:
                guard let file = item.mediaFile, let bytes = mediaBytes(item) else { continue }
                let info = try? JSONDecoder().decode(VideoInfo.self, from: Data(item.body.utf8))
                out.append(["kind": "video", "id": id, "duration": info?.duration ?? 0, "path": "video/\(file)", "bytes": bytes])
            }
        }
        return out
    }

    private static let fileChunk = 4 * 1024 * 1024

    /// One piece of an audio or video file this Mac made. The path comes from the item, never from the phone.
    private func studioFile(kind: String, id: String, offset: Int) async throws -> [String: Any] {
        guard kind == "audio" || kind == "video" else { throw HostRefusal(400, "unknown kind") }
        let visible = await visibleNotebooks()
        guard let item = StudioStore.all(notebook: nil).first(where: { $0.id == id }), visible.contains(item.notebookId),
              let file = item.mediaFile, let total = mediaBytes(item) else { throw HostRefusal(404, "no such file") }
        let url = StudioStore.audioURL(file)
        guard let handle = try? FileHandle(forReadingFrom: url) else { throw HostRefusal(404, "no such file") }
        defer { try? handle.close() }
        try? handle.seek(toOffset: UInt64(max(0, offset)))
        let data = (try? handle.read(upToCount: Self.fileChunk)) ?? Data()
        var out: [String: Any] = ["path": "\(kind)/\(file)", "offset": max(0, offset), "total": total, "data": data.base64EncodedString()]
        out["sha256"] = NSNull()
        return out
    }
}
