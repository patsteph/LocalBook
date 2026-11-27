import CryptoKit
import Foundation

/// BIP-39 recovery phrases, as the old app's `mnemonic` package made and checked them: 24 English words carry 256 bits
/// of entropy and an 8-bit checksum; the phrase alone (PBKDF2-HMAC-SHA512, 2048 rounds, salt "mnemonic") gives the
/// 64-byte seed the recovery key is cut from. The same phrase gives the same key on any machine.
public enum BIP39 {
    public enum PhraseError: LocalizedError, Equatable {
        case wordCount(Int), unknownWord(String), checksum
        public var errorDescription: String? {
            switch self {
            case .wordCount(let n): "A recovery phrase has 24 words; this one has \(n)."
            case .unknownWord(let w): "“\(w)” is not a recovery-phrase word."
            case .checksum: "That is not a valid recovery phrase (a word is wrong or out of order)."
            }
        }
    }

    /// A fresh 24-word phrase from 32 random bytes.
    public static func generate() -> String {
        var entropy = [UInt8](repeating: 0, count: 32)
        var rng = SystemRandomNumberGenerator()
        for i in entropy.indices { entropy[i] = rng.next() }
        return phrase(entropy: Data(entropy))
    }

    /// The phrase for `entropy` (16 to 32 bytes, a multiple of 4).
    public static func phrase(entropy: Data) -> String {
        precondition(entropy.count % 4 == 0 && entropy.count >= 16 && entropy.count <= 32)
        let checksumBits = entropy.count / 4
        let hash = SHA256.hash(data: entropy)
        var bits = Substring(bitString(entropy) + String(bitString(Data(hash)).prefix(checksumBits)))
        var words: [String] = []
        while bits.count >= 11 {
            let chunk = bits.prefix(11)
            bits = bits.dropFirst(11)
            words.append(Self.words[Int(chunk, radix: 2)!])
        }
        return words.joined(separator: " ")
    }

    /// Lower-cased, single spaces, Unicode NFKD: what the seed is computed from and what the checksum is checked on.
    public static func normalize(_ phrase: String) -> String {
        phrase.lowercased().split(whereSeparator: { $0.isWhitespace || $0.isNewline }).joined(separator: " ").decomposedStringWithCompatibilityMapping
    }

    /// The entropy a phrase encodes, or why it is not a phrase.
    @discardableResult
    public static func check(_ phrase: String) throws -> Data {
        let list = normalize(phrase).split(separator: " ").map(String.init)
        guard [12, 15, 18, 21, 24].contains(list.count) else { throw PhraseError.wordCount(list.count) }
        var bits = ""
        for w in list {
            guard let i = index[w] else { throw PhraseError.unknownWord(w) }
            let b = String(i, radix: 2)
            bits += String(repeating: "0", count: 11 - b.count) + b
        }
        let checksumBits = list.count / 3
        let entropyBits = bits.prefix(bits.count - checksumBits)
        var entropy = Data()
        var rest = Substring(entropyBits)
        while rest.count >= 8 { entropy.append(UInt8(rest.prefix(8), radix: 2)!); rest = rest.dropFirst(8) }
        let hash = SHA256.hash(data: entropy)
        guard bitString(Data(hash)).prefix(checksumBits) == bits.suffix(checksumBits) else { throw PhraseError.checksum }
        return entropy
    }

    /// The 64-byte BIP-39 seed (no passphrase). Does not check the phrase: call `check` first.
    public static func seed(_ phrase: String) -> Data {
        pbkdf2SHA512(password: Data(normalize(phrase).utf8), salt: Data("mnemonic".utf8), rounds: 2048, length: 64)
    }

    static let index: [String: Int] = Dictionary(uniqueKeysWithValues: words.enumerated().map { ($1, $0) })

    private static func bitString(_ d: Data) -> String {
        d.map { let b = String($0, radix: 2); return String(repeating: "0", count: 8 - b.count) + b }.joined()
    }

    /// PBKDF2 with HMAC-SHA512 (RFC 8018), in CryptoKit so the pure library needs no CommonCrypto.
    static func pbkdf2SHA512(password: Data, salt: Data, rounds: Int, length: Int) -> Data {
        let key = SymmetricKey(data: password)
        var out = Data()
        var block: UInt32 = 1
        while out.count < length {
            var saltBlock = salt
            saltBlock.append(contentsOf: withUnsafeBytes(of: block.bigEndian) { Data($0) })
            var u = Data(HMAC<SHA512>.authenticationCode(for: saltBlock, using: key))
            var t = u
            for _ in 1..<rounds {
                u = Data(HMAC<SHA512>.authenticationCode(for: u, using: key))
                for i in t.indices { t[i] ^= u[i] }
            }
            out.append(t)
            block += 1
        }
        return out.prefix(length)
    }
}
