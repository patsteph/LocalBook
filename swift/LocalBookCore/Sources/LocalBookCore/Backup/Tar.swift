import Foundation

/// A ustar archive, written and read the way Python's `tarfile` (USTAR_FORMAT) does it, since that is what the old
/// app's backups hold: regular files only, 512-byte headers, two zero blocks at the end, padded to 10240 bytes.
public enum Tar {
    public struct Entry: Sendable, Equatable {
        public var name: String
        public var data: Data
        public var mode: UInt32
        public var mtime: Int
        public init(name: String, data: Data, mode: UInt32 = 0o600, mtime: Int) { self.name = name; self.data = data; self.mode = mode; self.mtime = mtime }
    }

    public enum TarError: LocalizedError, Equatable {
        case nameTooLong(String), malformed, checksum(String)
        public var errorDescription: String? {
            switch self {
            case .nameTooLong(let n): "The path \(n) is too long for the archive."
            case .malformed: "The archive is damaged (not a tar stream)."
            case .checksum(let n): "The archive is damaged at \(n)."
            }
        }
    }

    public static let blockSize = 512
    public static let recordSize = 10_240

    public static func write(_ entries: [Entry]) throws -> Data {
        var out = Data()
        for e in entries {
            out.append(try header(for: e))
            out.append(e.data)
            let pad = (blockSize - e.data.count % blockSize) % blockSize
            if pad > 0 { out.append(Data(count: pad)) }
        }
        out.append(Data(count: blockSize * 2))
        let pad = (recordSize - out.count % recordSize) % recordSize
        if pad > 0 { out.append(Data(count: pad)) }
        return out
    }

    public static func read(_ data: Data) throws -> [Entry] {
        var entries: [Entry] = []
        var offset = data.startIndex
        while offset + blockSize <= data.endIndex {
            let head = data[offset..<(offset + blockSize)]
            if head.allSatisfy({ $0 == 0 }) { break }
            func field(_ start: Int, _ length: Int) -> Data { head[(offset + start)..<(offset + start + length)] }
            func text(_ start: Int, _ length: Int) -> String {
                let raw = field(start, length)
                let end = raw.firstIndex(of: 0) ?? raw.endIndex
                return String(decoding: raw[raw.startIndex..<end], as: UTF8.self)
            }
            func number(_ start: Int, _ length: Int) -> Int {
                let s = text(start, length).trimmingCharacters(in: CharacterSet(charactersIn: " \0"))
                return Int(s, radix: 8) ?? 0
            }
            var name = text(0, 100)
            let prefix = text(345, 155)
            if !prefix.isEmpty { name = prefix + "/" + name }
            let size = number(124, 12)
            let stored = number(148, 8)
            var sum = 0
            for (i, b) in head.enumerated() { sum += (148..<156).contains(i) ? 32 : Int(b) }
            guard sum == stored else { throw TarError.checksum(name) }
            let type = head[offset + 156]
            let bodyStart = offset + blockSize
            guard bodyStart + size <= data.endIndex else { throw TarError.malformed }
            if type == 0x30 || type == 0 {        // '0' or NUL: a regular file
                entries.append(Entry(name: name, data: Data(data[bodyStart..<(bodyStart + size)]), mode: UInt32(number(100, 8)), mtime: number(136, 12)))
            }
            offset = bodyStart + size + (blockSize - size % blockSize) % blockSize
        }
        return entries
    }

    /// The header the way Python writes it: octal fields as `%0*o` plus NUL, magic `ustar\0`, version `00`.
    static func header(for e: Entry) throws -> Data {
        let (prefix, name) = try splitName(e.name)
        var h = Data(count: blockSize)
        func put(_ s: String, at: Int, _ length: Int) {
            let bytes = Array(s.utf8.prefix(length))
            h.replaceSubrange(at..<(at + bytes.count), with: bytes)
        }
        func octal(_ n: Int, at: Int, _ length: Int) {
            let s = String(n, radix: 8)
            put(String(repeating: "0", count: max(0, length - 1 - s.count)) + s, at: at, length - 1)
        }
        put(name, at: 0, 100)
        octal(Int(e.mode), at: 100, 8)
        octal(0, at: 108, 8)
        octal(0, at: 116, 8)
        octal(e.data.count, at: 124, 12)
        octal(e.mtime, at: 136, 12)
        put("        ", at: 148, 8)
        put("0", at: 156, 1)
        put("ustar", at: 257, 6)
        put("00", at: 263, 2)
        octal(0, at: 329, 8)
        octal(0, at: 337, 8)
        put(prefix, at: 345, 155)
        let sum = h.reduce(0) { $0 + Int($1) }
        let s = String(sum, radix: 8)
        put(String(repeating: "0", count: max(0, 6 - s.count)) + s + "\0 ", at: 148, 8)
        return h
    }

    /// A name over 100 bytes is split at a `/` into the prefix field (155) and the name field (100), shortest prefix first.
    static func splitName(_ full: String) throws -> (prefix: String, name: String) {
        if full.utf8.count <= 100 { return ("", full) }
        let parts = full.split(separator: "/", omittingEmptySubsequences: false).map(String.init)
        for i in 1..<parts.count {
            let prefix = parts[..<i].joined(separator: "/"), name = parts[i...].joined(separator: "/")
            if prefix.utf8.count <= 155, name.utf8.count <= 100 { return (prefix, name) }
        }
        throw TarError.nameTooLong(full)
    }
}
