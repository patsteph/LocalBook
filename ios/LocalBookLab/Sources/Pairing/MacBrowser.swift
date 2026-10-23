import Foundation
import Network
import Observation
import CryptoKit
import Security

/// Phase 0 test 3, step 1: find the Macs on this network the way the Mac finds its peers —
/// Bonjour `_localbook._tcp`. Discovery only; nothing here is trusted or authenticated.
@MainActor
@Observable
final class MacBrowser {
    struct Mac: Identifiable, Equatable {
        let id: String            // the Bonjour instance name
        let name: String
        let deviceId: String?     // `id=` in the advertisement's TXT record
        let endpoint: NWEndpoint
        var reachability: Reachability = .unknown
        var tls: TLSProbe = .notRun
    }

    /// What the Mac's certificate looks like to iOS. The Mac's identity is an Ed25519
    /// certificate; if iOS's TLS stack can't complete a handshake against it, pairing needs a
    /// different transport or a different Mac certificate — so find out before building on it.
    enum TLSProbe: Equatable {
        case notRun, running
        case sawCertificate(fingerprint: String, chain: Int)
        case noCertificate(String)
    }

    enum Reachability: Equatable {
        case unknown, checking, reachable(ms: Int), unreachable(String)
    }

    private(set) var macs: [Mac] = []
    private(set) var state = "Not started"
    private(set) var browsing = false
    private var browser: NWBrowser?

    static let service = "_localbook._tcp"

    func start() {
        stop()
        let b = NWBrowser(for: .bonjourWithTXTRecord(type: Self.service, domain: nil), using: .tcp)
        b.stateUpdateHandler = { [weak self] s in
            Task { @MainActor in self?.apply(state: s) }
        }
        b.browseResultsChangedHandler = { [weak self] results, _ in
            Task { @MainActor in self?.apply(results: results) }
        }
        browser = b
        browsing = true
        state = "Looking…"
        b.start(queue: .main)
    }

    func stop() {
        browser?.cancel()
        browser = nil
        browsing = false
    }

    private func apply(state s: NWBrowser.State) {
        switch s {
        case .ready: state = macs.isEmpty ? "Looking…" : "Found \(macs.count)"
        case .failed(let e): state = "Failed: \(e.localizedDescription)"; browsing = false
        case .waiting(let e): state = "Waiting: \(e.localizedDescription)"   // e.g. local-network permission
        case .cancelled: state = "Stopped"
        default: break
        }
    }

    private func apply(results: Set<NWBrowser.Result>) {
        let previous = Dictionary(uniqueKeysWithValues: macs.map { ($0.id, $0.reachability) })
        macs = results.compactMap { r in
            guard case .service(let name, _, _, _) = r.endpoint else { return nil }
            var deviceId: String?
            if case .bonjour(let txt) = r.metadata { deviceId = txt["id"] }
            var mac = Mac(id: name, name: name, deviceId: deviceId, endpoint: r.endpoint)
            mac.reachability = previous[name] ?? .unknown
            return mac
        }
        .sorted { $0.name < $1.name }
        if browsing { state = macs.isEmpty ? "Looking…" : "Found \(macs.count)" }
    }

    /// TLS 1.3 handshake to the sync port, trusting nothing and sending nothing: only records
    /// whether iOS gets as far as handing us the Mac's certificate, and its SHA-256 (compare with
    /// the Mac's `sync/device.crt`). The Mac then rejects us for lacking a client certificate —
    /// that failure is expected and irrelevant here.
    func probeTLS(_ mac: Mac) {
        guard let i = macs.firstIndex(where: { $0.id == mac.id }) else { return }
        macs[i].tls = .running
        runProbe(endpoint: mac.endpoint, id: mac.id)
    }

    /// Same probe against any host:port — used with throwaway test servers on the Mac to tell
    /// "iOS can't do Ed25519" apart from "something else is wrong".
    private(set) var manualResult: TLSProbe = .notRun
    private(set) var log: [String] = []

    func probeTLS(host: String, port: UInt16) {
        guard let p = NWEndpoint.Port(rawValue: port) else { return }
        manualResult = .running
        log = []
        runProbe(endpoint: .hostPort(host: NWEndpoint.Host(host), port: p), id: "manual")
    }

    private func runProbe(endpoint: NWEndpoint, id: String) {
        let tls = NWProtocolTLS.Options()
        let sec = tls.securityProtocolOptions
        sec_protocol_options_set_min_tls_protocol_version(sec, .TLSv13)
        sec_protocol_options_set_verify_block(sec, { [weak self] _, trustRef, complete in
            let trust = sec_trust_copy_ref(trustRef).takeRetainedValue()
            let chain = (SecTrustCopyCertificateChain(trust) as? [SecCertificate]) ?? []
            let result: TLSProbe
            if let leaf = chain.first {
                let der = SecCertificateCopyData(leaf) as Data
                let fp = SHA256.hash(data: der).map { String(format: "%02x", $0) }.joined()
                result = .sawCertificate(fingerprint: fp, chain: chain.count)
            } else {
                result = .noCertificate("verify callback ran with an empty chain")
            }
            Task { @MainActor in self?.setTLS(result, for: id) }
            complete(true)          // diagnostic only: nothing is sent on this connection
        }, .main)
        let conn = NWConnection(to: endpoint, using: NWParameters(tls: tls, tcp: NWProtocolTCP.Options()))
        conn.stateUpdateHandler = { [weak self] s in
            Task { @MainActor in
                guard let self else { return }
                self.note(id, "state \(s)")
                switch s {
                case .ready: conn.cancel()
                case .failed(let e), .waiting(let e):
                    if self.current(id) == .running { self.setTLS(.noCertificate("\(e)"), for: id) }
                    conn.cancel()
                default: break
                }
            }
        }
        conn.start(queue: .main)
        DispatchQueue.main.asyncAfter(deadline: .now() + 10) { [weak self] in
            Task { @MainActor in
                guard let self, self.current(id) == .running else { return }
                self.setTLS(.noCertificate("No handshake in 10 s"), for: id)
                conn.cancel()
            }
        }
    }

    private func note(_ id: String, _ line: String) {
        if id == "manual" { log.append(line) }
    }

    private func current(_ id: String) -> TLSProbe? {
        id == "manual" ? manualResult : macs.first(where: { $0.id == id })?.tls
    }

    private func setTLS(_ t: TLSProbe, for id: String) {
        if id == "manual" { manualResult = t; return }
        if let j = macs.firstIndex(where: { $0.id == id }) { macs[j].tls = t }
    }

    /// Plain TCP connect to the advertised endpoint: proves the local-network permission, the
    /// route and the open sync port. No TLS here — that is step 2.
    func checkReachable(_ mac: Mac) {
        guard let i = macs.firstIndex(where: { $0.id == mac.id }) else { return }
        macs[i].reachability = .checking
        let started = Date()
        let conn = NWConnection(to: mac.endpoint, using: .tcp)
        let id = mac.id
        conn.stateUpdateHandler = { [weak self] s in
            Task { @MainActor in
                guard let self, let j = self.macs.firstIndex(where: { $0.id == id }) else { return }
                switch s {
                case .ready:
                    self.macs[j].reachability = .reachable(ms: Int(Date().timeIntervalSince(started) * 1000))
                    conn.cancel()
                case .failed(let e), .waiting(let e):
                    self.macs[j].reachability = .unreachable(e.localizedDescription)
                    conn.cancel()
                default: break
                }
            }
        }
        conn.start(queue: .main)
        DispatchQueue.main.asyncAfter(deadline: .now() + 8) { [weak self] in
            Task { @MainActor in
                guard let self, let j = self.macs.firstIndex(where: { $0.id == id }),
                      self.macs[j].reachability == .checking else { return }
                self.macs[j].reachability = .unreachable("No answer in 8 s")
                conn.cancel()
            }
        }
    }

    /// The IPv4 address behind an advertised Bonjour service: connect once and read where the path actually goes.
    func resolveHost(_ mac: Mac) async -> String? {
        await withCheckedContinuation { cont in
            let params = NWParameters.tcp
            if let ip = params.defaultProtocolStack.internetProtocol as? NWProtocolIP.Options { ip.version = .v4 }
            let conn = NWConnection(to: mac.endpoint, using: params)
            let box = ResumeOnce(cont)
            conn.stateUpdateHandler = { state in
                switch state {
                case .ready:
                    if case let .hostPort(host, _)? = conn.currentPath?.remoteEndpoint {
                        var text = "\(host)"
                        if let pct = text.firstIndex(of: "%") { text = String(text[..<pct]) }
                        box.resume(text)
                    } else { box.resume(nil) }
                    conn.cancel()
                case .failed, .cancelled: box.resume(nil)
                default: break
                }
            }
            conn.start(queue: .main)
            DispatchQueue.main.asyncAfter(deadline: .now() + 8) { box.resume(nil); conn.cancel() }
        }
    }
}

private final class ResumeOnce: @unchecked Sendable {
    private let lock = NSLock()
    private var cont: CheckedContinuation<String?, Never>?
    init(_ c: CheckedContinuation<String?, Never>) { cont = c }
    func resume(_ value: String?) {
        lock.lock(); let c = cont; cont = nil; lock.unlock()
        c?.resume(returning: value)
    }
}
