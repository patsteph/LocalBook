import Foundation

/// A reader for OLE2 compound files (the container of legacy .xls, .ppt and .doc): the FAT, the mini stream and
/// the directory, enough to pull a named stream out. Pure Foundation, so it is testable in the package.
public struct OLE2 {
    public struct Stream { public let name: String; public let data: Data }

    let bytes: Data
    let sectorSize: Int
    let miniSectorSize: Int
    let fat: [UInt32]
    let miniFAT: [UInt32]
    let miniStream: Data
    let miniCutoff: Int
    public let streams: [Stream]

    static let endOfChain: UInt32 = 0xFFFF_FFFE
    static let freeSector: UInt32 = 0xFFFF_FFFF

    public enum Failure: LocalizedError {
        case notOLE, corrupt(String)
        public var errorDescription: String? {
            switch self {
            case .notOLE: return "This is not an Office 97-2003 file"
            case .corrupt(let what): return "The file's container is damaged (\(what))"
            }
        }
    }

    public init(_ data: Data) throws {
        // Everything is computed into locals first: a nested function that read `self` before every stored property
        // was set is what Swift 6 refused, so the sector walk is a static function over the pieces it needs.
        let bytes = Data(data)                               // rebase so offsets start at 0
        guard bytes.count >= 512, bytes.prefix(8) == Data([0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1]) else { throw Failure.notOLE }
        let sectorShift = Int(bytes.u16(0x1E)), miniShift = Int(bytes.u16(0x20))
        guard (7...12).contains(sectorShift), (4...sectorShift).contains(miniShift) else { throw Failure.corrupt("sector size") }
        let sectorSize = 1 << sectorShift, miniSectorSize = 1 << miniShift
        let fatSectorCount = Int(bytes.u32(0x2C))
        let firstDirectory = bytes.u32(0x30)
        let miniCutoff = Int(bytes.u32(0x38))
        let firstMiniFAT = bytes.u32(0x3C)
        let firstDIFAT = bytes.u32(0x44), difatCount = Int(bytes.u32(0x48))

        // FAT sector numbers: 109 in the header, the rest chained through DIFAT sectors.
        var fatSectors: [UInt32] = []
        for i in 0..<109 where fatSectors.count < fatSectorCount {
            let s = bytes.u32(0x4C + i * 4)
            if Self.isSector(s) { fatSectors.append(s) }
        }
        var difat = firstDIFAT, seen = 0
        let perDIFAT = sectorSize / 4 - 1
        while Self.isSector(difat), seen < difatCount, fatSectors.count < fatSectorCount {
            let base = Self.offset(of: difat, sectorSize: sectorSize)
            guard base + sectorSize <= bytes.count else { throw Failure.corrupt("DIFAT") }
            for i in 0..<perDIFAT where fatSectors.count < fatSectorCount {
                let s = bytes.u32(base + i * 4)
                if Self.isSector(s) { fatSectors.append(s) }
            }
            difat = bytes.u32(base + perDIFAT * 4); seen += 1
        }
        var fat: [UInt32] = []
        fat.reserveCapacity(fatSectors.count * (sectorSize / 4))
        for s in fatSectors {
            let base = Self.offset(of: s, sectorSize: sectorSize)
            guard base + sectorSize <= bytes.count else { throw Failure.corrupt("FAT") }
            for i in 0..<(sectorSize / 4) { fat.append(bytes.u32(base + i * 4)) }
        }
        let chain = { (start: UInt32) -> Data in Self.chain(start, bytes: bytes, fat: fat, sectorSize: sectorSize) }

        // Directory entries, 128 bytes each.
        let directory = chain(firstDirectory)
        var entries: [(name: String, type: UInt8, start: UInt32, size: Int)] = []
        var off = 0
        while off + 128 <= directory.count {
            let nameLen = Int(directory.u16(off + 0x40))
            let type = directory[directory.startIndex + off + 0x42]
            if type != 0, nameLen >= 2 {
                let nameData = directory[(directory.startIndex + off)..<(directory.startIndex + off + max(0, Swift.min(nameLen - 2, 64)))]
                let name = String(data: nameData, encoding: .utf16LittleEndian) ?? ""
                entries.append((name, type, directory.u32(off + 0x74), Int(directory.u32(off + 0x78))))
            }
            off += 128
        }
        guard let root = entries.first(where: { $0.type == 5 }) else { throw Failure.corrupt("no root entry") }

        // The mini stream (small streams live in it) and its FAT.
        let miniStream = chain(root.start).prefix(root.size)
        var miniFAT: [UInt32] = []
        let miniFATData = chain(firstMiniFAT)
        var m = 0
        while m + 4 <= miniFATData.count { miniFAT.append(miniFATData.u32(m)); m += 4 }

        var streams: [Stream] = []
        for e in entries where e.type == 2 {
            var data: Data
            if e.size < miniCutoff {
                data = Data(); var s = e.start; var hops = 0
                while Self.isSector(s), hops <= miniFAT.count {
                    let base = Int(s) * miniSectorSize
                    guard base < miniStream.count else { break }
                    data.append(miniStream[(miniStream.startIndex + base)..<Swift.min(miniStream.startIndex + base + miniSectorSize, miniStream.endIndex)])
                    guard Int(s) < miniFAT.count else { break }
                    s = miniFAT[Int(s)]; hops += 1
                }
            } else {
                data = chain(e.start)
            }
            streams.append(Stream(name: e.name, data: Data(data.prefix(e.size))))
        }

        self.bytes = bytes
        self.sectorSize = sectorSize
        self.miniSectorSize = miniSectorSize
        self.fat = fat
        self.miniFAT = miniFAT
        self.miniStream = Data(miniStream)
        self.miniCutoff = miniCutoff
        self.streams = streams
    }

    /// The sectors of one chain through the FAT, joined.
    static func chain(_ start: UInt32, bytes: Data, fat: [UInt32], sectorSize: Int) -> Data {
        var out = Data(), s = start, hops = 0
        while isSector(s), hops <= fat.count {
            let base = offset(of: s, sectorSize: sectorSize)
            guard base < bytes.count else { break }
            out.append(bytes[base..<Swift.min(base + sectorSize, bytes.count)])
            guard Int(s) < fat.count else { break }
            s = fat[Int(s)]; hops += 1
        }
        return out
    }

    static func offset(of sector: UInt32, sectorSize: Int) -> Int { (Int(sector) + 1) * sectorSize }
    /// Below the special markers (FATSECT, DIFSECT, ENDOFCHAIN, FREESECT…).
    static func isSector(_ s: UInt32) -> Bool { s < 0xFFFF_FFFA }

    /// The stream called `name`, or the first whose name is in `names`.
    public func stream(_ names: String...) -> Data? {
        for n in names { if let s = streams.first(where: { $0.name == n }) { return s.data } }
        return nil
    }
}

extension Data {
    /// Little-endian readers at an offset from the data's start (whatever its `startIndex`).
    func u8(_ at: Int) -> UInt8 { self[startIndex + at] }
    func u16(_ at: Int) -> UInt16 { UInt16(u8(at)) | UInt16(u8(at + 1)) << 8 }
    func i16(_ at: Int) -> Int16 { Int16(bitPattern: u16(at)) }
    func u32(_ at: Int) -> UInt32 { UInt32(u16(at)) | UInt32(u16(at + 2)) << 16 }
    func f64(_ at: Int) -> Double { Double(bitPattern: UInt64(u32(at)) | UInt64(u32(at + 4)) << 32) }
    func sub(_ at: Int, _ count: Int) -> Data { Data(self[(startIndex + at)..<(startIndex + Swift.min(at + count, self.count))]) }
}
