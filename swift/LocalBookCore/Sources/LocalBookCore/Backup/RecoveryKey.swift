import CryptoKit
import Foundation

/// The recovery key: an X25519 pair cut from the recovery phrase's seed (`seed[0..<32]` as the private scalar), the
/// way the old app's keyvault did it, so a phrase written down under the old app opens this app's backups and the
/// other way round. Only the public half is kept (`keys/recovery.pub`, base64, the old file's name); the private half
/// exists only while a phrase is typed.
public enum RecoveryKey {
    public static func privateKey(phrase: String) throws -> Curve25519.KeyAgreement.PrivateKey {
        try BIP39.check(phrase)
        return try Curve25519.KeyAgreement.PrivateKey(rawRepresentation: BIP39.seed(phrase).prefix(32))
    }

    public static func publicKey(phrase: String) throws -> Data {
        try privateKey(phrase: phrase).publicKey.rawRepresentation
    }

    public static func publicKeyURL(base: URL = AppFolders.base) -> URL {
        base.appending(path: "keys", directoryHint: .isDirectory).appending(path: "recovery.pub")
    }

    /// The public key a phrase set earlier, nil when none has been.
    public static func storedPublicKey(base: URL = AppFolders.base) -> Data? {
        guard let text = try? String(contentsOf: publicKeyURL(base: base), encoding: .utf8) else { return nil }
        let raw = Data(base64Encoded: text.trimmingCharacters(in: .whitespacesAndNewlines))
        return raw?.count == 32 ? raw : nil
    }

    /// Remembers the public key of `phrase` so every backup from now on is sealed to it too. Returns the key.
    @discardableResult
    public static func store(phrase: String, base: URL = AppFolders.base) throws -> Data {
        let pub = try publicKey(phrase: phrase)
        let url = publicKeyURL(base: base)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try (pub.base64EncodedString() + "\n").write(to: url, atomically: true, encoding: .utf8)
        return pub
    }

    public static func forget(base: URL = AppFolders.base) {
        try? FileManager.default.removeItem(at: publicKeyURL(base: base))
    }
}
