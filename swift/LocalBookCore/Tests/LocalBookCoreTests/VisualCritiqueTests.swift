import Foundation
import Testing
@testable import LocalBookCore

/// The critic's words and the reading of its reply, without a model.
@Suite("Visual critique")
struct VisualCritiqueTests {
    @Test("a clean JSON reply, a spelled-out one and a ragged one are all read")
    func parse() throws {
        let clean = try #require(VisualCritique.parse(#"{"legibility": 4, "hierarchy": 3, "balance": 5, "colour": 4, "message": 2, "problem": "the title repeats the headline"}"#))
        #expect(clean.legibility == 4 && clean.message == 2 && clean.problem == "the title repeats the headline")
        #expect(clean.mean == 3.6 && clean.passes && clean.line == "3.6/5: the title repeats the headline")
        let american = try #require(VisualCritique.parse("Sure. {\"legibility\":2,\"hierarchy\":2,\"balance\":3,\"color\":2,\"message\":3,\"problem\":\"\"}"))
        #expect(american.colour == 2 && !american.passes && american.line == "2.4/5")
        let ragged = try #require(VisualCritique.parse("legibility: 5\nhierarchy: 4\nbalance: 4"))
        #expect(ragged.colour == 4 && ragged.message == 4, "missing axes take the mean of the rest")
        #expect(VisualCritique.parse("I cannot see an image.") == nil)
        #expect(VisualCritique.parse("legibility: 5") == nil, "one score is not a verdict")
    }

    @Test("the prompt names the five axes and asks for JSON only")
    func prompt() {
        let p = VisualCritique.prompt(title: "Cats & dogs", form: "Flowchart")
        for axis in ["legibility", "hierarchy", "balance", "colour", "message", "problem"] { #expect(p.contains(axis), Comment(rawValue: axis)) }
        #expect(p.contains("flowchart titled “Cats & dogs”") && p.contains("ONLY {"))
        #expect(VisualCritique.bar == 3.5)
    }
}
