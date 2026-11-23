import Foundation
import Testing
@testable import LocalBookCore

/// The pick: the words of the ask choose the form when they name one, and the model's one-word reply is read leniently.
@Suite("Visual pick")
struct VisualPickTests {
    @Test("asks that name a form get it without the model")
    func rules() {
        let cases: [(String, VisualSpec.Layout)] = [
            ("how the main process works, step by step", .flowchart),
            ("the decisions and what follows from each", .decisionTree),
            ("how things developed over time, with dates", .timeline),
            ("the two most important options or sides", .comparison),
            ("strengths and weaknesses of the approaches described", .prosCons),
            ("the numbers that matter most", .statCallouts),
            ("key facts, figures and names at a glance", .infographic),
            ("every important concept, grouped by theme", .clusters),
            ("the main ideas and how they connect", .mindmap),
            ("the customer lifecycle", .cycle),
            ("who does what when an order comes in", .swimlane),
            ("the org chart of the company", .tree),
            ("the layers of the architecture, from the bottom up", .pyramid),
            ("rank the risks", .rankedList),
            ("effort vs impact of the proposals", .quadrant),
            ("before and after the migration", .beforeAfter),
            ("the value proposition of the product", .valueProposition),
        ]
        for (ask, want) in cases { #expect(VisualPick.rule(for: ask) == want, ask) }
    }

    @Test("asks that name no form leave the choice to the model")
    func noRule() {
        #expect(VisualPick.rule(for: "") == nil)
        #expect(VisualPick.rule(for: "the trip to Lisbon") == nil)
        #expect(VisualPick.rule(for: "what the author believes") == nil)
    }

    @Test("the model's reply is read as an id, a label or a word inside a sentence")
    func parse() {
        #expect(VisualPick.parse("decision_tree") == .decisionTree)
        #expect(VisualPick.parse("I would use a decision tree here.") == .decisionTree)
        #expect(VisualPick.parse("Mind map") == .mindmap)
        #expect(VisualPick.parse("stat_callouts\n") == .statCallouts)
        #expect(VisualPick.parse("Key numbers") == .statCallouts)
        #expect(VisualPick.parse("tree") == .tree)
        #expect(VisualPick.parse("2×2 matrix") == .quadrant)
        #expect(VisualPick.parse("nothing useful") == nil)
    }

    @Test("the prompt offers every form once, briefly")
    func prompt() {
        let p = VisualPick.prompt(about: "the notebook", passages: ["A passage.\nWith a line break.", String(repeating: "x", count: 1000)])
        for l in VisualSpec.Layout.allCases { #expect(p.components(separatedBy: "\n" + l.rawValue + ":").count == 2, l.rawValue) }
        #expect(!p.contains("\nWith a line break"))
        #expect(p.count < 2200)
    }
}
