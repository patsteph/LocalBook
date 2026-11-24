import CryptoKit
import Foundation
import Security

/// PEM to DER, for certificates the phone sends as text.
public enum PEM {
    public static func der(_ pem: String) -> Data? {
        let b64 = pem.split(whereSeparator: \.isNewline).filter { !$0.hasPrefix("-----") }.joined()
        return Data(base64Encoded: b64)
    }

    public static func pem(_ der: Data) -> String {
        "-----BEGIN CERTIFICATE-----\n" + der.base64EncodedString(options: [.lineLength64Characters, .endLineWithLineFeed]) + "\n-----END CERTIFICATE-----\n"
    }
}

/// The host's side of the v2 signed request (`PhoneIdentity.signedHeaders` makes it, the Python Mac's
/// `verify_phone_signature` checks it): the phone's ECDSA P-256 signature over the method, path, time, a one-time
/// nonce, THIS Mac's id and certificate fingerprint, and the body's SHA-256. A captured request cannot be replayed
/// (the nonce is remembered), cannot be sent to another Mac the phone is paired with (the Mac is inside the
/// signature), and goes stale after `maxSkew` seconds.
public enum PhoneSignature {
    public static let proto = "LBP2"
    public static let maxSkew: TimeInterval = 120

    public static func message(method: String, path: String, ts: String, nonce: String,
                               macId: String, macFingerprint: String, body: Data) -> Data {
        let digest = SHA256.hash(data: body).map { String(format: "%02x", $0) }.joined()
        return Data([proto, method.uppercased(), path, ts, nonce, macId, macFingerprint, digest].joined(separator: "\n").utf8)
    }

    /// The P-256 public key in a certificate's PEM, or nil when the certificate is not a P-256 one.
    public static func publicKey(fromCertificatePEM pem: String) -> P256.Signing.PublicKey? {
        guard let der = PEM.der(pem), let cert = SecCertificateCreateWithData(nil, der as CFData),
              let key = SecCertificateCopyKey(cert), let rep = SecKeyCopyExternalRepresentation(key, nil) as Data? else { return nil }
        return try? P256.Signing.PublicKey(x963Representation: rep)
    }

    public static func fingerprint(ofCertificatePEM pem: String) -> String? {
        PEM.der(pem).map { MacLink.fingerprint(of: $0) }
    }

    /// True when the headers carry a fresh, well-formed signature by `key` over a request addressed to this Mac.
    public static func verify(key: P256.Signing.PublicKey, method: String, path: String, ts: String, nonce: String,
                              body: Data, signatureBase64: String, macId: String, macFingerprint: String, now: Date = Date()) -> Bool {
        guard let t = Int(ts), abs(Int(now.timeIntervalSince1970) - t) <= Int(maxSkew) else { return false }
        guard (16...64).contains(nonce.count), nonce.allSatisfy({ "0123456789abcdefABCDEF-_".contains($0) }) else { return false }
        guard let raw = Data(base64Encoded: signatureBase64),
              let signature = try? P256.Signing.ECDSASignature(derRepresentation: raw) else { return false }
        return key.isValidSignature(signature, for: message(method: method, path: path, ts: ts, nonce: nonce,
                                                            macId: macId, macFingerprint: macFingerprint, body: body))
    }
}

/// Remembers the nonces of valid requests for as long as one could still be fresh, so a request is honoured once.
public final class NonceCache: @unchecked Sendable {
    private let lock = NSLock()
    private var seen: [String: Date] = [:]
    private let ttl: TimeInterval
    private let maxItems: Int

    public init(ttl: TimeInterval = 2 * PhoneSignature.maxSkew + 30, maxItems: Int = 20_000) { self.ttl = ttl; self.maxItems = maxItems }

    /// True the first time `key` is seen; false after that (until it has expired).
    public func firstUse(_ key: String, now: Date = Date()) -> Bool {
        lock.lock(); defer { lock.unlock() }
        if seen.count > maxItems || (seen.count > 256 && seen.count % 64 == 0) {
            seen = seen.filter { now.timeIntervalSince($0.value) < ttl }
        }
        if let at = seen[key], now.timeIntervalSince(at) < ttl { return false }
        seen[key] = now
        return true
    }
}

/// Blocks an address after too many failed authentications in a window. In memory: a restart forgets it, which is
/// fine (it only slows guessing, it does not guard a secret).
public final class FailureLimiter: @unchecked Sendable {
    private let lock = NSLock()
    private var fails: [String: [Date]] = [:]
    private let maxFailures: Int
    private let window: TimeInterval
    private let block: TimeInterval

    public init(maxFailures: Int = 6, window: TimeInterval = 60, block: TimeInterval = 120) {
        self.maxFailures = maxFailures; self.window = window; self.block = block
    }

    public func blocked(_ key: String, now: Date = Date()) -> Bool {
        lock.lock(); defer { lock.unlock() }
        let recent = (fails[key] ?? []).filter { now.timeIntervalSince($0) < max(window, block) }
        fails[key] = recent.isEmpty ? nil : recent
        guard recent.count >= maxFailures, let last = recent.last else { return false }
        return now.timeIntervalSince(last) < block
    }

    public func failure(_ key: String, now: Date = Date()) {
        lock.lock(); defer { lock.unlock() }
        fails[key, default: []].append(now)
    }
}
