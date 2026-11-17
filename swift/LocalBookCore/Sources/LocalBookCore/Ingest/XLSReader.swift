import Foundation

/// Excel 97-2003 (.xls, BIFF8) sheets as pandas over xlrd gave them to the old app: every cell as text, numbers
/// as xlrd's floats (whole ones without the ".0", which pandas then reads as integers), dates by their format,
/// booleans as True/False, formulas by their cached result. Proved by `Fixtures/xls_golden.json`
/// (`scripts/ci/xls_golden.py`: xlwt writes the books, the old app's extractor reads them).
public enum XLSReader {
    public enum Failure: LocalizedError {
        case noWorkbook, notBIFF8
        public var errorDescription: String? {
            switch self {
            case .noWorkbook: return "No workbook was found in the file"
            case .notBIFF8: return "Excel 5.0/95 workbooks are not read; save the file as .xlsx"
            }
        }
    }

    public static func read(_ data: Data) throws -> [(name: String, table: TextTable)] {
        let ole = try OLE2(data)
        guard let book = ole.stream("Workbook", "Book") else { throw Failure.noWorkbook }
        var reader = Reader(book)
        return try reader.sheets().map { (name: $0.name, table: TextTable.fromRows($0.rows)) }
    }

    // MARK: records

    struct Record { let type: UInt16; let start: Int; let length: Int }

    /// Every record in the stream, in order.
    static func records(_ d: Data) -> [Record] {
        var out: [Record] = [], off = 0
        while off + 4 <= d.count {
            let type = d.u16(off), len = Int(d.u16(off + 2))
            guard off + 4 + len <= d.count else { break }
            out.append(Record(type: type, start: off + 4, length: len))
            off += 4 + len
        }
        return out
    }

    struct Reader {
        let d: Data
        let recs: [Record]
        var shared: [String] = []
        var formats: [UInt16: String] = [:]     // custom number formats by id
        var xfFormat: [UInt16] = []             // XF index → format id
        var date1904 = false

        init(_ d: Data) { self.d = d; recs = XLSReader.records(d) }

        mutating func sheets() throws -> [(name: String, rows: [[String]])] {
            guard let bof = recs.first, bof.type == 0x0809 else { throw Failure.noWorkbook }
            guard d.u16(bof.start) >= 0x0600 else { throw Failure.notBIFF8 }
            var bounds: [(name: String, pos: Int, kind: UInt8)] = []
            var i = 0
            while i < recs.count {
                let r = recs[i]
                switch r.type {
                case 0x000A: i = recs.count; continue                       // EOF of the globals
                case 0x0022: date1904 = d.u16(r.start) == 1                 // DATEMODE
                case 0x0085:                                               // BOUNDSHEET
                    let pos = Int(d.u32(r.start)), kind = d.u8(r.start + 5)
                    bounds.append((shortString(at: r.start + 6, in: r), pos, kind))
                case 0x00FC:                                               // SST with its CONTINUEs
                    var parts = [d.sub(r.start, r.length)]
                    while i + 1 < recs.count, recs[i + 1].type == 0x003C { i += 1; parts.append(d.sub(recs[i].start, recs[i].length)) }
                    shared = sharedStrings(parts)
                case 0x041E:                                               // FORMAT
                    formats[d.u16(r.start)] = unicodeString(at: r.start + 2, in: r)
                case 0x00E0:                                               // XF
                    xfFormat.append(d.u16(r.start + 2))
                default: break
                }
                i += 1
            }
            var out: [(String, [[String]])] = []
            for b in bounds where b.kind == 0 {                            // worksheets only (not charts, macros)
                out.append((b.name, sheet(at: b.pos)))
            }
            return out
        }

        // MARK: one sheet

        func sheet(at pos: Int) -> [[String]] {
            guard let first = recs.firstIndex(where: { $0.start - 4 == pos }) else { return [] }
            var cells: [Int: [Int: String]] = [:]
            var maxRow = -1, maxCol = -1
            func put(_ row: Int, _ col: Int, _ s: String) {
                cells[row, default: [:]][col] = s
                maxRow = max(maxRow, row); maxCol = max(maxCol, col)
            }
            var i = first
            while i < recs.count {
                let r = recs[i]
                switch r.type {
                case 0x000A: i = recs.count; continue                       // EOF
                case 0x00FD:                                               // LABELSST
                    let idx = Int(d.u32(r.start + 6))
                    put(Int(d.u16(r.start)), Int(d.u16(r.start + 2)), idx < shared.count ? shared[idx] : "")
                case 0x0204:                                               // LABEL
                    put(Int(d.u16(r.start)), Int(d.u16(r.start + 2)), unicodeString(at: r.start + 6, in: r))
                case 0x0203:                                               // NUMBER
                    put(Int(d.u16(r.start)), Int(d.u16(r.start + 2)), number(d.f64(r.start + 6), xf: d.u16(r.start + 4)))
                case 0x027E:                                               // RK
                    put(Int(d.u16(r.start)), Int(d.u16(r.start + 2)), number(Self.rk(d.u32(r.start + 6)), xf: d.u16(r.start + 4)))
                case 0x00BD:                                               // MULRK
                    let row = Int(d.u16(r.start)), colFirst = Int(d.u16(r.start + 2))
                    var off = 4, col = colFirst
                    while off + 6 <= r.length - 2 {
                        put(row, col, number(Self.rk(d.u32(r.start + off + 2)), xf: d.u16(r.start + off)))
                        off += 6; col += 1
                    }
                case 0x0205:                                               // BOOLERR
                    let isError = d.u8(r.start + 7) == 1
                    put(Int(d.u16(r.start)), Int(d.u16(r.start + 2)), isError ? "" : (d.u8(r.start + 6) == 1 ? "True" : "False"))
                case 0x0006:                                               // FORMULA with its cached result
                    let row = Int(d.u16(r.start)), col = Int(d.u16(r.start + 2)), xf = d.u16(r.start + 4)
                    if d.u16(r.start + 12) == 0xFFFF {
                        switch d.u8(r.start + 6) {
                        case 0:                                            // string in the STRING record that follows
                            if i + 1 < recs.count, recs[i + 1].type == 0x0207 { i += 1; put(row, col, unicodeString(at: recs[i].start, in: recs[i])) }
                            else { put(row, col, "") }
                        case 1: put(row, col, d.u8(r.start + 8) == 1 ? "True" : "False")
                        default: put(row, col, "")                         // error or empty string
                        }
                    } else {
                        put(row, col, number(d.f64(r.start + 6), xf: xf))
                    }
                default: break
                }
                i += 1
            }
            guard maxRow >= 0 else { return [] }
            return (0...maxRow).map { r in (0...maxCol).map { c in cells[r]?[c] ?? "" } }
        }

        // MARK: values

        /// An RK number: a 30-bit integer or the top of a double, both optionally divided by 100.
        static func rk(_ raw: UInt32) -> Double {
            var v: Double
            if raw & 0x02 != 0 { v = Double(Int32(bitPattern: raw) >> 2) }
            else { v = Double(bitPattern: UInt64(raw & 0xFFFF_FFFC) << 32) }
            if raw & 0x01 != 0 { v /= 100 }
            return v
        }

        func number(_ v: Double, xf: UInt16) -> String {
            if isDate(xf: xf) { return Self.date(v, from1904: date1904) }
            if v == v.rounded(), abs(v) < 1e15 { return String(Int64(v)) }
            return "\(v)"
        }

        /// xlrd's reading of a cell's format: built-in date ids, or a custom format whose letters are date letters.
        func isDate(xf: UInt16) -> Bool {
            guard Int(xf) < xfFormat.count else { return false }
            let fmt = xfFormat[Int(xf)]
            if (14...22).contains(fmt) || (27...36).contains(fmt) || (45...47).contains(fmt) || (50...58).contains(fmt) { return true }
            guard let s = formats[fmt] else { return false }
            return Self.looksLikeDateFormat(s)
        }

        static func looksLikeDateFormat(_ format: String) -> Bool {
            // Drop quoted text, [colours] and escaped characters; then date letters decide.
            var s = "", quoted = false, bracket = false, escape = false
            for ch in format {
                if escape { escape = false; continue }
                if ch == "\\" { escape = true; continue }
                if ch == "\"" { quoted.toggle(); continue }
                if ch == "[" { bracket = true; continue }
                if ch == "]" { bracket = false; continue }
                if !quoted, !bracket { s.append(ch) }
            }
            let lower = s.lowercased()
            if lower.contains("0") || lower.contains("#") || lower.contains("?") { return false }
            return lower.contains("y") || lower.contains("d") || lower.contains("h") || lower.contains("s") || lower.contains("m")
        }

        /// An Excel serial day as pandas prints the Timestamp xlrd made of it.
        static func date(_ serial: Double, from1904: Bool) -> String {
            let epoch: Double = from1904 ? 24107 : 25569                  // days from the workbook's day 0 to 1970-01-01
            let seconds = ((serial - epoch) * 86400).rounded()
            let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.timeZone = TimeZone(identifier: "UTC"); f.dateFormat = "yyyy-MM-dd HH:mm:ss"
            return f.string(from: Date(timeIntervalSince1970: seconds))
        }

        // MARK: strings

        /// BIFF8 short string (8-bit length): sheet names.
        func shortString(at off: Int, in r: Record) -> String {
            let n = Int(d.u8(off)), flags = d.u8(off + 1)
            return Self.chars(d, at: off + 2, count: n, wide: flags & 1 != 0)
        }

        /// BIFF8 unicode string (16-bit length) inside one record.
        func unicodeString(at off: Int, in r: Record) -> String {
            guard off + 3 <= r.start + r.length else { return "" }
            let n = Int(d.u16(off)), flags = d.u8(off + 2)
            var p = off + 3
            if flags & 0x08 != 0 { p += 2 }
            if flags & 0x04 != 0 { p += 4 }
            return Self.chars(d, at: p, count: n, wide: flags & 1 != 0)
        }

        static func chars(_ d: Data, at off: Int, count: Int, wide: Bool) -> String {
            let bytes = d.sub(off, wide ? count * 2 : count)
            return (wide ? String(data: bytes, encoding: .utf16LittleEndian) : String(data: bytes, encoding: .windowsCP1252)) ?? ""
        }

        /// The shared string table across its CONTINUE records: a string's characters may run into the next record,
        /// which then starts with a fresh width flag; rich-text runs and extended data are skipped across records too.
        func sharedStrings(_ parts: [Data]) -> [String] {
            var cursor = Cursor(parts: parts)
            cursor.skip(8)                                                  // total and unique counts
            let unique = Int(parts[0].u32(4))
            var out: [String] = []
            out.reserveCapacity(unique)
            for _ in 0..<unique {
                guard cursor.remaining >= 3 else { break }
                let n = Int(cursor.u16()), flags = cursor.u8()
                var runs = 0, ext = 0
                if flags & 0x08 != 0 { runs = Int(cursor.u16()) }
                if flags & 0x04 != 0 { ext = Int(cursor.u32()) }
                out.append(cursor.chars(n, wide: flags & 1 != 0))
                cursor.skip(runs * 4 + ext)
            }
            return out
        }

        struct Cursor {
            let parts: [Data]
            var part = 0, off = 0
            init(parts: [Data]) { self.parts = parts }

            var remaining: Int { parts.indices.filter { $0 > part }.reduce(parts[part].count - off) { $0 + parts[$1].count } }

            /// Moves to the next record when this one is used up; true when a boundary was crossed.
            mutating func settle() -> Bool {
                var crossed = false
                while part < parts.count, off >= parts[part].count { part += 1; off = 0; crossed = true }
                return crossed && part < parts.count
            }
            mutating func u8() -> UInt8 { _ = settle(); guard part < parts.count else { return 0 }; let v = parts[part].u8(off); off += 1; return v }
            mutating func u16() -> UInt16 { UInt16(u8()) | UInt16(u8()) << 8 }
            mutating func u32() -> UInt32 { UInt32(u16()) | UInt32(u16()) << 16 }
            mutating func skip(_ n: Int) {
                var left = n
                while left > 0, part < parts.count {
                    _ = settle(); guard part < parts.count else { return }
                    let take = min(left, parts[part].count - off); off += take; left -= take
                }
            }
            /// `n` characters, re-reading the width flag at each record boundary crossed mid-string.
            mutating func chars(_ n: Int, wide: Bool) -> String {
                var s = "", left = n, wide = wide
                while left > 0, part < parts.count {
                    if settle() { wide = parts[part].u8(off) & 1 != 0; off += 1 }
                    guard part < parts.count else { break }
                    let width = wide ? 2 : 1
                    let avail = (parts[part].count - off) / width
                    guard avail > 0 else { off = parts[part].count; continue }
                    let take = min(left, avail)
                    s += XLSReader.Reader.chars(parts[part], at: off, count: take, wide: wide)
                    off += take * width; left -= take
                }
                return s
            }
        }
    }
}
