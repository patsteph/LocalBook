import Foundation

/// Brings what the Mac has made in Studio onto this phone's shelf. Diff-only: it lists the Mac's items (a few bytes
/// each), fetches the text of only the ones it lacks or whose revision changed, and removes ones the Mac deleted.
/// Audio and video files are large, so they download when you ask, not before.
enum StudioPull {
    private struct Remote: Decodable {
        let kind: String, id: String, notebook_id: String, title: String
        let created: String?, rev: String?, bytes: Int?
    }
    private struct Listing: Decodable { let items: [Remote] }

    /// Returns true when the Mac answered (even if nothing changed).
    @discardableResult
    static func pull() async -> Bool {
        guard let mac = MacLink.paired else { return false }
        do {
            let data = try await MacLink.signedCall(mac, path: "/phone/library/studio/list", json: [:], timeout: 60)
            let remote = try JSONDecoder().decode(Listing.self, from: data).items
            let local = Dictionary(uniqueKeysWithValues: StudioStore.all(notebook: nil).map { ($0.id, $0) })
            let hidden = StudioSync.read(StudioSync.hiddenURL)
            let deleting = StudioSync.read(StudioSync.deleteQueueURL)
            let remoteIds = Set(remote.map(\.id))

            // Mac items the Mac no longer has.
            for (id, item) in local where item.origin == "mac" && !remoteIds.contains(id) { StudioStore.delete(item) }

            // New or changed on the Mac.
            let wanted = remote.filter { r in
                if hidden.contains(r.id) || deleting.contains(r.id) { return false }
                guard let have = local[r.id] else { return true }
                guard have.origin == "mac" else { return false }
                if have.rev != r.rev { return true }
                // Earlier versions kept the wrong date; refetch once so the real one lands.
                if let real = r.created.flatMap(parseDate), abs(real.timeIntervalSince(have.created)) > 60 { return true }
                return false
            }
            for start in stride(from: 0, to: wanted.count, by: 20) {
                let batch = Array(wanted[start..<min(wanted.count, start + 20)])
                let ask = batch.map { ["kind": $0.kind, "id": $0.id] }
                let reply = try await MacLink.signedCall(mac, path: "/phone/library/studio/get", json: ["items": ask], timeout: 90)
                guard let got = (try JSONSerialization.jsonObject(with: reply) as? [String: Any])?["items"] as? [[String: Any]] else { continue }
                let byId = Dictionary(uniqueKeysWithValues: got.compactMap { g in (g["id"] as? String).map { ($0, g) } })
                for r in batch {
                    guard let g = byId[r.id], var item = make(r, g) else { continue }
                    if let old = local[r.id], old.mediaFile != nil, item.mediaFile == old.mediaFile { item.mediaBytes = old.mediaBytes }
                    StudioStore.save(item)
                }
            }
            return true
        } catch { return false }
    }

    /// The Mac's timestamps come as "2026-10-20T12:34:56.123456" (no zone, microseconds) or with a zone; read all forms.
    /// Zone-less times are UTC, as the Mac stores them.
    static func parseDate(_ text: String) -> Date? {
        var t = text.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: " ", with: "T")
        if let dot = t.firstIndex(of: ".") {
            var end = t.index(after: dot)
            while end < t.endIndex, t[end].isNumber { end = t.index(after: end) }
            let frac = String(t[t.index(after: dot)..<end].prefix(3))
            t = String(t[..<dot]) + "." + frac.padding(toLength: 3, withPad: "0", startingAt: 0) + t[end...]
        }
        let hasZone = t.hasSuffix("Z") || t.range(of: #"[+-]\d\d:?\d\d$"#, options: .regularExpression) != nil
        if !hasZone { t += "Z" }
        let f = ISO8601DateFormatter()
        for options: ISO8601DateFormatter.Options in [[.withInternetDateTime, .withFractionalSeconds], [.withInternetDateTime]] {
            f.formatOptions = options
            if let d = f.date(from: t) { return d }
        }
        return nil
    }

    private static func make(_ r: Remote, _ g: [String: Any]) -> StudioItem? {
        let created = r.created.flatMap(parseDate) ?? Date()
        var item = StudioItem(id: r.id, notebookId: r.notebook_id, kind: .doc, title: r.title, created: created, body: "")
        item.origin = "mac"; item.rev = r.rev
        let encoder = JSONEncoder()
        switch r.kind {
        case "doc":
            guard let text = g["content"] as? String, !text.isEmpty else { return nil }
            item.kind = .doc; item.body = text
        case "quiz":
            let qs: [StudioQuestion] = (g["questions"] as? [[String: Any]] ?? []).compactMap { q in
                guard let text = q["question"] as? String, let choices = q["choices"] as? [String], let a = q["answer"] as? Int else { return nil }
                return StudioQuestion(question: text, choices: choices, answer: a, why: q["why"] as? String)
            }
            guard !qs.isEmpty, let data = try? encoder.encode(qs) else { return nil }
            item.kind = .quiz; item.body = String(decoding: data, as: UTF8.self)
        case "visual":
            guard let svg = g["svg"] as? String, svg.contains("<svg") else { return nil }
            item.kind = .visual; item.body = svg
        case "audio":
            let duration = (g["duration"] as? Double) ?? 0
            let path = g["path"] as? String ?? ""
            let file = r.id + "." + ((path as NSString).pathExtension.isEmpty ? "wav" : (path as NSString).pathExtension)
            let script = AudioScript(file: file, duration: duration, lines: parseScript(g["script"] as? String ?? "", duration: duration))
            guard let data = try? encoder.encode(script) else { return nil }
            item.kind = .audio; item.body = String(decoding: data, as: UTF8.self)
            item.mediaFile = file; item.mediaBytes = g["bytes"] as? Int ?? r.bytes
        case "video":
            let path = g["path"] as? String ?? ""
            let file = r.id + "." + ((path as NSString).pathExtension.isEmpty ? "mp4" : (path as NSString).pathExtension)
            guard let data = try? encoder.encode(VideoInfo(file: file, duration: (g["duration"] as? Double) ?? 0)) else { return nil }
            item.kind = .video; item.body = String(decoding: data, as: UTF8.self)
            item.mediaFile = file; item.mediaBytes = g["bytes"] as? Int ?? r.bytes
        default: return nil
        }
        return item
    }

    /// "Name: words" lines become spoken lines; speakers alternate between the two hosts by first appearance.
    /// Start times are spread by length, since the Mac's audio file carries no markers.
    static func parseScript(_ script: String, duration: Double) -> [AudioScript.Line] {
        var speakers: [String] = []
        var parsed: [(host: Int, text: String)] = []
        for raw in script.split(separator: "\n") {
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard !line.isEmpty else { continue }
            if let colon = line.firstIndex(of: ":"), line.distance(from: line.startIndex, to: colon) <= 24 {
                let name = String(line[..<colon]).trimmingCharacters(in: CharacterSet(charactersIn: "*# ").union(.whitespaces))
                let text = String(line[line.index(after: colon)...]).trimmingCharacters(in: CharacterSet(charactersIn: "* ").union(.whitespaces))
                if !text.isEmpty {
                    if !speakers.contains(name) { speakers.append(name) }
                    parsed.append((speakers.firstIndex(of: name)! % 2, text)); continue
                }
            }
            parsed.append((parsed.last?.host ?? 0, line))
        }
        let total = max(1, parsed.reduce(0) { $0 + $1.text.count })
        var used = 0
        return parsed.map { p in
            defer { used += p.text.count }
            return AudioScript.Line(host: p.host, text: p.text, start: duration * Double(used) / Double(total))
        }
    }

    /// Downloads a Mac item's audio or video file. `progress` is 0…1. Throws if the Mac can't be reached.
    static func download(_ item: StudioItem, progress: @escaping @MainActor (Double) -> Void) async throws {
        guard let mac = MacLink.paired, let media = item.mediaFile else { throw URLError(.cannotConnectToHost) }
        let kind = item.kind == .video ? "video" : "audio"
        let url = StudioStore.audioURL(media)
        let part = url.appendingPathExtension("part")
        try? FileManager.default.removeItem(at: part)
        FileManager.default.createFile(atPath: part.path, contents: nil)
        let handle = try FileHandle(forWritingTo: part)
        defer { try? handle.close() }
        var offset = 0, total = Int.max
        while offset < total {
            let reply = try await MacLink.signedCall(mac, path: "/phone/library/studio/file",
                                                     json: ["kind": kind, "id": item.id, "offset": offset], timeout: 120)
            guard let r = try JSONSerialization.jsonObject(with: reply) as? [String: Any],
                  let b64 = r["data"] as? String, let chunk = Data(base64Encoded: b64), !chunk.isEmpty else { throw URLError(.cannotParseResponse) }
            total = r["total"] as? Int ?? offset + chunk.count
            try handle.write(contentsOf: chunk)
            offset += chunk.count
            await progress(min(1, Double(offset) / Double(max(1, total))))
        }
        try? FileManager.default.removeItem(at: url)
        try FileManager.default.moveItem(at: part, to: url)
    }
}
