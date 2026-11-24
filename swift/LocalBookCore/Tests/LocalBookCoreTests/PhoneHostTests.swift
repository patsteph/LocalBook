import CryptoKit
import Foundation
import Testing
@testable import LocalBookCore

/// The host's pure parts: reading a request off the wire, the v2 signature, the nonce and failure rules, and what the
/// library hands a phone (source digests, the notebook digest, chunk pages).
@Suite("Phone link host")
struct PhoneHostTests {
    // MARK: HTTP

    @Test("a request arriving in pieces is read once it is whole, and the query is not part of the path")
    func parseInPieces() throws {
        let body = Data("{\"notebook_id\":\"nb\"}".utf8)
        let head = "POST /phone/library/sources?x=1 HTTP/1.1\r\nHost: mac\r\nContent-Type: application/json\r\nX-LB-Device: phone-1\r\nContent-Length: \(body.count)\r\n\r\n"
        var buffer = Data(head.utf8)
        #expect(try HTTPWire.parse(buffer) == nil)
        buffer.append(body.prefix(5))
        #expect(try HTTPWire.parse(buffer) == nil)
        buffer.append(body.dropFirst(5))
        buffer.append(Data("GET / HTTP/1.1\r\n".utf8))              // the start of something else must not be consumed
        let (request, consumed) = try #require(try HTTPWire.parse(buffer))
        #expect(request.method == "POST")
        #expect(request.path == "/phone/library/sources")
        #expect(request.headers["x-lb-device"] == "phone-1")
        #expect(request.body == body)
        #expect(consumed == head.utf8.count + body.count)
    }

    @Test("a reply carries its length and closes the connection")
    func response() {
        let data = HTTPWire.response(status: 403, json: Data("{\"detail\":\"no\"}".utf8))
        let text = String(decoding: data, as: UTF8.self)
        #expect(text.hasPrefix("HTTP/1.1 403 Forbidden\r\n"))
        #expect(text.contains("Content-Length: 15\r\n"))
        #expect(text.contains("Connection: close\r\n\r\n{\"detail\":\"no\"}"))
    }

    @Test("a body past the limit or a broken request line is refused, not waited for")
    func limits() {
        let huge = Data("POST /x HTTP/1.1\r\nContent-Length: 99999999999\r\n\r\n".utf8)
        #expect(throws: HTTPWire.ParseError.tooLarge) { try HTTPWire.parse(huge) }
        let broken = Data("HELLO\r\n\r\n".utf8)
        #expect(throws: HTTPWire.ParseError.malformed) { try HTTPWire.parse(broken) }
    }

    // MARK: signatures

    @Test("the message the phone signs is the Python Mac's, line for line")
    func messageLayout() {
        let m = PhoneSignature.message(method: "post", path: "/phone/hello", ts: "1700000000", nonce: "ab" * 8,
                                       macId: "mac-1", macFingerprint: "ff", body: Data())
        let emptyHash = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        #expect(String(decoding: m, as: UTF8.self) == "LBP2\nPOST\n/phone/hello\n1700000000\n\(String(repeating: "ab", count: 8))\nmac-1\nff\n\(emptyHash)")
    }

    @Test("a signature verifies for the Mac it was made for, and for nothing else")
    func verify() throws {
        let key = P256.Signing.PrivateKey()
        let body = Data("{\"a\":1}".utf8)
        let now = Date()
        let ts = String(Int(now.timeIntervalSince1970))
        let nonce = String(repeating: "0f", count: 8)
        let message = PhoneSignature.message(method: "POST", path: "/phone/library/manifest", ts: ts, nonce: nonce, macId: "mac-a", macFingerprint: "aa", body: body)
        let sig = try key.signature(for: message).derRepresentation.base64EncodedString()
        func check(path: String = "/phone/library/manifest", ts t: String = ts, nonce n: String = nonce, body b: Data = body, mac: String = "mac-a", fp: String = "aa", at: Date = now) -> Bool {
            PhoneSignature.verify(key: key.publicKey, method: "POST", path: path, ts: t, nonce: n, body: b, signatureBase64: sig, macId: mac, macFingerprint: fp, now: at)
        }
        #expect(check())
        #expect(!check(path: "/phone/library/chunks"))                 // another route
        #expect(!check(body: Data("{\"a\":2}".utf8)))                  // another body
        #expect(!check(mac: "mac-b"))                                 // redirected to another Mac
        #expect(!check(fp: "bb"))
        #expect(!check(at: now.addingTimeInterval(PhoneSignature.maxSkew + 5)))   // stale
        #expect(!check(nonce: "short"))
    }

    @Test("a nonce is good once; an address is blocked after six failures and freed later")
    func nonceAndFailures() {
        let nonces = NonceCache(ttl: 60)
        #expect(nonces.firstUse("p:1"))
        #expect(!nonces.firstUse("p:1"))
        #expect(nonces.firstUse("p:2"))
        #expect(nonces.firstUse("p:1", now: Date().addingTimeInterval(61)))
        let limiter = FailureLimiter(maxFailures: 6, window: 60, block: 120)
        let t0 = Date()
        for i in 0..<5 { limiter.failure("ip", now: t0.addingTimeInterval(Double(i))) }
        #expect(!limiter.blocked("ip", now: t0.addingTimeInterval(5)))
        limiter.failure("ip", now: t0.addingTimeInterval(5))
        #expect(limiter.blocked("ip", now: t0.addingTimeInterval(6)))
        #expect(!limiter.blocked("other", now: t0.addingTimeInterval(6)))
        #expect(!limiter.blocked("ip", now: t0.addingTimeInterval(5 + 121)))
    }

    @Test("the pairing code is the same from either side")
    func pairingCode() {
        #expect(MacLink.sas("aa", "bb") == MacLink.sas("bb", "aa"))
        #expect(MacLink.sas("aa", "bb").count == 6)
        #expect(MacLink.sas("aa", "bb") != MacLink.sas("aa", "bc"))
    }

    // MARK: the library a phone reads

    private func freshStore() async throws -> LibraryStore {
        let url = FileManager.default.temporaryDirectory.appending(path: "host-\(UUID().uuidString).sqlite")
        let store = try LibraryStore(path: url)
        try await store.prepare()
        return store
    }

    private func vector(_ fill: UInt8 = 1) -> Data { Data(repeating: fill, count: LibrarySync.expectedDims * 2) }

    private func chunk(_ nb: String, _ source: String, _ index: Int, _ text: String, summary: Bool = false) -> LibraryChunk {
        .init(notebookId: nb, sourceId: source, chunkIndex: index, text: text, summary: summary, filename: "\(source).pdf", sourceType: "pdf", vector: vector())
    }

    @Test("source digests change with the text, the notebook digest with its sources, and pages walk every row in order")
    func digestsAndPages() async throws {
        let store = try await freshStore()
        let rows = [chunk("nb", "s1", -1, "summary", summary: true), chunk("nb", "s1", 0, "one"), chunk("nb", "s1", 1, "two"),
                    chunk("nb", "s2", 0, "alpha"), chunk("nb", "s2", 1, "beta"), chunk("nb", "s2", 2, "gamma")]
        try await store.apply(notebook: "nb", title: "Harbor", digest: "x", removed: [], resent: [], chunks: rows, hashes: ["s1": "h1", "s2": "h2"])
        let sources = await store.hostSources(notebook: "nb")
        #expect(sources.map(\.sourceId) == ["s1", "s2"])
        #expect(sources.map(\.chunks) == [3, 3])
        #expect(sources[0].hash.count == 24)
        #expect(sources[0].hash != sources[1].hash)
        let digest = LibraryStore.hostDigest(sources)
        #expect(digest.count == 24)

        try await store.apply(notebook: "nb", title: "Harbor", digest: "x", removed: [], resent: ["s2"],
                              chunks: [chunk("nb", "s2", 0, "alpha"), chunk("nb", "s2", 1, "beta changed"), chunk("nb", "s2", 2, "gamma")], hashes: ["s2": "h2b"])
        let after = await store.hostSources(notebook: "nb")
        #expect(after[0].hash == sources[0].hash)
        #expect(after[1].hash != sources[1].hash)
        #expect(LibraryStore.hostDigest(after) != digest)

        // Pages of two, in (source_id, chunk_index) order, the summary row first.
        var got: [(String, Int)] = []
        var cursor: (sourceId: String, chunkIndex: Int)?
        var pages = 0
        repeat {
            let page = await store.hostRows(notebook: "nb", sources: nil, after: cursor, limit: 2)
            pages += 1
            got += page.rows.map { ($0.sourceId, $0.chunkIndex) }
            cursor = page.more ? page.rows.last.map { ($0.sourceId, $0.chunkIndex) } : nil
        } while cursor != nil
        #expect(pages == 3)
        #expect(got.map { "\($0.0):\($0.1)" } == ["s1:-1", "s1:0", "s1:1", "s2:0", "s2:1", "s2:2"])
        let first = await store.hostRows(notebook: "nb", sources: nil, after: nil, limit: 2)
        #expect(first.rows.first?.summary == true)
        #expect(first.rows.first?.vector.count == LibrarySync.expectedDims * 2)

        // Only the sources asked for.
        let onlyS2 = await store.hostRows(notebook: "nb", sources: ["s2"], after: nil, limit: 100)
        #expect(onlyS2.rows.map(\.sourceId) == ["s2", "s2", "s2"])
        #expect(!onlyS2.more)
        let none = await store.hostRows(notebook: "nb", sources: ["nope"], after: nil, limit: 100)
        #expect(none.rows.isEmpty)
    }

    @Test("a notebook kept on this Mac is marked and unmarked")
    func keptLocal() async throws {
        let store = try await freshStore()
        try await store.createNotebook(id: "nb", title: "Private", color: nil)
        #expect(!(await store.isKeptLocal("nb")))
        try await store.setKeptLocal("nb", true)
        #expect(await store.isKeptLocal("nb"))
        #expect(await store.keptLocal() == ["nb"])
        try await store.setKeptLocal("nb", false)
        #expect(await store.keptLocal().isEmpty)
    }

    @Test("the pinned devices survive a reload, and a device pairing again replaces its certificate")
    func phoneStore() {
        let url = FileManager.default.temporaryDirectory.appending(path: "phones-\(UUID().uuidString).json")
        let store = PhoneHostStore(url: url)
        store.pin(PairedPhone(deviceId: "phone-1", name: "iPhone", certPEM: "A", fingerprint: "fa"))
        store.pin(PairedPhone(deviceId: "phone-2", name: "iPhone 2", certPEM: "B", fingerprint: "fb"))
        store.pin(PairedPhone(deviceId: "phone-1", name: "iPhone", certPEM: "C", fingerprint: "fc"))
        let again = PhoneHostStore(url: url)
        #expect(again.phones().count == 2)
        #expect(again.phone("phone-1")?.certPEM == "C")
        again.touch("phone-2")
        #expect(again.phone("phone-2")?.lastSeen != nil)
        again.forget("phone-1")
        #expect(PhoneHostStore(url: url).phones().map(\.deviceId) == ["phone-2"])
    }

    @Test("the manifest's embedder is the one every device shares")
    func embedder() {
        let key = PhoneHost.embedderKey
        #expect(key["model_id"] as? String == LibrarySync.expectedModel)
        #expect(key["dims"] as? Int == LibrarySync.expectedDims)
        #expect(key["document_template"] as? String == "title: {title} | text: {text}")
    }
}

private func * (s: String, n: Int) -> String { String(repeating: s, count: n) }
