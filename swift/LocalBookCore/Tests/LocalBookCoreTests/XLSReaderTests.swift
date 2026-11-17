import Foundation
import Testing
@testable import LocalBookCore

/// `XLSReader` + `TabularText` against what xlrd, pandas and the old app's extractor made of the same books.
struct XLSReaderTests {
    struct Golden: Decodable { let cases: [Case] }
    struct Case: Decodable { let name: String; let xls: String; let text: String }

    static func golden() throws -> Golden {
        let url = try #require(Bundle.module.url(forResource: "xls_golden", withExtension: "json", subdirectory: "Fixtures"))
        return try JSONDecoder().decode(Golden.self, from: Data(contentsOf: url))
    }

    @Test func booksMatchTheOldApp() throws {
        for c in try Self.golden().cases {
            let data = try #require(Data(base64Encoded: c.xls))
            let sheets = try XLSReader.read(data)
            let got = TabularText.workbookText(sheets)
            #expect(got == c.text, "\(c.name): got\n\(got.debugDescription)\nwanted\n\(c.text.debugDescription)")
        }
    }

    @Test func rkNumbers() {
        #expect(XLSReader.Reader.rk(0x0000_001E) == 7)              // 7 << 2 | int flag
        #expect(XLSReader.Reader.rk(0x0000_001F) == 0.07)           // same, divided by 100
        #expect(XLSReader.Reader.rk(0x3FF8_0000) == 1.5)            // top of the double 1.5
        #expect(XLSReader.Reader.rk(UInt32(bitPattern: -4 | 2)) == -1)
    }

    @Test func dateFormats() {
        #expect(XLSReader.Reader.looksLikeDateFormat("YYYY-MM-DD"))
        #expect(XLSReader.Reader.looksLikeDateFormat("[$-409]d-mmm-yy;@"))
        #expect(!XLSReader.Reader.looksLikeDateFormat("#,##0.00"))
        #expect(!XLSReader.Reader.looksLikeDateFormat("\"Day\" 0"))
        #expect(XLSReader.Reader.date(45356, from1904: false) == "2024-03-05 00:00:00")
    }

    @Test func notAnOLEFileThrows() {
        #expect(throws: (any Error).self) { try XLSReader.read(Data("PK\u{3}\u{4}not ole".utf8)) }
    }
}
