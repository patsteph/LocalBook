import CryptoKit
import Foundation
import Security

/// This device's backup key: 32 random bytes in the Keychain, made the first time a backup is sealed. Every archive is
/// sealed to it, so a restore on the Mac that made the backup needs no phrase; on any other Mac the phrase opens it.
/// The data-protection Keychain, like the pairing key (`PhoneIdentity`): the app is sandboxed and this device only.
public enum DeviceKeys {
    public enum KeyError: LocalizedError {
        case keychain(OSStatus)
        public var errorDescription: String? {
            switch self {
            case .keychain(let s): "The Keychain refused this Mac's backup key (\(s))."
            }
        }
    }

    static let service = "com.patsteph.localbook.keys"

    /// The key, made when `create` and there is none yet; nil when none and not creating.
    public static func backupKey(create: Bool) throws -> SymmetricKey? {
        try key(account: "backup", create: create)
    }

    static func key(account: String, create: Bool) throws -> SymmetricKey? {
        var query: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                    kSecAttrAccount as String: account, kSecReturnData as String: true]
        #if os(macOS)
        query[kSecUseDataProtectionKeychain as String] = true
        #endif
        var found: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &found)
        if status == errSecSuccess, let data = found as? Data, data.count == 32 { return SymmetricKey(data: data) }
        guard status == errSecItemNotFound || status == errSecSuccess else { throw KeyError.keychain(status) }
        guard create else { return nil }
        let fresh = SymmetricKey(size: .bits256)
        var add: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service,
                                  kSecAttrAccount as String: account, kSecValueData as String: fresh.withUnsafeBytes { Data($0) },
                                  kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
                                  kSecAttrLabel as String: "LocalBook backup key"]
        #if os(macOS)
        add[kSecUseDataProtectionKeychain as String] = true
        #endif
        let added = SecItemAdd(add as CFDictionary, nil)
        guard added == errSecSuccess || added == errSecDuplicateItem else { throw KeyError.keychain(added) }
        if added == errSecDuplicateItem { return try key(account: account, create: false) }
        return fresh
    }
}
