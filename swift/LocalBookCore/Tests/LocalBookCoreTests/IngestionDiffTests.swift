import Foundation
import Testing
@testable import LocalBookCore

/// The compare behind Settings › Diagnostics › Ingestion diff: verdicts, the line score, and the report's shape.
@Suite("Ingestion diff compares as the gate needs")
struct IngestionDiffTests {
    static func file(_ name: String, text: String?, chunks: [String]?, error: String? = nil) -> IngestionDiff.Fixture.File {
        IngestionDiff.Fixture.File(name: name, format: (name as NSString).pathExtension, bytes: text?.utf8.count ?? 0, sha256: "", text: text, chunks: chunks, error: error)
    }

    static let fixtureJSON = """
    {"format": "localbook-ingestion-fixture/1", "created_at": "2026-11-17T12:00:00+00:00", "folder": "set", "recursive": false,
     "python": "3.12.1", "token_counter": "tokenizer", "token_budget": 400,
     "files": [{"name": "a.txt", "format": "txt", "bytes": 5, "sha256": "x", "text": "Hello", "chunks": ["Hello"]},
               {"name": "b.pdf", "format": "pdf", "bytes": 9, "sha256": "y", "error": "ValueError: no text"},
               {"name": "https://x.y/p", "url": "https://x.y/p", "title": "P", "format": "web", "bytes": 2, "sha256": "z", "text": "Hi", "chunks": ["Hi"]}]}
    """

    @Test("the fixture the Python script writes decodes, and another format is refused")
    func decode() throws {
        let f = try IngestionDiff.Fixture.load(Data(Self.fixtureJSON.utf8))
        #expect(f.files.count == 3)
        #expect(f.files[0].chunks == ["Hello"])
        #expect(f.files[0].url == nil)
        #expect(f.files[2].url == "https://x.y/p")
        #expect(f.files[2].title == "P")
        #expect(f.files[1].error?.hasPrefix("ValueError") == true)
        #expect(f.tokenBudget == 400)
        let other = Self.fixtureJSON.replacingOccurrences(of: "ingestion-fixture/1", with: "parity-fixture/1")
        #expect(throws: IngestionDiff.Problem.self) { try IngestionDiff.Fixture.load(Data(other.utf8)) }
    }

    @Test("identical text and chunks, whitespace aside")
    func identical() {
        let f = Self.file("a.md", text: "# Title\n\nBody  line one.\nBody line two.", chunks: ["# Title\n\nBody  line one.\nBody line two."])
        let r = IngestionDiff.compare(f, here: .read(text: "# Title\n\n\nBody line one.\n  Body line two.\n", chunks: ["# Title\n\nBody  line one.\nBody line two."]))
        #expect(r.verdict == .identical)
        #expect(r.textScore == 1)
        #expect(r.chunksMatched == 1)
    }

    @Test("same lines cut differently is the chunker's difference, not the reader's")
    func sameText() {
        let text = (1...6).map { "Line \($0)." }.joined(separator: "\n")
        let f = Self.file("a.txt", text: text, chunks: [text])
        let r = IngestionDiff.compare(f, here: .read(text: text, chunks: ["Line 1.\nLine 2.\nLine 3.", "Line 4.\nLine 5.\nLine 6."]))
        #expect(r.verdict == .sameText)
        #expect(r.pythonChunks == 1 && r.swiftChunks == 2 && r.chunksMatched == 0)
        #expect(r.onlyPython == nil && r.onlySwift == nil)
    }

    @Test("a reader that loses a few lines is close; one that loses many differs")
    func closeAndDiffers() {
        let lines = (1...100).map { "Sentence number \($0) of the document." }
        let f = Self.file("a.pdf", text: lines.joined(separator: "\n"), chunks: [lines.joined(separator: "\n")])
        let close = IngestionDiff.compare(f, here: .read(text: lines.dropLast(2).joined(separator: "\n"), chunks: ["x"]))
        #expect(close.verdict == .close)
        #expect(close.textScore > 0.95 && close.textScore < 1)
        #expect(close.onlyPython == "Sentence number 99 of the document.")
        #expect(close.onlySwift == nil)
        let far = IngestionDiff.compare(f, here: .read(text: lines.prefix(50).joined(separator: "\n") + "\nExtra here.", chunks: ["x"]))
        #expect(far.verdict == .differs)
        #expect(far.onlySwift == "Extra here.")
    }

    @Test("failures and missing files get their own verdicts")
    func failures() {
        let f = Self.file("a.pdf", text: "Hello", chunks: ["Hello"])
        #expect(IngestionDiff.compare(f, here: .missing).verdict == .missing)
        #expect(IngestionDiff.compare(f, here: .unreadable).verdict == .unreadable)
        #expect(IngestionDiff.compare(f, here: .failed("boom")).verdict == .failedHere)
        #expect(IngestionDiff.compare(f, here: .failed("boom")).note?.contains("boom") == true)
        let there = Self.file("b.pdf", text: nil, chunks: nil, error: "ValueError: no text")
        #expect(IngestionDiff.compare(there, here: .read(text: "Hello", chunks: ["Hello"])).verdict == .failedThere)
    }

    @Test("the report counts, sorts worst first, and round-trips through JSON")
    func report() throws {
        let fixture = try IngestionDiff.Fixture.load(Data(Self.fixtureJSON.utf8))
        let same = IngestionDiff.compare(fixture.files[0], here: .read(text: "Hello", chunks: ["Hello"]))
        let there = IngestionDiff.compare(fixture.files[1], here: .read(text: "Some", chunks: ["Some"]))
        let far = IngestionDiff.compare(Self.file("c.txt", text: "One\nTwo", chunks: ["One\nTwo"]), here: .read(text: "Three", chunks: ["Three"]))
        let report = IngestionDiff.Report(fixture: fixture, files: [same, there, far], swiftTokenCounter: "estimate", device: "test")
        #expect(report.summary.files == 3 && report.summary.identical == 1 && report.summary.failed == 1 && report.summary.differs == 1)
        #expect(report.summary.identicalRate == 0.5)
        #expect(report.files.map(\.name) == ["c.txt", "b.pdf", "a.txt"])
        let back = try JSONDecoder().decode(IngestionDiff.Report.self, from: Self.snakeToCamel(report.json()))
        #expect(back.files.count == 3 && back.summary.identical == 1)
        let md = report.markdown()
        #expect(md.contains("# Ingestion diff: set"))
        #expect(md.contains("### c.txt · txt · differs"))
        #expect(md.contains("- a.txt · txt · 1 chunks"))
    }

    /// The report is written snake_case for the Python side; decode it the way `Fixture.load` would.
    static func snakeToCamel(_ data: Data) throws -> Data {
        let d = JSONDecoder(); d.keyDecodingStrategy = .convertFromSnakeCase
        let e = JSONEncoder()
        return try e.encode(d.decode(IngestionDiff.Report.self, from: data))
    }
}
