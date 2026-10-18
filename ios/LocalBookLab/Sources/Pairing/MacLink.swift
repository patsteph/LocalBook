import CryptoKit
import Foundation
import Security

/// Phone ↔ Mac link (Phase 0 test 3, steps 2–3). Pair on `sync_port + 2` (server-auth only: the
/// 6-digit code both screens show is what authenticates the certificate), then talk on
/// `sync_port + 3` with mutual TLS pinned to exactly the certificate seen at pairing and every
/// request signed with this phone's key.
struct PairedMac: Codable, Equatable {
    var deviceId: String
    var name: String
    var host: String
    var syncPort: Int
    var certDER: Data
    var fingerprint: String
}

enum MacLinkError: LocalizedError {
    case http(Int, String), certificateMismatch, noCertificate, notPaired
    var errorDescription: String? {
        switch self {
        case .http(let c, let m): "Mac answered \(c): \(m)"
        case .certificateMismatch: "The certificate in the reply is not the one the connection used"
        case .noCertificate: "The Mac sent no certificate"
        case .notPaired: "Not paired yet"
        }
    }
}

enum MacLink {
    static let pairPortOffset = 2          // sync_port + 2
    static let syncPortOffset = 3
    static let macSyncPort = 47600

    private static let storeKey = "lab.pairedMac"
    static var paired: PairedMac? {
        get { UserDefaults.standard.data(forKey: storeKey).flatMap { try? JSONDecoder().decode(PairedMac.self, from: $0) } }
        set { UserDefaults.standard.set(newValue.flatMap { try? JSONEncoder().encode($0) }, forKey: storeKey) }
    }

    /// The code both screens show: SHA-256 over the two certificate fingerprints, sorted, mod 10^6.
    /// (The Mac computes `int(sha256(lo + hi).hexdigest(), 16) % 1_000_000` over the hex strings.)
    static func sas(_ a: String, _ b: String) -> String {
        let (lo, hi) = a < b ? (a, b) : (b, a)
        var acc = 0
        for byte in SHA256.hash(data: Data((lo + hi).utf8)) { acc = (acc * 256 + Int(byte)) % 1_000_000 }
        return String(format: "%06d", acc)
    }

    static func fingerprint(of der: Data) -> String {
        SHA256.hash(data: der).map { String(format: "%02x", $0) }.joined()
    }

    struct PairingOutcome { let mac: PairedMac; let code: String }

    /// Step 2: say hello to a Mac whose pairing window is open. Nothing is pinned until the user
    /// has compared the code on both screens and the Mac side has confirmed.
    static func requestPairing(host: String) async throws -> PairingOutcome {
        let delegate = TrustCapture(pinned: nil, identity: nil)
        let session = URLSession(configuration: .ephemeral, delegate: delegate, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        var req = URLRequest(url: URL(string: "https://\(host):\(macSyncPort + pairPortOffset)/pair/hello")!)
        req.httpMethod = "POST"
        req.timeoutInterval = 15
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: [
            "device_id": PhoneIdentity.deviceId, "name": PhoneIdentity.deviceName,
            "cert_pem": try PhoneIdentity.certificatePEM(),
        ])
        let (data, response) = try await session.data(for: req)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard status == 200 else { throw MacLinkError.http(status, String(decoding: data.prefix(200), as: UTF8.self)) }
        guard let seen = delegate.seenDER else { throw MacLinkError.noCertificate }
        let body = try JSONSerialization.jsonObject(with: data) as? [String: Any] ?? [:]
        guard let pem = body["cert_pem"] as? String, let replyDER = derFromPEM(pem),
              replyDER == seen else { throw MacLinkError.certificateMismatch }
        let theirs = fingerprint(of: seen)
        let mine = fingerprint(of: SecCertificateCopyData(try PhoneIdentity.certificate()) as Data)
        let mac = PairedMac(deviceId: body["device_id"] as? String ?? "?", name: body["name"] as? String ?? "Mac",
                            host: host, syncPort: (body["sync_port"] as? Int) ?? macSyncPort + syncPortOffset,
                            certDER: seen, fingerprint: theirs)
        return PairingOutcome(mac: mac, code: sas(mine, theirs))
    }

    /// Step 3: a signed call over mutual TLS pinned to the certificate seen at pairing.
    static func signedHello(_ mac: PairedMac) async throws -> String {
        let delegate = TrustCapture(pinned: mac.certDER, identity: try PhoneIdentity.identity())
        let session = URLSession(configuration: .ephemeral, delegate: delegate, delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        let path = "/phone/hello"
        let body = Data("{}".utf8)
        var req = URLRequest(url: URL(string: "https://\(mac.host):\(mac.syncPort)\(path)")!)
        req.httpMethod = "POST"
        req.timeoutInterval = 15
        req.httpBody = body
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        for (k, v) in try PhoneIdentity.signedHeaders(method: "POST", path: path, body: body) { req.setValue(v, forHTTPHeaderField: k) }
        let (data, response) = try await session.data(for: req)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard status == 200 else { throw MacLinkError.http(status, String(decoding: data.prefix(200), as: UTF8.self)) }
        return String(decoding: data, as: UTF8.self)
    }

    private static func derFromPEM(_ pem: String) -> Data? {
        let b64 = pem.split(separator: "\n").filter { !$0.hasPrefix("-----") }.joined()
        return Data(base64Encoded: b64)
    }
}

/// Handles the two TLS challenges. Pairing: accept the server certificate and remember it (the
/// code authenticates it afterwards). Sync: accept ONLY the pinned certificate, and present ours.
private final class TrustCapture: NSObject, URLSessionDelegate, @unchecked Sendable {
    let pinned: Data?
    let identity: SecIdentity?
    private let lock = NSLock()
    private var _seen: Data?
    var seenDER: Data? { lock.lock(); defer { lock.unlock() }; return _seen }

    init(pinned: Data?, identity: SecIdentity?) { self.pinned = pinned; self.identity = identity }

    func urlSession(_ session: URLSession, didReceive challenge: URLAuthenticationChallenge,
                    completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        switch challenge.protectionSpace.authenticationMethod {
        case NSURLAuthenticationMethodServerTrust:
            guard let trust = challenge.protectionSpace.serverTrust,
                  let leaf = (SecTrustCopyCertificateChain(trust) as? [SecCertificate])?.first else {
                return completionHandler(.cancelAuthenticationChallenge, nil)
            }
            let der = SecCertificateCopyData(leaf) as Data
            lock.lock(); _seen = der; lock.unlock()
            if let pinned, pinned != der { return completionHandler(.cancelAuthenticationChallenge, nil) }
            completionHandler(.useCredential, URLCredential(trust: trust))
        case NSURLAuthenticationMethodClientCertificate:
            guard let identity else { return completionHandler(.performDefaultHandling, nil) }
            completionHandler(.useCredential, URLCredential(identity: identity, certificates: nil, persistence: .forSession))
        default:
            completionHandler(.performDefaultHandling, nil)
        }
    }
}
