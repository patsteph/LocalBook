import CryptoKit
import Foundation

/// The `.lbbackup` envelope the old app wrote (`services/backup_service.py`), byte for byte:
///
///     LBBK1\n | header length (4 bytes, big-endian) | header JSON | nonce (12) | AES-256-GCM(tar, AAD = header JSON)
///
/// The header names the recipients the content key is wrapped for: this device's Keychain key (`kind: device`, AES-GCM
/// with AAD "device") and the recovery key (`kind: recovery`: an ephemeral X25519 agreement, HKDF-SHA256 with info
/// `LocalBook/LB-10/backup-wrap/v1 ‖ ephemeral public ‖ recipient public`, AES-GCM with AAD "recovery"). Either opens
/// it. The header is the AAD of the body, so an edited header (a swapped recipient, a back-dated archive) fails to
/// decrypt instead of passing. The tar inside holds `data/MANIFEST.json` and the files under `data/`.
public enum BackupEnvelope {
    public static let magic = Data("LBBK1\n".utf8)
    public static let suffix = "lbbackup"
    static let wrapInfo = Data("LocalBook/LB-10/backup-wrap/v1".utf8)

    public struct Recipient: Codable, Sendable, Equatable {
        public var kind: String
        public var device_id: String?
        public var ephemeral_pub: String?
        public var nonce: String
        public var wrapped: String
    }

    public struct Header: Codable, Sendable, Equatable {
        public var format_version: Int
        public var created_at: String
        public var device_id: String
        public var recipients: [Recipient]
    }

    public enum OpenError: LocalizedError, Equatable {
        case notABackup, damaged, locked(triedPhrase: Bool), noRecipient
        public var errorDescription: String? {
            switch self {
            case .notABackup: "This is not a LocalBook backup."
            case .damaged: "The backup is damaged and cannot be opened."
            case .locked(let phrase): phrase ? "Neither this Mac's key nor that recovery phrase opens this backup."
                : "This Mac's key does not open this backup; it needs the recovery phrase."
            case .noRecipient: "A backup needs this Mac's key or a recovery phrase to be sealed to."
            }
        }
    }

    /// Seals `tar` for the recipients given; at least one is needed.
    public static func seal(tar: Data, createdAt: String, deviceId: String, deviceKey: SymmetricKey?, recoveryPublicKey: Data?) throws -> Data {
        let content = SymmetricKey(size: .bits256)
        var recipients: [Recipient] = []
        if let pub = recoveryPublicKey {
            let recipient = try Curve25519.KeyAgreement.PublicKey(rawRepresentation: pub)
            let ephemeral = Curve25519.KeyAgreement.PrivateKey()
            let wrap = try wrapKey(shared: ephemeral.sharedSecretFromKeyAgreement(with: recipient), ephemeralPub: ephemeral.publicKey.rawRepresentation, recipientPub: pub)
            let nonce = AES.GCM.Nonce()
            let box = try AES.GCM.seal(content.withUnsafeBytes { Data($0) }, using: wrap, nonce: nonce, authenticating: Data("recovery".utf8))
            recipients.append(Recipient(kind: "recovery", ephemeral_pub: ephemeral.publicKey.rawRepresentation.base64EncodedString(),
                                        nonce: Data(nonce).base64EncodedString(), wrapped: (box.ciphertext + box.tag).base64EncodedString()))
        }
        if let deviceKey {
            let nonce = AES.GCM.Nonce()
            let box = try AES.GCM.seal(content.withUnsafeBytes { Data($0) }, using: deviceKey, nonce: nonce, authenticating: Data("device".utf8))
            recipients.append(Recipient(kind: "device", device_id: deviceId, nonce: Data(nonce).base64EncodedString(), wrapped: (box.ciphertext + box.tag).base64EncodedString()))
        }
        guard !recipients.isEmpty else { throw OpenError.noRecipient }
        let header = Header(format_version: 1, created_at: createdAt, device_id: deviceId, recipients: recipients)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        let headerBytes = try encoder.encode(header)
        let nonce = AES.GCM.Nonce()
        let body = try AES.GCM.seal(tar, using: content, nonce: nonce, authenticating: headerBytes)
        var out = magic
        out.append(contentsOf: withUnsafeBytes(of: UInt32(headerBytes.count).bigEndian) { Data($0) })
        out.append(headerBytes)
        out.append(Data(nonce))
        out.append(body.ciphertext)
        out.append(body.tag)
        return out
    }

    /// The header alone: who can open the archive and when it was made.
    public static func header(of archive: Data) throws -> Header {
        try split(archive).header
    }

    /// The tar inside, with this device's key when it has one and the phrase when given.
    public static func open(_ archive: Data, deviceKey: SymmetricKey?, phrase: String?) throws -> (header: Header, tar: Data) {
        let parts = try split(archive)
        var content: SymmetricKey?
        let recovery = try phrase.map { try RecoveryKey.privateKey(phrase: $0) }
        for r in parts.header.recipients {
            guard let nonce = Data(base64Encoded: r.nonce), let wrapped = Data(base64Encoded: r.wrapped), wrapped.count > 16 else { continue }
            let box = try? AES.GCM.SealedBox(nonce: AES.GCM.Nonce(data: nonce), ciphertext: wrapped.dropLast(16), tag: wrapped.suffix(16))
            guard let box else { continue }
            if r.kind == "device", let deviceKey, let key = try? AES.GCM.open(box, using: deviceKey, authenticating: Data("device".utf8)) {
                content = SymmetricKey(data: key); break
            }
            if r.kind == "recovery", let recovery, let ephemeral = r.ephemeral_pub.flatMap({ Data(base64Encoded: $0) }),
               let ephemeralKey = try? Curve25519.KeyAgreement.PublicKey(rawRepresentation: ephemeral),
               let shared = try? recovery.sharedSecretFromKeyAgreement(with: ephemeralKey),
               let wrap = try? wrapKey(shared: shared, ephemeralPub: ephemeral, recipientPub: recovery.publicKey.rawRepresentation),
               let key = try? AES.GCM.open(box, using: wrap, authenticating: Data("recovery".utf8)) {
                content = SymmetricKey(data: key); break
            }
        }
        guard let content else { throw OpenError.locked(triedPhrase: phrase != nil) }
        guard parts.body.count > 16, let box = try? AES.GCM.SealedBox(nonce: AES.GCM.Nonce(data: parts.nonce), ciphertext: parts.body.dropLast(16), tag: parts.body.suffix(16)),
              let tar = try? AES.GCM.open(box, using: content, authenticating: parts.headerBytes) else { throw OpenError.damaged }
        return (parts.header, tar)
    }

    static func wrapKey(shared: SharedSecret, ephemeralPub: Data, recipientPub: Data) throws -> SymmetricKey {
        shared.hkdfDerivedSymmetricKey(using: SHA256.self, salt: Data(), sharedInfo: wrapInfo + ephemeralPub + recipientPub, outputByteCount: 32)
    }

    static func split(_ archive: Data) throws -> (header: Header, headerBytes: Data, nonce: Data, body: Data) {
        guard archive.count > magic.count + 4, archive.prefix(magic.count) == magic else { throw OpenError.notABackup }
        let lenStart = archive.startIndex + magic.count
        let length = archive[lenStart..<(lenStart + 4)].reduce(0) { $0 << 8 | Int($1) }
        let headerStart = lenStart + 4
        guard length > 0, archive.endIndex - headerStart >= length + 12 + 16 else { throw OpenError.damaged }
        let headerBytes = Data(archive[headerStart..<(headerStart + length)])
        guard let header = try? JSONDecoder().decode(Header.self, from: headerBytes) else { throw OpenError.damaged }
        let nonce = Data(archive[(headerStart + length)..<(headerStart + length + 12)])
        return (header, headerBytes, nonce, Data(archive[(headerStart + length + 12)...]))
    }
}

/// The manifest inside the tar (`data/MANIFEST.json`): what was backed up and how to check it. Kept as a dictionary so
/// an archive from the old app still reads (its keys are a superset of what this app writes).
public struct BackupManifest: @unchecked Sendable {
    /// JSON values only (strings, numbers, bools, arrays, dictionaries), so the struct is safe to pass between tasks.
    public var raw: [String: Any]

    public init(raw: [String: Any]) { self.raw = raw }
    public init(json: Data) throws {
        guard let o = try JSONSerialization.jsonObject(with: json) as? [String: Any] else { throw BackupEnvelope.OpenError.damaged }
        raw = o
    }

    public var createdAt: String { raw["created_at"] as? String ?? "" }
    public var deviceId: String { raw["device_id"] as? String ?? "" }
    public var deviceName: String? { raw["device_name"] as? String }
    public var files: [String: (sha256: String, bytes: Int)] {
        var out: [String: (String, Int)] = [:]
        for (k, v) in raw["files"] as? [String: [String: Any]] ?? [:] { out[k] = (v["sha256"] as? String ?? "", v["bytes"] as? Int ?? 0) }
        return out
    }
    public var totalBytes: Int { files.values.reduce(0) { $0 + $1.bytes } }
    public var includesMedia: Bool { (raw["includes_blobs"] as? Bool) ?? (raw["include_blobs"] as? Bool) ?? false }
    public var rowCounts: [String: [String: Int]] { raw["row_counts"] as? [String: [String: Int]] ?? [:] }
    /// `swift-1` for this app's archives; the old app wrote its migration ledger's version.
    public var schemaVersion: String? { (raw["schema"] as? [String: Any])?["version"] as? String }
    public var isFromThisApp: Bool { schemaVersion?.hasPrefix("swift") ?? false }

    public func json() throws -> Data {
        try JSONSerialization.data(withJSONObject: raw, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])
    }
}

/// Reads a tar from `BackupEnvelope.open` back into files and a manifest, checking every file against it.
public enum BackupPayload {
    public static let dataPrefix = "data/"
    public static let manifestName = "MANIFEST.json"

    public static func unpack(tar: Data) throws -> (manifest: BackupManifest, files: [Tar.Entry]) {
        let entries = try Tar.read(tar)
        guard let m = entries.first(where: { $0.name == dataPrefix + manifestName }) else { throw BackupEnvelope.OpenError.damaged }
        let manifest = try BackupManifest(json: m.data)
        var files: [Tar.Entry] = []
        for var e in entries where e.name != dataPrefix + manifestName {
            guard e.name.hasPrefix(dataPrefix) else { continue }
            e.name = String(e.name.dropFirst(dataPrefix.count))
            files.append(e)
        }
        return (manifest, files)
    }

    /// The files whose bytes do not match the manifest (empty when the archive is whole).
    public static func mismatches(manifest: BackupManifest, files: [Tar.Entry]) -> [String] {
        let listed = manifest.files
        var bad: [String] = []
        for f in files {
            guard let want = listed[f.name] else { bad.append(f.name); continue }
            if want.bytes != f.data.count || want.sha256 != SHA256.hash(data: f.data).map({ String(format: "%02x", $0) }).joined() { bad.append(f.name) }
        }
        for name in listed.keys where !files.contains(where: { $0.name == name }) { bad.append(name) }
        return bad.sorted()
    }

    public static func pack(manifest: BackupManifest, files: [Tar.Entry]) throws -> Data {
        var entries = files.map { var e = $0; e.name = dataPrefix + e.name; return e }
        entries.append(Tar.Entry(name: dataPrefix + manifestName, data: try manifest.json(), mtime: files.first?.mtime ?? Int(Date().timeIntervalSince1970)))
        return try Tar.write(entries.sorted { $0.name < $1.name })
    }
}
