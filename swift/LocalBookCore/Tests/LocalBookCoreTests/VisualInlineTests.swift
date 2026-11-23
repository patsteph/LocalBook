import Foundation
import Testing
@testable import LocalBookCore

/// Visuals fenced inside briefings: found, drawn, dropped when they draw nothing, left out of plain text.
@Suite("Visuals in documents")
struct VisualInlineTests {
    let doc = """
    # Agents at work

    ## What they are
    - Programs that plan and act.

    ```visual
    {"title": "Agents", "items": [{"title": "Plan", "children": ["break the goal down"]}, {"title": "Act", "children": ["call tools", "check results"]}]}
    ```

    ## Why it matters
    They change how software is built [1].
    """

    @Test("a good fence becomes a picture between the prose; the prose keeps its order")
    func pieces() throws {
        let pieces = VisualInline.pieces(in: doc)
        #expect(pieces.count == 3)
        guard case .text(let before) = pieces[0], case .visual(let spec) = pieces[1], case .text(let after) = pieces[2] else { Issue.record("unexpected shape"); return }
        #expect(before.contains("# Agents at work") && !before.contains("```"))
        #expect(spec.layout == .mindmap && spec.items.count == 2 && spec.items[1].children.count == 2)
        #expect(after.trimmingCharacters(in: .whitespacesAndNewlines).hasPrefix("## Why it matters"))
        #expect(VisualInline.hasVisual(doc) && !VisualInline.hasVisual("# Plain\n\nNo picture here."))
    }

    @Test("a fence that draws nothing is dropped, from the view and from the saved document")
    func badFence() {
        let bad = "# T\n\n```visual\nnot json at all\n```\n\nAfter."
        #expect(VisualInline.pieces(in: bad) == [.text("# T\n"), .text("\nAfter.")] || VisualInline.pieces(in: bad).allSatisfy { if case .text = $0 { true } else { false } })
        #expect(!VisualInline.cleaned(bad).contains("```"))
        #expect(VisualInline.cleaned(bad).contains("After."))
        let good = VisualInline.cleaned(doc)
        #expect(good.contains("```visual\n{") && good.hasSuffix("[1]."))
        #expect(VisualInline.hasVisual(good))
    }

    @Test("plain text leaves the picture out; an export can put an SVG in its place")
    func rendered() {
        let plain = VisualInline.stripped(doc)
        #expect(!plain.contains("```") && !plain.contains("\"title\"") && plain.contains("## Why it matters"))
        let html = VisualInline.rendered(doc) { VisualSlide.svg(for: $0) }
        #expect(html.contains("<svg") && html.contains("Agents") && !html.contains("```"))
        #expect(VisualInline.promptClause.contains("```visual") && VisualInline.promptClause.contains("mind map"))
    }

    @Test("an unclosed fence at the end still draws")
    func unclosed() {
        let s = "# T\n```visual\n{\"title\": \"X\", \"items\": [{\"title\": \"A\", \"children\": [\"a\"]}]}"
        #expect(VisualInline.hasVisual(s))
    }

    @Test("share files take a Finder-safe name and the three kinds")
    func files() {
        #expect(VisualFiles.name("Why / choose: us?") == "Why   choose  us")
        #expect(VisualFiles.name("   ") == "Visual")
        #expect(VisualFiles.Kind.allCases.map(\.rawValue) == ["png", "svg", "pdf"])
    }
}
