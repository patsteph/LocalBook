import Foundation
import Testing
@testable import LocalBookCore

/// `chunk_golden.json` is written by the old app's own chunker and token-budget splitter over fixed texts
/// (`scripts/ci/chunk_golden.py`). The Swift port has to make the same pieces, byte for byte, on every case: that is
/// how a source ingested by this app gets the same chunks, and so the same citations, as one the Python Mac ingested.
@Suite("Chunking matches the old app")
struct ChunkerTests {
    struct Fixture: Decodable {
        struct Case: Decodable { let name: String; let sourceType: String; let filename: String; let text: String; let chunks: [String]; let budgeted: [String]; let parents: [String] }
        let chunkSize: Int; let chunkOverlap: Int; let tokenBudget: Int; let cases: [Case]
    }

    static func load() throws -> Fixture {
        let url = try #require(Bundle.module.url(forResource: "chunk_golden", withExtension: "json", subdirectory: "Fixtures"))
        let d = JSONDecoder(); d.keyDecodingStrategy = .convertFromSnakeCase
        return try d.decode(Fixture.self, from: Data(contentsOf: url))
    }

    /// Where two lists first differ, for a readable failure.
    static func firstDifference(_ a: [String], _ b: [String]) -> String? {
        if a == b { return nil }
        for (i, (x, y)) in zip(a, b).enumerated() where x != y {
            return "chunk \(i) differs:\n  swift (\(x.count)): \(x.prefix(160))…\n  python (\(y.count)): \(y.prefix(160))…"
        }
        return "swift made \(a.count) chunks, python \(b.count)"
    }

    @Test("the settings the fixture was made with are the ones built in")
    func settings() throws {
        let f = try Self.load()
        #expect(f.chunkSize == Chunker.chunkSize)
        #expect(f.chunkOverlap == Chunker.chunkOverlap)
        #expect(f.tokenBudget == TokenBudget.budget)
        #expect(f.cases.count >= 9)
    }

    @Test("every case chunks exactly as the Python functions did")
    func chunks() throws {
        for c in try Self.load().cases {
            let got = Chunker.chunks(text: c.text, sourceType: c.sourceType, filename: c.filename)
            #expect(got == c.chunks, "\(c.name): \(Self.firstDifference(got, c.chunks) ?? "")")
        }
    }

    @Test("the token budget splits the same chunks the same way")
    func budget() throws {
        let f = try Self.load()
        for c in f.cases {
            let got = TokenBudget.enforce(c.chunks, budget: f.tokenBudget, count: TokenBudget.fallbackCount)
            #expect(got == c.budgeted, "\(c.name): \(Self.firstDifference(got, c.budgeted) ?? "")")
        }
    }

    @Test("parent text around each chunk matches")
    func parents() throws {
        for c in try Self.load().cases {
            let got = c.chunks.indices.map { Chunker.parentContext(c.chunks, index: $0) }
            #expect(got == c.parents, "\(c.name): \(Self.firstDifference(got, c.parents) ?? "")")
        }
    }

    @Test("a pdf's heading lines stay in the index and tiny trailing pieces fold back")
    func hierarchyShape() {
        let text = (0..<3).map { "# Part \($0)\n" + String(repeating: "Sentence number one about retrieval. ", count: 12) }.joined(separator: "\n\n") + "\n\n# End\nshort."
        let chunks = Chunker.chunks(text: text, sourceType: "pdf", filename: "x.pdf")
        #expect(!chunks.isEmpty)
        #expect(chunks.contains { $0.contains("# Part 0") })
        #expect(chunks.last.map { $0.contains("short.") } == true)
    }
}
