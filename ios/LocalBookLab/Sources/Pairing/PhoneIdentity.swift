import CryptoKit
import Foundation
import Security

/// This phone's identity for the Mac link: a P-256 key in the Keychain and a self-signed
/// certificate for it, shaped like the Mac's own (CA:TRUE with key identifiers, so the Mac's
/// OpenSSL accepts the pinned certificate as its own trust anchor). iOS TLS cannot use Ed25519,
/// which is why the Mac keeps a second, P-256 identity for phones.
enum PhoneIdentity {
    private static let tag = Data("com.patsteph.localbook.lab.phone-identity".utf8)
    private static let idKey = "lab.phone.deviceId"

    static var deviceId: String {
        if let id = UserDefaults.standard.string(forKey: idKey) { return id }
        let id = "phone-" + (0..<6).map { _ in String(format: "%02x", UInt8.random(in: 0...255)) }.joined()
        UserDefaults.standard.set(id, forKey: idKey)
        return id
    }

    static let deviceName = "iPhone"

    /// The private key, created on first use. Not Secure Enclave: this is a Lab test.
    static func privateKey() throws -> SecKey {
        let query: [String: Any] = [kSecClass as String: kSecClassKey,
                                    kSecAttrApplicationTag as String: tag,
                                    kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
                                    // A permanent key can leave its PUBLIC half in the Keychain under the
                                    // same tag; without the class, the lookup may return that one and
                                    // signing fails with -50 "algorithm not supported by the key".
                                    kSecAttrKeyClass as String: kSecAttrKeyClassPrivate,
                                    kSecReturnRef as String: true]
        var found: CFTypeRef?
        if SecItemCopyMatching(query as CFDictionary, &found) == errSecSuccess, let k = found {
            return k as! SecKey
        }
        let attrs: [String: Any] = [kSecAttrKeyType as String: kSecAttrKeyTypeECSECPrimeRandom,
                                    kSecAttrKeySizeInBits as String: 256,
                                    kSecPrivateKeyAttrs as String: [
                                        kSecAttrIsPermanent as String: true,
                                        kSecAttrApplicationTag as String: tag,
                                        kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
                                    ] as [String: Any]]
        var err: Unmanaged<CFError>?
        guard let key = SecKeyCreateRandomKey(attrs as CFDictionary, &err) else { throw err!.takeRetainedValue() }
        return key
    }

    /// The self-signed certificate (DER), built once and kept in the Keychain beside the key.
    static func certificate() throws -> SecCertificate {
        let label = "LocalBook Lab phone identity"
        let q: [String: Any] = [kSecClass as String: kSecClassCertificate, kSecAttrLabel as String: label,
                                kSecReturnRef as String: true]
        var found: CFTypeRef?
        if SecItemCopyMatching(q as CFDictionary, &found) == errSecSuccess, let c = found {
            return c as! SecCertificate
        }
        let der = try buildCertificateDER()
        guard let cert = SecCertificateCreateWithData(nil, der as CFData) else { throw LabError.model("bad certificate") }
        let add: [String: Any] = [kSecClass as String: kSecClassCertificate, kSecValueRef as String: cert,
                                  kSecAttrLabel as String: label]
        let status = SecItemAdd(add as CFDictionary, nil)
        guard status == errSecSuccess || status == errSecDuplicateItem else { throw LabError.model("keychain \(status)") }
        return cert
    }

    static func certificatePEM() throws -> String {
        let der = SecCertificateCopyData(try certificate()) as Data
        let b64 = der.base64EncodedString(options: [.lineLength64Characters, .endLineWithLineFeed])
        return "-----BEGIN CERTIFICATE-----\n\(b64)\n-----END CERTIFICATE-----\n"
    }

    /// A SecIdentity (certificate + its private key) for mutual TLS.
    static func identity() throws -> SecIdentity {
        _ = try privateKey()
        let cert = try certificate()
        let der = SecCertificateCopyData(cert) as Data
        // Ask for the identities in the Keychain and take the one whose certificate is ours. (A
        // lookup by kSecValueRef returned -25291 on a device; matching the certificate bytes is
        // unambiguous and needs nothing but the key and certificate already stored there.)
        let q: [String: Any] = [kSecClass as String: kSecClassIdentity,
                                kSecReturnRef as String: true,
                                kSecMatchLimit as String: kSecMatchLimitAll]
        var found: CFTypeRef?
        let s = SecItemCopyMatching(q as CFDictionary, &found)
        guard s == errSecSuccess, let all = found as? [SecIdentity] else {
            throw LabError.model("no identities in the Keychain (\(s)) — key and certificate are stored but not paired")
        }
        for id in all {
            var c: SecCertificate?
            if SecIdentityCopyCertificate(id, &c) == errSecSuccess, let c, (SecCertificateCopyData(c) as Data) == der {
                return id
            }
        }
        throw LabError.model("none of the \(all.count) Keychain identities matches this phone's certificate")
    }

    /// The request-signing message and headers the Mac's `identity.verify_signature` expects.
    static func signedHeaders(method: String, path: String, body: Data) throws -> [String: String] {
        let ts = String(Int(Date().timeIntervalSince1970))
        let digest = SHA256.hash(data: body).map { String(format: "%02x", $0) }.joined()
        let message = Data([method.uppercased(), path, ts, digest].joined(separator: "\n").utf8)
        var err: Unmanaged<CFError>?
        guard let sig = SecKeyCreateSignature(try privateKey(), .ecdsaSignatureMessageX962SHA256,
                                              message as CFData, &err) as Data? else { throw err!.takeRetainedValue() }
        return ["X-LB-Device": deviceId, "X-LB-Time": ts, "X-LB-Sig": sig.base64EncodedString()]
    }

    // MARK: X.509, by hand (a Lab needs one certificate, not a library)

    private static func der(_ tag: UInt8, _ body: Data) -> Data {
        var out = Data([tag])
        let n = body.count
        if n < 0x80 { out.append(UInt8(n)) }
        else if n < 0x100 { out.append(contentsOf: [0x81, UInt8(n)]) }
        else { out.append(contentsOf: [0x82, UInt8(n >> 8), UInt8(n & 0xff)]) }
        return out + body
    }

    private static func buildCertificateDER() throws -> Data {
        let priv = try privateKey()
        guard let pub = SecKeyCopyPublicKey(priv),
              let point = SecKeyCopyExternalRepresentation(pub, nil) as Data? else { throw LabError.model("no public key") }
        let sigAlg = der(0x30, Data([0x06, 0x08, 0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x04, 0x03, 0x02]))
        let name = der(0x30, der(0x31, der(0x30, Data([0x06, 0x03, 0x55, 0x04, 0x03])
            + der(0x0C, Data("LocalBook phone \(deviceId)".utf8)))))
        func utc(_ d: Date) -> Data {
            let f = DateFormatter(); f.dateFormat = "yyMMddHHmmss'Z'"
            f.timeZone = TimeZone(identifier: "UTC"); f.locale = Locale(identifier: "en_US_POSIX")
            return der(0x17, Data(f.string(from: d).utf8))
        }
        let validity = der(0x30, utc(Date().addingTimeInterval(-86_400)) + utc(Date().addingTimeInterval(86_400 * 365 * 20)))
        let spki = der(0x30, der(0x30, Data([0x06, 0x07, 0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x02, 0x01,
                                              0x06, 0x08, 0x2A, 0x86, 0x48, 0xCE, 0x3D, 0x03, 0x01, 0x07]))
                        + der(0x03, Data([0x00]) + point))
        let keyId = Data(Insecure.SHA1.hash(data: point))
        func ext(_ oid: [UInt8], critical: Bool, _ value: Data) -> Data {
            der(0x30, Data([0x06, UInt8(oid.count)] + oid) + (critical ? Data([0x01, 0x01, 0xFF]) : Data()) + der(0x04, value))
        }
        let extensions = der(0xA3, der(0x30,
            ext([0x55, 0x1D, 0x13], critical: true, Data([0x30, 0x03, 0x01, 0x01, 0xFF]))            // CA:TRUE
            + ext([0x55, 0x1D, 0x0F], critical: true, Data([0x03, 0x02, 0x02, 0x84]))                // digitalSignature, keyCertSign
            + ext([0x55, 0x1D, 0x0E], critical: false, der(0x04, keyId))                             // subject key id
            + ext([0x55, 0x1D, 0x23], critical: false, der(0x30, der(0x80, keyId)))))                // authority key id
        var serial = Data((0..<8).map { _ in UInt8.random(in: 0...255) }); serial[0] &= 0x7F; serial[0] |= 0x01
        let tbs = der(0x30, Data([0xA0, 0x03, 0x02, 0x01, 0x02]) + der(0x02, serial) + sigAlg + name + validity
                      + name + spki + extensions)
        var err: Unmanaged<CFError>?
        guard let sig = SecKeyCreateSignature(priv, .ecdsaSignatureMessageX962SHA256,
                                              tbs as CFData, &err) as Data? else { throw err!.takeRetainedValue() }
        return der(0x30, tbs + sigAlg + der(0x03, Data([0x00]) + sig))
    }
}
