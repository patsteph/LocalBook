import Foundation
import Testing
@testable import LocalBookCore

/// The per-notebook answer boundary. The passages here are what a coffee notebook returns for *any* question, because
/// nearest-neighbour search always returns its nearest neighbours; the gate has to tell a real match from "nearest".
@Suite("Answer boundary")
struct AnswerBoundaryTests {
    static let coffee: [AnswerBoundary.Passage] = [
        .init(text: "Harbor Lights Coffee roasts on Tuesdays and Fridays. The roastery opens at 7 and the first pour is at 7:30.", rerank: 0.004),
        .init(text: "Shipping policy: orders over 40 dollars ship free within the state; everything else is a flat 6 dollars.", rerank: 0.002),
        .init(text: "The Harbor Blend is a medium roast of Colombian and Ethiopian beans with notes of cherry and cocoa.", rerank: 0.003),
        .init(text: "Staff handbook: shifts are six hours, breaks are twenty minutes, and the espresso machine is cleaned nightly.", rerank: 0.001),
        .init(text: "Quarterly review: wholesale accounts grew 12 percent and the Netflix campus cart opened in March.", rerank: 0.002),
    ]

    @Test("a mistyped or unknown question finds no evidence in a notebook that has nothing about it")
    func typo() {
        #expect(!AnswerBoundary.hasEvidence(question: "ttt", passages: Self.coffee))
        #expect(!AnswerBoundary.hasEvidence(question: "Explain the transformer architecture", passages: Self.coffee))
        #expect(!AnswerBoundary.hasEvidence(question: "What is test-time training?", passages: Self.coffee))
    }

    @Test("a word of the question in a passage is evidence, allowing a short common stem")
    func lexical() {
        #expect(AnswerBoundary.hasEvidence(question: "What time does the roastery open?", passages: Self.coffee))
        #expect(AnswerBoundary.hasEvidence(question: "When are the roasting days?", passages: Self.coffee))      // roast-ing vs roast-s
        #expect(AnswerBoundary.hasEvidence(question: "How long are the breaks?", passages: Self.coffee))
        #expect(AnswerBoundary.hasEvidence(question: "Netflix?", passages: Self.coffee))
        #expect(AnswerBoundary.hasEvidence(question: "orders over 40", passages: Self.coffee))
    }

    @Test("a strong reranker score is evidence on its own, a weak one is not")
    func rerank() {
        let paraphrase = [AnswerBoundary.Passage(text: "Beans arrive green and go in the machine twice a week.", rerank: 0.42)]
        #expect(AnswerBoundary.hasEvidence(question: "How often do you cook the coffee?", passages: paraphrase))
        let weak = [AnswerBoundary.Passage(text: "Beans arrive green and go in the machine twice a week.", rerank: 0.05)]
        #expect(!AnswerBoundary.hasEvidence(question: "How often do you cook the coffee?", passages: weak))
    }

    @Test("a question about the notebook as a whole always has evidence")
    func holistic() {
        for q in ["What are the main points in this notebook?", "What changed most recently?", "Who is mentioned most often?",
                  "Summarize these notes", "Give me an overview", "What are the key themes?"] {
            #expect(AnswerBoundary.isHolistic(q), Comment(rawValue: q))
            #expect(AnswerBoundary.hasEvidence(question: q, passages: Self.coffee), Comment(rawValue: q))
        }
        #expect(!AnswerBoundary.isHolistic("ttt"))
        #expect(!AnswerBoundary.isHolistic("What time does the roastery open?"))
    }

    @Test("chatter and stopwords alone are not evidence, and no passages is never evidence")
    func chatter() {
        #expect(AnswerBoundary.terms(of: "What is the?").isEmpty)
        #expect(AnswerBoundary.terms(of: "hello").isEmpty)
        #expect(AnswerBoundary.terms(of: "What's Netflix's cart?") == ["netflix", "cart"])
        #expect(!AnswerBoundary.hasEvidence(question: "hello", passages: Self.coffee))
        #expect(!AnswerBoundary.hasEvidence(question: "What are the main points?", passages: []))
    }

    @Test("the refusal and the instructions name the notebook, and the model's own refusal is recognised")
    func wording() {
        let no = AnswerBoundary.noAnswer(notebook: "Harbor Lights Coffee")
        #expect(no.hasPrefix("I couldn't find anything about that in Harbor Lights Coffee."))
        #expect(AnswerBoundary.noAnswer(notebook: "  ").contains("this notebook"))
        let rules = AnswerBoundary.instructions(notebook: "Harbor Lights Coffee")
        #expect(rules.contains("“Harbor Lights Coffee”"))
        #expect(rules.contains("Never use anything you know from elsewhere"))
        #expect(rules.contains("I couldn't find anything about that in Harbor Lights Coffee."))
        #expect(AnswerBoundary.isRefusal("I couldn't find anything about that in Harbor Lights Coffee."))
        #expect(AnswerBoundary.isRefusal("I couldn't find this in the documents."))
        #expect(AnswerBoundary.isRefusal(no))
        #expect(!AnswerBoundary.isRefusal("The roastery opens at 7 [1]."))
        let long = String(repeating: "The roastery opens at 7 [1]. ", count: 20) + "I couldn't find this in the documents for the Friday hours."
        #expect(!AnswerBoundary.isRefusal(long))
    }
}
