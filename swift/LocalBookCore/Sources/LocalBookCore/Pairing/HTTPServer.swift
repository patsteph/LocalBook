import Foundation
import Network
import Security

/// A TLS listener that answers one JSON request per connection: the shape of the phone link's two ports. Network
/// framework end to end, no dependency. The handler runs off the listener's queue and may take as long as a
/// notebook's chunk page or a note's indexing does.
public final class HTTPServer: @unchecked Sendable {
    public typealias Handler = @Sendable (_ request: HTTPRequest, _ remoteHost: String) async -> (status: Int, body: Data)

    public struct Advert: Sendable {
        public let name: String
        public let type: String
        public let txt: [String: String]
        public init(name: String, type: String, txt: [String: String]) { self.name = name; self.type = type; self.txt = txt }
    }

    public enum ServerError: LocalizedError {
        case port(UInt16, String), identity
        public var errorDescription: String? {
            switch self {
            case .port(let p, let why): "Port \(p) could not be opened: \(why)"
            case .identity: "This Mac's certificate and key could not be used for TLS"
            }
        }
    }

    public let port: UInt16
    private let identity: SecIdentity
    private let advert: Advert?
    private let handler: Handler
    private let queue: DispatchQueue
    private var listener: NWListener?
    private var ready = false
    private let lock = NSLock()
    private var connections: [ObjectIdentifier: NWConnection] = [:]

    public init(port: UInt16, identity: SecIdentity, advert: Advert? = nil, handler: @escaping Handler) {
        self.port = port; self.identity = identity; self.advert = advert; self.handler = handler
        queue = DispatchQueue(label: "localbook.host.\(port)")
    }

    /// Opens the port; returns once it is listening, throws when it cannot (in use, no permission).
    public func start() async throws {
        let tls = NWProtocolTLS.Options()
        let sec = tls.securityProtocolOptions
        guard let secIdentity = sec_identity_create(identity) else { throw ServerError.identity }
        sec_protocol_options_set_local_identity(sec, secIdentity)
        sec_protocol_options_set_min_tls_protocol_version(sec, .TLSv12)
        let params = NWParameters(tls: tls, tcp: NWProtocolTCP.Options())
        params.allowLocalEndpointReuse = true
        guard let nwPort = NWEndpoint.Port(rawValue: port) else { throw ServerError.port(port, "not a port number") }
        let listener: NWListener
        do { listener = try NWListener(using: params, on: nwPort) } catch { throw ServerError.port(port, error.localizedDescription) }
        if let advert {
            listener.service = NWListener.Service(name: advert.name, type: advert.type, txtRecord: NWTXTRecord(advert.txt))
        }
        listener.newConnectionHandler = { [weak self] conn in self?.serve(conn) }
        self.listener = listener
        let gate = StartGate()
        listener.stateUpdateHandler = { [weak self] state in
            switch state {
            case .ready: self?.ready = true; gate.finish(nil)
            case .failed(let e):
                gate.finish(ServerError.port(self?.port ?? 0, e.localizedDescription))
                self?.stop()
            case .cancelled: gate.finish(ServerError.port(self?.port ?? 0, "closed"))
            default: break
            }
        }
        listener.start(queue: queue)
        try await gate.wait()
    }

    public func stop() {
        listener?.stateUpdateHandler = nil
        listener?.cancel()
        listener = nil
        ready = false
        lock.lock(); let open = Array(connections.values); connections.removeAll(); lock.unlock()
        for c in open { c.cancel() }
    }

    public var isListening: Bool { ready }

    private func serve(_ conn: NWConnection) {
        let key = ObjectIdentifier(conn)
        lock.lock(); connections[key] = conn; lock.unlock()
        var remote = "?"
        if case .hostPort(let host, _) = conn.endpoint {
            remote = "\(host)"
            if let pct = remote.firstIndex(of: "%") { remote = String(remote[..<pct]) }
        }
        let buffer = ByteBuffer()
        conn.stateUpdateHandler = { [weak self] state in
            switch state {
            case .failed, .cancelled:
                self?.lock.lock(); self?.connections[key] = nil; self?.lock.unlock()
            default: break
            }
        }
        conn.start(queue: queue)
        read(conn, buffer: buffer, remote: remote)
    }

    private func read(_ conn: NWConnection, buffer: ByteBuffer, remote: String) {
        conn.receive(minimumIncompleteLength: 1, maximumLength: 256 * 1024) { [weak self] data, _, complete, error in
            guard let self else { conn.cancel(); return }
            if let data { buffer.append(data) }
            if error != nil { conn.cancel(); return }
            do {
                if let parsed = try HTTPWire.parse(buffer.data) {
                    let request = parsed.request
                    let handler = self.handler
                    let box = ConnectionBox(conn)
                    Task.detached {
                        let (status, body) = await handler(request, remote)
                        self.reply(box.conn, status: status, body: body)
                    }
                    return
                }
            } catch HTTPWire.ParseError.tooLarge {
                reply(conn, status: 413, body: Data("{\"detail\":\"request too large\"}".utf8)); return
            } catch {
                reply(conn, status: 400, body: Data("{\"detail\":\"malformed request\"}".utf8)); return
            }
            if complete { conn.cancel(); return }
            self.read(conn, buffer: buffer, remote: remote)
        }
    }

    private func reply(_ conn: NWConnection, status: Int, body: Data) {
        conn.send(content: HTTPWire.response(status: status, json: body), completion: .contentProcessed { _ in
            conn.cancel()
        })
    }
}

/// Carries a connection into the handler's task.
private final class ConnectionBox: @unchecked Sendable {
    let conn: NWConnection
    init(_ c: NWConnection) { conn = c }
}

/// The bytes of one connection so far.
private final class ByteBuffer: @unchecked Sendable {
    private let lock = NSLock()
    private var bytes = Data()
    func append(_ d: Data) { lock.lock(); bytes.append(d); lock.unlock() }
    var data: Data { lock.lock(); defer { lock.unlock() }; return bytes }
}

/// Resumes the start continuation once, whichever state arrives first.
private final class StartGate: @unchecked Sendable {
    private let lock = NSLock()
    private var cont: CheckedContinuation<Void, Error>?
    private var outcome: Result<Void, Error>?

    func finish(_ error: Error?) {
        lock.lock()
        guard outcome == nil else { lock.unlock(); return }
        let result: Result<Void, Error> = error.map { .failure($0) } ?? .success(())
        outcome = result
        let c = cont; cont = nil
        lock.unlock()
        c?.resume(with: result)
    }

    func wait() async throws {
        try await withCheckedThrowingContinuation { (c: CheckedContinuation<Void, Error>) in
            lock.lock()
            if let outcome { lock.unlock(); c.resume(with: outcome); return }
            cont = c
            lock.unlock()
        }
    }
}
