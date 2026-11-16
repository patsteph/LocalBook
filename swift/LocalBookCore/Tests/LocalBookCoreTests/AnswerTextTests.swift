import Foundation
import Testing
@testable import LocalBookCore

@Suite("Answer text")
struct AnswerTextTests {
    @Test("every citation form links its numbers the same way")
    func citations() {
        #expect(AnswerText.linkCitations("Seen [3].") == "Seen \\[[3](lb-source://3)\\].")
        #expect(AnswerText.linkCitations("Both [1, 3] agree") == "Both \\[[1](lb-source://1), [3](lb-source://3)\\] agree")
        #expect(AnswerText.linkCitations("[2;5]") == "\\[[2](lb-source://2);[5](lb-source://5)\\]")
        #expect(AnswerText.linkCitations("in 2024 [100] no") == "in 2024 [100] no")
        #expect(AnswerText.linkCitations("[x]") == "[x]")
    }

    @Test("rendered citations carry our link and the brackets stay visible")
    func rendered() {
        let out = AnswerText.render("Yes [1, 2].")
        #expect(String(out.characters) == "Yes [1, 2].")
        let links = out.runs.compactMap { $0.link }.map(\.absoluteString)
        #expect(links == ["lb-source://1", "lb-source://2"])
    }

    @Test("a line markdown cannot parse keeps its words, not link syntax")
    func fallback() {
        let out = AnswerText.render("odd *unclosed [4]")
        #expect(!String(out.characters).contains("lb-source"))
    }
}
