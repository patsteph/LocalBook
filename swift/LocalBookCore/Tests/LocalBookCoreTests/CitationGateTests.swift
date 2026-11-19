import Foundation
import Testing
@testable import LocalBookCore

/// The citation gate's pure half: the fixture, the verdict for one question, and the report's bar.
@Suite("Citation gate")
struct CitationGateTests {
    static let fixtureJSON = #"""
    {"format":"localbook-citation-fixture/1","created_at":"2026-11-17T18:00:00+00:00",
     "mac":{"device":"mbp","embedder":"embeddinggemma-2","reranker":"flashrank/fp32","analysis":"fallback","top_k":4,"overcollect":12},
     "notebook_sources":{"nb1":["s1","s2","s3"]},"bm25_blind":{"nb1":["s3"]},"unsearchable":{},
     "questions":[{"notebook_id":"nb1","notebook_title":"Retail","question":"What did Costco say about renewals?","low_confidence":false,"candidates":12,
       "citations":[{"source_id":"s1","chunk_index":3,"filename":"q3.pdf","confidence":0.81},{"source_id":"s2","chunk_index":0,"filename":"10-K.pdf","confidence":0.44}]},
      {"notebook_id":"nb1","notebook_title":"Retail","question":"Nothing here","citations":[]}]}
    """#

    static func fixture() throws -> CitationGate.Fixture { try CitationGate.Fixture.load(Data(fixtureJSON.utf8)) }
    static func c(_ s: String, _ i: Int) -> CitationGate.Citation { .init(sourceId: s, chunkIndex: i) }

    @Test("the fixture loads with its questions and refuses another format")
    func load() throws {
        let f = try Self.fixture()
        #expect(f.questions.count == 2 && f.questions[0].citations[0].key == "s1/3" && f.questions[0].candidates == 12)
        #expect(f.mac.reranker == "flashrank/fp32")
        #expect(f.notebookSources?["nb1"] == ["s1", "s2", "s3"] && f.bm25Blind?["nb1"] == ["s3"] && f.unsearchable?.isEmpty == true)
        #expect(throws: (any Error).self) { _ = try CitationGate.Fixture.load(Data(#"{"format":"x","created_at":"","mac":{"device":"","embedder":"","reranker":"","analysis":""},"questions":[]}"#.utf8)) }
    }

    @Test("verdicts: the same list, the same set, a shared citation, nothing shared, both empty")
    func verdicts() throws {
        let q = try Self.fixture().questions[0]
        #expect(CitationGate.Result(q, ours: [Self.c("s1", 3), Self.c("s2", 0)]).verdict == .identical)
        #expect(CitationGate.Result(q, ours: [Self.c("s2", 0), Self.c("s1", 3)]).verdict == .sameSet)
        let o = CitationGate.Result(q, ours: [Self.c("s1", 3), Self.c("s9", 1)])
        #expect(o.verdict == .overlap && o.shared == 1)
        #expect(CitationGate.Result(q, ours: [Self.c("s1", 4)]).verdict == .different)
        #expect(CitationGate.Result(try Self.fixture().questions[1], ours: []).verdict == .identical)
        #expect(CitationGate.Result(q, verdict: .failed, problem: "search model not ready").verdict == .failed)
    }

    @Test("the report counts, passes at 95 % same-set over the compared questions, and lists what to explain first")
    func report() throws {
        let f = try Self.fixture()
        let q = f.questions[0]
        var results = (0..<19).map { _ in CitationGate.Result(q, ours: q.citations) }
        results.append(CitationGate.Result(q, ours: [Self.c("s2", 0), Self.c("s1", 3)]))
        results.append(CitationGate.Result(q, verdict: .notebookMissing, problem: nil))
        var r = CitationGate.Report(fixture: f, results: results, device: "mac", recipe: "recipe 7", leftOut: ["Retail": 2], bm25Blind: ["Retail": 1])
        #expect(r.summary.compared == 20 && r.summary.sameRate == 1 && r.summary.exactRate == 0.95 && r.summary.passes && r.summary.missing == 1)
        #expect(r.markdown().contains("were left out of the search: Retail 2.") && r.markdown().contains("keyword ranking, as there: Retail 1."))
        #expect(r.results[0].verdict == .notebookMissing)
        #expect(r.markdown().contains("**PASS**: cited the same on 100 %") && r.markdown().contains("## To explain"))
        results.append(CitationGate.Result(q, ours: []))
        results.append(CitationGate.Result(q, ours: [Self.c("zz", 0)]))
        r = CitationGate.Report(fixture: f, results: results, device: "mac", recipe: "recipe 7")
        #expect(r.summary.compared == 22 && !r.summary.passes && r.results[0].verdict == .different)
        let md = r.markdown()
        #expect(md.contains("**NOT YET**") && md.contains("| [1] | q3.pdf #3 (81 %) |  |"))
        let json = String(decoding: try r.json(), as: UTF8.self)
        #expect(json.contains("\"same_rate\"") == false && json.contains("\"fixture_made\""))
    }
}
