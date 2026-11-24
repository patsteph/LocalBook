import CryptoKit
import Foundation
import Security

/// A refusal the host sends back as a status and a `detail` line (what FastAPI sent from the Python Mac).
public struct HostRefusal: Error, Sendable {
    public let status: Int
    public let message: String
    public init(_ status: Int, _ message: String) { self.status = status; self.message = message }
}

/// The routes the library alone cannot serve (a note to index, an edit, a Studio item): the app answers them.
/// `handle` returns the JSON reply, nil for a path it does not know, or throws a `HostRefusal`.
public protocol PhoneHostDelegate: Sendable {
    func handle(path: String, body: Data, from phone: PairedPhone) async throws -> Data?
    /// The answer recipe to publish when this library has none of its own (never paired with a Python Mac).
    func fallbackRecipe() async -> Data?
}

/// This Mac as the host of the phone link (Phase 4, first slice): the pairing port, open for five minutes at a time,
/// and the signed sync port the phone talks to afterwards. The same ports, TLS pinning, request signatures, nonce
/// and failure rules as the Python Mac's `peer.py`, so the shipped iPhone app pairs with this Mac unchanged.
///
/// Reading the library is answered here from `LibraryStore`; everything that changes the library goes through the
/// delegate, which runs the same code the Mac's own window uses.
public actor PhoneHost {
    public static let pairPort = UInt16(MacLink.macSyncPort + MacLink.pairPortOffset)
    public static let syncPort = UInt16(MacLink.macSyncPort + MacLink.syncPortOffset)
    public static let pairingWindow: TimeInterval = 300
    public static let proto = 1

    public enum State: Sendable, Equatable {
        case off
        case listening
        case failed(String)
    }

    private let store: LibraryStore
    private let phones: PhoneHostStore
    private let delegate: any PhoneHostDelegate
    private var sync: HTTPServer?
    private var pairing: HTTPServer?
    private var closePairingTask: Task<Void, Never>?
    private let nonces = NonceCache()
    private let failures = FailureLimiter()
    private var identity: SecIdentity?
    private var certPEM = ""
    private var fingerprint = ""

    public private(set) var state: State = .off
    public private(set) var pairingOpenUntil: Date?
    public private(set) var requests: [PairingRequest] = []
    /// Called after anything a Settings pane shows has changed (state, the pairing window, a request, a phone).
    public var onChange: (@Sendable () -> Void)?

    public init(store: LibraryStore, phones: PhoneHostStore = .shared, delegate: any PhoneHostDelegate) {
        self.store = store; self.phones = phones; self.delegate = delegate
    }

    public func setOnChange(_ f: @Sendable @escaping () -> Void) { onChange = f }
    private func changed() { onChange?() }

    public var pairedPhones: [PairedPhone] { phones.phones() }
    public var isListening: Bool { state == .listening }

    // MARK: lifecycle

    /// Opens the sync port and advertises this Mac on the network. Throws when the port is taken (the LocalBook
    /// already on this Mac, with its own phone link up) or the Mac's certificate cannot be used.
    public func start() async throws {
        if sync != nil { return }
        do {
            let id = try PhoneIdentity.identity()
            let der = SecCertificateCopyData(try PhoneIdentity.certificate()) as Data
            identity = id
            certPEM = PEM.pem(der)
            fingerprint = MacLink.fingerprint(of: der)
            let advert = HTTPServer.Advert(name: PhoneIdentity.deviceName, type: MacBrowser.service, txt: ["id": PhoneIdentity.deviceId])
            let server = HTTPServer(port: Self.syncPort, identity: id, advert: advert) { [weak self] request, remote in
                guard let self else { return HTTPWire.problem(503, "host stopped") }
                return await self.handleSync(request, from: remote)
            }
            try await server.start()
            sync = server
            state = .listening
            SoakTrace.log("host: listening on \(Self.syncPort) as \(PhoneIdentity.deviceName)")
        } catch {
            state = .failed(error.localizedDescription)
            SoakTrace.log("host: could not start: \(error.localizedDescription)")
            changed()
            throw error
        }
        changed()
    }

    public func stop() {
        closePairing()
        sync?.stop(); sync = nil
        state = .off
        changed()
    }

    /// Lets a device pair for the next five minutes: the pairing port opens, and closes by itself.
    public func openPairing() async throws -> Date {
        if sync == nil { try await start() }
        guard let identity else { throw HTTPServer.ServerError.identity }
        if pairing == nil {
            let server = HTTPServer(port: Self.pairPort, identity: identity) { [weak self] request, remote in
                guard let self else { return HTTPWire.problem(503, "host stopped") }
                return await self.handlePair(request, from: remote)
            }
            try await server.start()
            pairing = server
        }
        let until = Date().addingTimeInterval(Self.pairingWindow)
        pairingOpenUntil = until
        closePairingTask?.cancel()
        closePairingTask = Task { [weak self] in
            try? await Task.sleep(for: .seconds(Self.pairingWindow))
            guard !Task.isCancelled else { return }
            await self?.closePairingIfExpired()
        }
        changed()
        return until
    }

    private func closePairingIfExpired() {
        if let until = pairingOpenUntil, until <= Date() { closePairing() }
    }

    public func closePairing() {
        closePairingTask?.cancel(); closePairingTask = nil
        pairing?.stop(); pairing = nil
        pairingOpenUntil = nil
        requests.removeAll()
        changed()
    }

    /// The person compared the codes and they match: the device is pinned and its signed calls are accepted.
    @discardableResult
    public func confirm(_ requestId: String) -> PairedPhone? {
        guard let r = requests.first(where: { $0.id == requestId }) else { return nil }
        let phone = PairedPhone(deviceId: r.deviceId, name: r.name, certPEM: r.certPEM, fingerprint: r.fingerprint)
        phones.pin(phone)
        requests.removeAll { $0.id == requestId || $0.deviceId == r.deviceId }
        SoakTrace.log("host: paired \(r.name) (\(r.deviceId.prefix(12)))")
        if requests.isEmpty { closePairing() } else { changed() }
        return phone
    }

    public func reject(_ requestId: String) {
        requests.removeAll { $0.id == requestId }
        changed()
    }

    public func forget(_ deviceId: String) {
        phones.forget(deviceId)
        SoakTrace.log("host: forgot \(deviceId.prefix(12))")
        changed()
    }

    /// A notebook is either synced to every paired device or kept on this Mac; this flips one.
    public func setKeptLocal(_ notebook: String, _ local: Bool) async {
        try? await store.setKeptLocal(notebook, local)
        changed()
    }

    // MARK: the pairing port

    private func handlePair(_ req: HTTPRequest, from ip: String) async -> (Int, Data) {
        guard req.method == "POST", req.path == "/pair/hello" else { return HTTPWire.problem(404, "unknown route") }
        guard let until = pairingOpenUntil, until > Date() else { return HTTPWire.problem(403, "pairing is not open on this Mac") }
        let body = (try? JSONSerialization.jsonObject(with: req.body) as? [String: Any]) ?? [:]
        guard let deviceId = body["device_id"] as? String, !deviceId.isEmpty, let pem = body["cert_pem"] as? String,
              PhoneSignature.publicKey(fromCertificatePEM: pem) != nil, let theirs = PhoneSignature.fingerprint(ofCertificatePEM: pem) else {
            return HTTPWire.problem(400, "a phone must present a P-256 certificate")
        }
        let name = (body["name"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? "iPhone"
        let request = PairingRequest(id: UUID().uuidString, deviceId: deviceId, name: name, certPEM: pem, fingerprint: theirs,
                                     code: MacLink.sas(fingerprint, theirs), host: ip, createdAt: Date())
        requests.removeAll { $0.deviceId == deviceId }
        requests.append(request)
        SoakTrace.log("host: pairing request from \(name) at \(ip)")
        changed()
        return json(["device_id": PhoneIdentity.deviceId, "name": PhoneIdentity.deviceName, "cert_pem": certPEM,
                     "sync_port": Int(Self.syncPort), "proto": Self.proto])
    }

    // MARK: the sync port

    private func handleSync(_ req: HTTPRequest, from ip: String) async -> (Int, Data) {
        guard req.method == "POST" else { return HTTPWire.problem(404, "unknown route") }
        if failures.blocked(ip) {
            SoakTrace.log("host: \(req.path) refused, too many failed attempts from \(ip)")
            return HTTPWire.problem(429, "too many failed attempts; try again later")
        }
        let deviceId = req.headers["x-lb-device"] ?? ""
        let nonce = req.headers["x-lb-nonce"] ?? ""
        guard let phone = phones.phone(deviceId), let key = PhoneSignature.publicKey(fromCertificatePEM: phone.certPEM),
              PhoneSignature.verify(key: key, method: req.method, path: req.path, ts: req.headers["x-lb-time"] ?? "0", nonce: nonce,
                                    body: req.body, signatureBase64: req.headers["x-lb-sig"] ?? "",
                                    macId: PhoneIdentity.deviceId, macFingerprint: fingerprint) else {
            failures.failure(ip)
            SoakTrace.log("host: \(req.path) refused: not a paired phone, or a bad signature (\(deviceId.prefix(8)) from \(ip))")
            return HTTPWire.problem(403, "not a paired phone, or a bad signature")
        }
        guard nonces.firstUse("\(deviceId):\(nonce)") else {
            failures.failure(ip)
            return HTTPWire.problem(403, "request already used")
        }
        phones.touch(deviceId)
        let started = Date()
        let out = await route(req, phone: phone)
        let ms = Int(Date().timeIntervalSince(started) * 1000)
        if out.0 != 200 || ms > 2000 { SoakTrace.log("host: \(req.path) -> \(out.0) in \(ms) ms") }
        return out
    }

    private func route(_ req: HTTPRequest, phone: PairedPhone) async -> (Int, Data) {
        let body = (try? JSONSerialization.jsonObject(with: req.body) as? [String: Any]) ?? [:]
        let notebook = (body["notebook_id"] as? String) ?? ""
        do {
            switch req.path {
            case "/phone/hello":
                return json(["device_id": PhoneIdentity.deviceId, "name": PhoneIdentity.deviceName, "proto": Self.proto,
                             "models": ["embedding_model": LibrarySync.expectedModel], "you": phone.deviceId])
            case "/phone/library/manifest":
                return json(await manifest())
            case "/phone/library/sources":
                guard await visible(notebook) else { throw HostRefusal(404, "no such notebook") }
                let sources = await store.hostSources(notebook: notebook).map { s -> [String: Any] in
                    var row: [String: Any] = ["source_id": s.sourceId, "filename": s.filename, "source_type": s.sourceType, "chunks": s.chunks, "hash": s.hash]
                    if let c = s.created { row["created"] = c }
                    return row
                }
                return json(["notebook_id": notebook, "sources": sources])
            case "/phone/library/chunks":
                guard await visible(notebook) else { throw HostRefusal(404, "no such notebook") }
                return json(await chunks(notebook: notebook, body: body))
            case "/phone/library/recipe":
                if let own = await store.meta("recipe"), !own.isEmpty { return (200, Data(own.utf8)) }
                if let data = await delegate.fallbackRecipe() { return (200, data) }
                throw HostRefusal(404, "this Mac has no answer recipe yet")
            case "/phone/library/personal":
                guard await visible(notebook) else { throw HostRefusal(404, "no such notebook") }
                if let own = await store.meta("personal:" + notebook), !own.isEmpty { return (200, Data(own.utf8)) }
                return json(["notebook_id": notebook, "user_context": "", "core_memory": "", "entities": [], "relationships": [], "version": "none"])
            case "/phone/library/topics":
                guard await visible(notebook) else { return json(["topics": [], "sources": 0, "untagged": 0]) }
                let topics = await store.topics(notebook: notebook).map { t -> [String: Any] in
                    ["id": t.id, "name": t.name, "keywords": t.keywords, "chunks": t.chunks, "sources": t.sources]
                }
                let untagged = Int(await store.meta("untagged:\(notebook)") ?? "") ?? 0
                return json(["topics": topics, "sources": await store.hostSourceCount(notebook: notebook), "untagged": untagged])
            case "/phone/ask":
                // Patrick's rule: a phone's questions stay on the phone. The phone answers them itself.
                throw HostRefusal(404, "this Mac does not answer questions for phones; the phone answers them itself")
            default:
                if !notebook.isEmpty, req.path != "/phone/library/notebook/edit", !(await visible(notebook)) {
                    throw HostRefusal(404, "no such notebook")
                }
                if req.path == "/phone/library/notebook/edit", !notebook.isEmpty, await store.isKeptLocal(notebook) {
                    throw HostRefusal(404, "no such notebook")
                }
                if let reply = try await delegate.handle(path: req.path, body: req.body, from: phone) { return (200, reply) }
                throw HostRefusal(404, "unknown route \(req.path)")
            }
        } catch let r as HostRefusal {
            return HTTPWire.problem(r.status, r.message)
        } catch {
            SoakTrace.log("host: \(req.path) failed: \(error.localizedDescription)")
            return HTTPWire.problem(500, error.localizedDescription)
        }
    }

    /// A notebook the phone may see: one this Mac has and does not keep to itself.
    private func visible(_ notebook: String) async -> Bool {
        guard !notebook.isEmpty, !(await store.isKeptLocal(notebook)) else { return false }
        return await store.notebooks().contains { $0.id == notebook }
    }

    /// The embedder every device in a library shares (`LibrarySync` refuses anything else).
    public nonisolated static var embedderKey: [String: Any] {
        ["model_id": LibrarySync.expectedModel, "dims": LibrarySync.expectedDims, "max_tokens": LibrarySync.expectedMaxTokens,
         "pooling": "model", "document_template": "title: {title} | text: {text}", "query_template": "task: search result | query: {text}",
         "vector": "float16-le-base64"]
    }

    private func manifest() async -> [String: Any] {
        var notebooks: [[String: Any]] = []
        let kept = await store.keptLocal()
        for nb in await store.notebooks() where !kept.contains(nb.id) {
            let sources = await store.hostSources(notebook: nb.id)
            var row: [String: Any] = ["id": nb.id, "title": nb.title, "collapsed": nb.sectionCollapsed, "sources": sources.count,
                                      "chunks": sources.reduce(0) { $0 + $1.chunks }, "digest": LibraryStore.hostDigest(sources)]
            if let section = nb.section, !section.isEmpty { row["section"] = section } else { row["section"] = NSNull() }
            if let color = nb.color { row["color"] = color } else { row["color"] = NSNull() }
            notebooks.append(row)
        }
        return ["proto": Self.proto, "embedder": Self.embedderKey, "notebooks": notebooks]
    }

    private func chunks(notebook: String, body: [String: Any]) async -> [String: Any] {
        let sources = body["source_ids"] as? [String]
        var after: (sourceId: String, chunkIndex: Int)?
        if let a = body["after"] as? [String: Any], let sid = a["source_id"] as? String {
            after = (sid, (a["chunk_index"] as? Int) ?? -2)
        }
        let limit = (body["limit"] as? Int) ?? 200
        let page = await store.hostRows(notebook: notebook, sources: sources, after: after, limit: limit)
        var rows: [[String: Any]] = []
        for r in page.rows where r.vector.count == LibrarySync.expectedDims * 2 {        // never send a vector of the wrong shape
            rows.append(["source_id": r.sourceId, "chunk_index": r.chunkIndex, "text": r.text, "summary": r.summary,
                         "filename": r.filename, "source_type": r.sourceType, "vector": r.vector.base64EncodedString()])
        }
        var out: [String: Any] = ["notebook_id": notebook, "chunks": rows, "more": page.more]
        if let last = page.rows.last { out["after"] = ["source_id": last.sourceId, "chunk_index": last.chunkIndex] } else { out["after"] = NSNull() }
        return out
    }

    private func json(_ object: [String: Any]) -> (Int, Data) {
        guard let data = try? JSONSerialization.data(withJSONObject: object) else { return HTTPWire.problem(500, "reply could not be encoded") }
        return (200, data)
    }
}

/// The addresses a person can type into a phone when Bonjour does not find this Mac.
public enum LocalAddresses {
    public static func ipv4() -> [String] {
        var out: [String] = []
        var first: UnsafeMutablePointer<ifaddrs>?
        guard getifaddrs(&first) == 0, let start = first else { return [] }
        defer { freeifaddrs(first) }
        var cursor: UnsafeMutablePointer<ifaddrs>? = start
        while let p = cursor {
            defer { cursor = p.pointee.ifa_next }
            guard let addr = p.pointee.ifa_addr, addr.pointee.sa_family == UInt8(AF_INET) else { continue }
            let name = String(cString: p.pointee.ifa_name)
            guard name != "lo0", !name.hasPrefix("utun"), !name.hasPrefix("awdl"), !name.hasPrefix("llw") else { continue }
            var host = [CChar](repeating: 0, count: Int(NI_MAXHOST))
            if getnameinfo(addr, socklen_t(addr.pointee.sa_len), &host, socklen_t(host.count), nil, 0, NI_NUMERICHOST) == 0 {
                let bytes = host.prefix { $0 != 0 }.map { UInt8(bitPattern: $0) }
                let text = String(decoding: bytes, as: UTF8.self)
                if !out.contains(text) { out.append(text) }
            }
        }
        return out
    }
}
