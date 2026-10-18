import Foundation
import Network
import Observation

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
}
