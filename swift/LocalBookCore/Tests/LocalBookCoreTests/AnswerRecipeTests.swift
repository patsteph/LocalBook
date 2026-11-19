import Foundation
import Testing
@testable import LocalBookCore

/// The Mac publishes its answer recipe with conformance vectors computed by its own Python functions
/// (`backend/services/sync/phone_recipe.py`). The fixture is that JSON, written by the backend in the cloud. These
/// tests re-run the Swift ports on the vectors, which is the same proof the phone runs at every sync.
@Suite("Answer recipe conformance")
struct AnswerRecipeTests {
    static func load() throws -> AnswerRecipe {
        let url = try #require(Bundle.module.url(forResource: "phone_recipe", withExtension: "json", subdirectory: "Fixtures"))
        let d = JSONDecoder(); d.keyDecodingStrategy = .convertFromSnakeCase
        return try d.decode(AnswerRecipe.self, from: Data(contentsOf: url))
    }

    @Test("the fixture decodes and names a version")
    func decodes() throws {
        let r = try Self.load()
        #expect(r.recipeVersion.count == 12)
        #expect(r.systemPrompts["factual"] != nil && r.systemPrompts["synthesis"] != nil && r.systemPrompts["complex"] != nil)
        #expect(!r.conformance.classification.isEmpty)
    }

    @Test("every conformance vector agrees with the Mac")
    func conformance() throws {
        let r = try Self.load()
        let problems = r.check()
        #expect(problems.isEmpty, "\(problems)")
    }

    @Test("citation numbers that point at no source are dropped")
    func citationFix() throws {
        let r = try Self.load()
        #expect(r.fixCitations("Fine [1] and [2, 3].", count: 3) == "Fine [1] and [2, 3].")
        #expect(r.fixCitations("Mixed [1, 562] and [2].", count: 4) == "Mixed [1] and [2].")
        #expect(r.fixCitations("Words [see 2] and [1-3] stay.", count: 5) == "Words [see 2] and [1-3] stay.")
    }

    @Test("BM25 sees the question and its quarter, while the synonym expansions go to the embedding only")
    func keywordQuery() throws {
        let r = try Self.load()
        #expect(r.keywordQuery("How many demos in Q1 2026?") == "How many demos in Q1 2026? Q 1 FY 2026")
        #expect(r.keywordQuery("Q3 pipeline?") == "Q3 pipeline? Q 3 FY 2026")
        #expect(r.keywordQuery("How many demos last year?") == "How many demos last year?")
        #expect(r.expand("How many demos last year?") != "How many demos last year?")
    }

    @Test("retrieval numbers keep the Mac's ratios")
    func retrieval() throws {
        let r = try Self.load()
        #expect(r.retrieval.hybridK == 2 * r.retrieval.overcollect)
        #expect(r.retrieval.vectorCandidates == 4 * r.retrieval.overcollect)
        #expect(r.citations.minConfidence == 0.20)
    }
}
