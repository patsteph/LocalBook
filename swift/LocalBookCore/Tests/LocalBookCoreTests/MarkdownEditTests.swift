import Foundation
import Testing
@testable import LocalBookCore

/// The note editor's Markdown: styling spans and the formatting commands, on the text as typed.
@Suite("Markdown editing commands")
struct MarkdownEditTests {
    func run(_ text: String, _ selection: NSRange, _ action: MarkdownEdit.Action) -> (String, NSRange)? {
        guard let e = MarkdownEdit.edit(text, selection: selection, action: action) else { return nil }
        return (e.apply(to: text), e.selection)
    }

    @Test("headings toggle on the current line and keep the caret on its text")
    func headings() throws {
        let (t1, s1) = try #require(run("Title\nBody", NSRange(location: 2, length: 0), .heading(2)))
        #expect(t1 == "## Title\nBody")
        #expect(s1 == NSRange(location: 5, length: 0))
        let (t2, _) = try #require(run(t1, NSRange(location: 5, length: 0), .heading(2)))
        #expect(t2 == "Title\nBody")
        let (t3, _) = try #require(run(t1, NSRange(location: 5, length: 0), .heading(1)))
        #expect(t3 == "# Title\nBody")
        #expect(run("Plain", NSRange(location: 0, length: 0), .heading(0)) == nil)
    }

    @Test("lists over several lines, with blank lines between items left alone")
    func lists() throws {
        let text = "One\nTwo\n\nThree"
        let all = NSRange(location: 0, length: text.utf16.count)
        let (bullets, sel) = try #require(run(text, all, .bullets))
        #expect(bullets == "- One\n- Two\n\n- Three")
        #expect(sel == NSRange(location: 0, length: bullets.utf16.count))
        let (numbers, _) = try #require(run(bullets, NSRange(location: 0, length: bullets.utf16.count), .numbers))
        #expect(numbers == "1. One\n2. Two\n\n3. Three")
        let (plain, _) = try #require(run(numbers, NSRange(location: 0, length: numbers.utf16.count), .numbers))
        #expect(plain == text)
        let (tasks, _) = try #require(run("- [x] done\nnext", NSRange(location: 0, length: 15), .checklist))
        #expect(tasks == "- [x] done\n- [ ] next")
        let (quote, _) = try #require(run("a\nb", NSRange(location: 0, length: 3), .quote))
        #expect(quote == "> a\n> b")
    }

    @Test("bold, italic and code wrap, unwrap, and keep spaces outside the markers")
    func inline() throws {
        let (b, bs) = try #require(run("make this bold now", NSRange(location: 5, length: 4), .bold))
        #expect(b == "make **this** bold now")
        #expect(bs == NSRange(location: 7, length: 4))
        let (u, _) = try #require(run(b, bs, .bold))                       // markers just outside the selection
        #expect(u == "make this bold now")
        let (u2, _) = try #require(run(b, NSRange(location: 5, length: 8), .bold))   // markers inside the selection
        #expect(u2 == "make this bold now")
        let (sp, _) = try #require(run("a b c", NSRange(location: 1, length: 3), .italic))
        #expect(sp == "a _b_ c")
        let (empty, es) = try #require(run("x", NSRange(location: 1, length: 0), .code))
        #expect(empty == "x``")
        #expect(es == NSRange(location: 2, length: 0))
        let (gone, _) = try #require(run(empty, es, .code))
        #expect(gone == "x")
    }

    @Test("links select what to type next")
    func links() throws {
        let (l, ls) = try #require(run("see docs", NSRange(location: 4, length: 4), .link(nil)))
        #expect(l == "see [docs](https://)")
        #expect((l as NSString).substring(with: ls) == "https://")
        let (l2, ls2) = try #require(run("", NSRange(location: 0, length: 0), .link("https://x.y")))
        #expect(l2 == "[link](https://x.y)")
        #expect((l2 as NSString).substring(with: ls2) == "link")
    }

    @Test("Return continues a list, numbers on, and ends it on an empty item")
    func newline() throws {
        let e1 = try #require(MarkdownEdit.newline("- one", selection: NSRange(location: 5, length: 0)))
        #expect(e1.apply(to: "- one") == "- one\n- ")
        let e2 = try #require(MarkdownEdit.newline("1. one\n2. two", selection: NSRange(location: 13, length: 0)))
        #expect(e2.apply(to: "1. one\n2. two") == "1. one\n2. two\n3. ")
        let e3 = try #require(MarkdownEdit.newline("- [x] done", selection: NSRange(location: 10, length: 0)))
        #expect(e3.replacement == "\n- [ ] ")
        let e4 = try #require(MarkdownEdit.newline("- one\n- ", selection: NSRange(location: 8, length: 0)))
        #expect(e4.apply(to: "- one\n- ") == "- one\n")
        #expect(MarkdownEdit.newline("plain", selection: NSRange(location: 5, length: 0)) == nil)
        #expect(MarkdownEdit.newline("- one", selection: NSRange(location: 1, length: 0)) == nil)     // caret inside the marker
    }

    @Test("task boxes toggle and words are counted without syntax")
    func tasksAndWords() throws {
        let t = try #require(MarkdownEdit.toggleTask("- [ ] milk\n- [x] eggs", at: 2))
        #expect(t.apply(to: "- [ ] milk\n- [x] eggs") == "- [x] milk\n- [x] eggs")
        #expect(MarkdownEdit.toggleTask("plain", at: 0) == nil)
        #expect(MarkdownEdit.wordCount("# Two words\n\n- one\n- [ ] two three\n```\ncode here\n```") == 7)
    }

    @Test("spans mark headings, markers, inline marks and fences, inside the text's bounds")
    func spans() {
        let text = "# Head\n> quoted\n- item with **bold** and `code`\n- [x] done [docs](https://d)\n```\nlet x = *1*\n```\n>"
        let spans = MarkdownEdit.spans(text)
        let ns = text as NSString
        for s in spans { #expect(s.range.location + s.range.length <= ns.length, "\(s) runs past the text") }
        func has(_ style: MarkdownEdit.Style, _ sub: String) -> Bool {
            spans.contains { $0.style == style && ns.substring(with: $0.range) == sub }
        }
        #expect(has(.heading(1), "# Head"))
        #expect(has(.syntax, "# "))
        #expect(has(.quote, "> quoted"))
        #expect(has(.listMarker, "- "))
        #expect(has(.bold, "**bold**"))
        #expect(has(.code, "`code`"))
        #expect(has(.taskDone, "- [x] done [docs](https://d)"))
        #expect(has(.listMarker, "- [x] "))
        #expect(has(.link, "docs"))
        #expect(has(.syntax, "](https://d)"))
        #expect(has(.codeBlock, "let x = *1*"))
        #expect(!has(.italic, "*1*"))
        #expect(has(.syntax, ">"))
    }
}
