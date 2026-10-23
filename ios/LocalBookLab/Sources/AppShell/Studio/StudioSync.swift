import CryptoKit
import Foundation

/// Sends what is made in Studio on this phone to the paired Mac, where it is stored with the Mac's own Studio items
/// (so it reaches the other Macs and the Canvas). Safe to call any time: it sends only what the Mac doesn't have,
/// resumes a half-sent audio file, and stays quiet when the Mac is out of reach.
enum StudioSync {
    private static var ledgerURL: URL { StudioStore.folder.appendingPathComponent("sent-to-mac.json") }

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
        let sent = sentIds()
        let waiting = StudioStore.all(notebook: nil).filter { !sent.contains($0.id) }
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
        case .visual: break                                    // sent once the Mac and phone share a visual format
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
