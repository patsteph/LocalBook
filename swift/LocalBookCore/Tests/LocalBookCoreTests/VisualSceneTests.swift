import CoreGraphics
import Foundation
import Testing
@testable import LocalBookCore

/// The visuals engine's deterministic half: `VisualLayout` places a spec as a scene, `VisualLint` checks it and
/// `VisualSVG` writes it. These run with the real fonts, so a card that cannot hold its words fails here first.
@Suite("Visual scene")
struct VisualSceneTests {
    private func spec(_ json: String, _ layout: VisualSpec.Layout) throws -> VisualSpec {
        try #require(VisualSpec.parse(json, layout: layout))
    }

    private func texts(_ scene: VisualScene) -> [VisualTextBlock] {
        scene.nodes.compactMap { node -> VisualTextBlock? in if case .text(let t) = node { return t } else { return nil } }
    }

    @Test("words wrap inside their box and never past it")
    func wrapping() {
        let style = VisualTextStyle(size: 20)
        let (lines, lh, ascent) = VisualText.wrap("A reranker scores each candidate passage against the question and keeps the best few for the answer model", style: style, width: 300)
        #expect(lines.count >= 3)
        #expect(lh > 20 && ascent > 10)
        for line in lines { #expect(VisualText.width(of: line, style: style) <= 300, "\(line)") }
        #expect(lines.joined(separator: " ").contains("answer model"))
        #expect(VisualText.wrap("one\n\ntwo", style: style, width: 300).lines == ["one", "two"])
    }

    @Test("a flowchart of seven steps snakes over two rows, fits and passes the lint")
    func flowchart() throws {
        let steps = (1...7).map { #"{"title": "Step \#($0) with a name", "detail": "What happens in this step, in one sentence that wraps."}"# }
        let s = try spec(#"{"title": "How it works", "steps": [\#(steps.joined(separator: ","))]}"#, .flowchart)
        let scene = VisualLayout.scene(for: s)
        let cards = scene.nodes.filter { if case .card = $0 { true } else { false } }
        #expect(cards.count == 7)
        let arrows = scene.nodes.filter { if case .fill = $0 { true } else { false } }
        #expect(arrows.count == 6, "an arrow between each pair of steps, including the turn between rows")
        #expect(VisualLint.check(scene).isEmpty, "\(VisualLint.check(scene))")
        #expect(scene.scale == 1)
        let frame = CGRect(origin: .zero, size: scene.size)
        for r in scene.frames { #expect(frame.contains(r)) }
    }

    @Test("a deep mind map opened everywhere shrinks to fit instead of running off the slide")
    func mindmap() throws {
        let branches = (1...8).map { b in
            let children = (1...6).map { "\"Child \($0) of branch \(b) with a few more words\"" }.joined(separator: ",")
            return "{\"title\": \"Branch \(b) title\", \"children\": [\(children)]}"
        }
        let s = try spec(#"{"title": "Everything in the notebook", "branches": [\#(branches.joined(separator: ","))]}"#, .mindmap)
        let folded = VisualLayout.scene(for: s)
        #expect(VisualLint.check(folded).filter { $0.kind == .offSlide || $0.kind == .overlap }.isEmpty)
        #expect(texts(folded).contains { $0.lines.first == "+3 more" })
        let open = VisualLayout.scene(for: s, expanded: Set(0..<8))
        #expect(open.scale < 1)
        #expect(VisualLint.check(open).filter { $0.kind == .offSlide || $0.kind == .overlap }.isEmpty, "\(VisualLint.check(open))")
        let hits = open.nodes.compactMap { node -> Int? in if case .card(_, _, _, _, let h) = node { return h } else { return nil } }
        #expect(Set(hits) == Set(0..<8), "every branch capsule can be tapped")
    }

    @Test("a comparison, a timeline and an infographic at depth fit the frame")
    func others() throws {
        let rows = (1...10).map { #"{"label": "Criterion \#($0)", "cells": ["Option A does this well enough", "Option B is slower here", "Option C is not offered"]}"# }
        let c = try spec(#"{"title": "Alternatives", "columns": ["A", "B", "C"], "rows": [\#(rows.joined(separator: ","))]}"#, .comparison)
        let events = (1...12).map { #"{"when": "Month \#($0)", "title": "Event \#($0)", "detail": "A sentence about what changed and why it mattered."}"# }
        let t = try spec(#"{"title": "History", "events": [\#(events.joined(separator: ","))]}"#, .timeline)
        let i = try spec(#"{"title": "What matters", "stats": [{"value": "42 %", "label": "share"}, {"value": "1,200", "label": "items"}, {"value": "3×", "label": "faster"}, {"value": "$4.5M", "label": "saved"}, {"value": "9", "label": "teams"}], "points": [{"title": "One", "detail": "First point in a sentence."}, {"title": "Two", "detail": "Second point."}, {"title": "Three", "detail": "Third point, longer, wrapping to a second line when needed."}]}"#, .infographic)
        for s in [c, t, i] {
            let scene = VisualLayout.scene(for: s)
            let bad = VisualLint.check(scene).filter { $0.kind == .offSlide || $0.kind == .overlap || $0.kind == .cut }
            #expect(bad.isEmpty, "\(s.layout): \(bad)")
            #expect(VisualLayout.scene(for: s) == scene, "the same spec always draws the same picture")
        }
    }

    @Test("the SVG names every line, escapes markup and carries the theme's colours")
    func svg() throws {
        let s = try spec(#"{"title": "Cats & dogs", "steps": [{"title": "<Feed>", "detail": "twice a day"}]}"#, .flowchart)
        let scene = VisualLayout.scene(for: s)
        let svg = VisualSVG.render(scene)
        #expect(svg.hasPrefix("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1600 900'"))
        #expect(svg.hasSuffix("</svg>"))
        #expect(svg.contains("<title>Cats &amp; dogs</title>"))
        #expect(svg.contains(">&lt;Feed&gt;</text>"))
        let lines = texts(scene).map(\.lines.count).reduce(0, +)
        #expect(svg.components(separatedBy: "<text ").count - 1 == lines)
        #expect(svg.contains("fill='\(VisualTheme.clean.light.accents[0].hex)'"))
        #expect(VisualSVG.render(scene, dark: true).contains("fill='\(VisualTheme.clean.dark.background.hex)'"))
    }

    @Test("the lint sees a card past the edge, an overlap and an empty slide")
    func lint() {
        var scene = VisualScene()
        #expect(VisualLint.check(scene).map(\.kind) == [.empty])
        scene.add(.card(CGRect(x: 1500, y: 100, width: 200, height: 100), fill: .surface, stroke: nil, radius: 8, hit: nil))
        scene.add(.card(CGRect(x: 100, y: 100, width: 200, height: 100), fill: .surface, stroke: nil, radius: 8, hit: nil))
        scene.add(.card(CGRect(x: 200, y: 150, width: 200, height: 100), fill: .surface, stroke: nil, radius: 8, hit: nil))
        scene.add(.text(VisualTextBlock(frame: CGRect(x: 100, y: 400, width: 200, height: 24), lines: ["words"], style: .init(size: 20), lineHeight: 24, ascent: 18, truncated: false)))
        let kinds = VisualLint.check(scene).map(\.kind)
        #expect(kinds.contains(.offSlide) && kinds.contains(.overlap) && !kinds.contains(.empty))
    }

    @Test("the theme answers every role in both appearances")
    func theme() {
        for dark in [false, true] {
            for role in [VisualRole.ink, .quiet, .surface, .rule, .background, .onAccent, .accent(0), .accent(7), .tint(-1)] {
                let c = VisualTheme.clean.color(role, dark: dark)
                #expect(c.r >= 0 && c.r <= 1 && c.a > 0 && c.a <= 1)
            }
        }
        #expect(VisualTheme.clean.color(.tint(2), dark: false).a < 1)
        #expect(VisualTheme.RGB(0x2f6fed).hex == "#2f6fed")
    }
}
