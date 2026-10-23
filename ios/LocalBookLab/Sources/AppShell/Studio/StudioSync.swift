import CryptoKit
import Foundation

/// Sends what is made in Studio on this phone to the paired Mac, where it is stored with the Mac's own Studio items
/// (so it reaches the other Macs and the Canvas). Safe to call any time: it sends only what the Mac doesn't have,
/// resumes a half-sent audio file, and stays quiet when the Mac is out of reach.
enum StudioSync {
    private static var ledgerURL: URL { StudioStore.folder.appendingPathComponent("sent-to-mac.json") }

    static var hiddenURL: URL { StudioStore.folder.appendingPathComponent("hidden-mac-items.json") }
    private static var deleteURL: URL { StudioStore.folder.appendingPathComponent("delete-on-mac.json") }

    static func read(_ url: URL) -> Set<String> {
        guard let data = try? Data(contentsOf: url), let ids = try? JSONDecoder().decode([String].self, from: data) else { return [] }
        return Set(ids)
    }

    static func write(_ ids: Set<String>, to url: URL) {
        if let data = try? JSONEncoder().encode(Array(ids)) { try? data.write(to: url, options: .atomic) }
    }

    /// Deletes an item here, and queues the delete for the Mac if the Mac ever received it.
    static func delete(_ item: StudioItem) {
        if item.origin == "mac" {                                  // the Mac keeps its own; only hide it on this phone
            var hidden = read(hiddenURL); hidden.insert(item.id); write(hidden, to: hiddenURL)
            StudioStore.delete(item); return
        }
        var sent = sentIds()
        if sent.remove(item.id) != nil {
            write(sent, to: ledgerURL)
            var pending = read(deleteURL); pending.insert(item.id); write(pending, to: deleteURL)
        }
        StudioStore.delete(item)
    }

    static func sentIds() -> Set<String> {
        guard let data = try? Data(contentsOf: ledgerURL), let ids = try? JSONDecoder().decode([String].self, from: data) else { return [] }
        return Set(ids)
    }

    private static func markSent(_ id: String) {
        var ids = sentIds(); ids.insert(id)
        if let data = try? JSONEncoder().encode(Array(ids)) { try? data.write(to: ledgerURL, options: .atomic) }
    }

    /// Returns how many items are still waiting to be sent.
    @discardableResult
    static func flush() async -> Int {
        guard let mac = MacLink.paired else { return 0 }
        var pendingDeletes = read(deleteURL)
        for id in pendingDeletes {
            do {
                _ = try await MacLink.signedCall(mac, path: "/phone/library/studio/delete", json: ["id": id], timeout: 30)
                pendingDeletes.remove(id); write(pendingDeletes, to: deleteURL)
            } catch { break }                                 // out of reach: the delete waits
        }
        let sent = sentIds()
        let waiting = StudioStore.all(notebook: nil).filter { !sent.contains($0.id) && $0.origin != "mac" }
        var left = waiting.count
        for item in waiting {
            do {
                try await send(item, to: mac)
                markSent(item.id); left -= 1
            } catch let e as SendError where e == .unknownNotebook {
                markSent(item.id); left -= 1                 // the Mac doesn't share this notebook: nothing to send
            } catch { return left }                           // out of reach: try again next time
        }
        return left
    }

    static var deleteQueueURL: URL { deleteURL }

    private enum SendError: Error { case unknownNotebook }

    private static func send(_ item: StudioItem, to mac: PairedMac) async throws {
        let created = ISO8601DateFormatter().string(from: item.created)
        func put(_ kind: String, _ body: Any) async throws {
            let payload: [String: Any] = ["kind": kind, "id": item.id, "notebook_id": item.notebookId, "title": item.title,
                                          "topic": item.title, "created": created, "body": body]
            do { _ = try await MacLink.signedCall(mac, path: "/phone/library/studio/put", json: payload, timeout: 60) }
            catch MacLinkError.http(404, _) { throw SendError.unknownNotebook }
        }
        switch item.kind {
        case .doc: try await put("doc", item.body)
        case .cards: try await put("cards", try JSONSerialization.jsonObject(with: Data(item.body.utf8)))
        case .quiz: try await put("quiz", try JSONSerialization.jsonObject(with: Data(item.body.utf8)))
        case .audio: try await sendAudio(item, to: mac, created: created)
        case .visual, .video: break                            // sent once the Mac and phone share the format
        }
    }

    private static func sendAudio(_ item: StudioItem, to mac: PairedMac, created: String) async throws {
        let script = try JSONDecoder().decode(AudioScript.self, from: Data(item.body.utf8))
        let data = try Data(contentsOf: StudioStore.audioURL(script.file))
        let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        let text = script.lines.map { "\(StudioEngine.hostNames[$0.host]): \($0.text)" }.joined(separator: "\n")
        var offset = 0
        while true {
            let end = min(data.count, offset + 600_000)
            let payload: [String: Any] = [
                "id": item.id, "notebook_id": item.notebookId, "title": item.title, "topic": item.title, "created": created,
                "script": text, "duration": script.duration, "offset": offset, "total": data.count, "sha256": digest,
                "data": data[offset..<end].base64EncodedString(),
            ]
            let reply: Data
            do { reply = try await MacLink.signedCall(mac, path: "/phone/library/studio/audio", json: payload, timeout: 90) }
            catch MacLinkError.http(404, _) { throw SendError.unknownNotebook }
            let r = try JSONSerialization.jsonObject(with: reply) as? [String: Any]
            if r?["done"] as? Bool == true { return }
            guard let next = r?["next"] as? Int else { throw URLError(.cannotParseResponse) }
            offset = next                                      // the Mac says where to continue (it may already have part)
        }
    }
}
