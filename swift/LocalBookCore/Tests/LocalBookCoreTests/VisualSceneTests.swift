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

    @Test("every form in the catalogue draws a realistic fill inside the frame with no overlap")
    func catalogue() throws {
        let seq = (1...6).map { #"{"title": "Stage \#($0) of the work", "value": "\#($0 * 10) %", "detail": "What this stage does, said in one sentence that wraps once."}"# }.joined(separator: ",")
        let groups = (1...4).map { g in "{\"title\": \"Group \(g) name\", \"children\": [" + (1...3).map { "\"Yes \($0): a short phrase for group \(g)\"" }.joined(separator: ",") + "]}" }.joined(separator: ",")
        let two = #"{"title": "Pros", "children": ["Fast to set up", "Cheap to run for small teams", "Well documented"]}, {"title": "Cons", "children": ["Slower at scale", "Fewer integrations"]}"#
        let items = (1...8).map { #"{"title": "Item \#($0)", "x": \#($0 % 5 + 1), "y": \#(($0 * 2) % 5 + 1)}"# }.joined(separator: ",")
        for layout in VisualSpec.Layout.allCases {
            let json: String = switch layout.shape {
            case .sequence: #"{"title": "The subject", "items": [\#(seq)]}"#
            case .groups: layout == .prosCons || layout == .beforeAfter ? #"{"title": "The subject", "groups": [\#(two)]}"# : #"{"title": "The subject", "groups": [\#(groups)]}"#
            case .matrix: #"{"title": "The subject", "columns": ["A", "B"], "rows": [{"label": "Cost", "cells": ["low", "high"]}, {"label": "Speed", "cells": ["slow", "fast"]}]}"#
            case .quadrant: #"{"title": "The subject", "axes": ["Effort", "Impact"], "items": [\#(items)]}"#
            case .figures: #"{"title": "The subject", "stats": [{"value": "42 %", "label": "share"}, {"value": "1,200", "label": "items"}, {"value": "3×", "label": "faster"}], "points": [{"title": "One", "detail": "First point."}, {"title": "Two", "detail": "Second point."}]}"#
            }
            let s = try spec(json, layout)
            #expect(s.layout == layout)
            let scene = VisualLayout.scene(for: s)
            let bad = VisualLint.check(scene).filter { $0.kind == .offSlide || $0.kind == .overlap || $0.kind == .cut || $0.kind == .empty }
            #expect(bad.isEmpty, "\(layout): \(bad)")
            #expect(scene.scale >= 0.7, "\(layout) had to shrink to \(scene.scale)")
            #expect(texts(scene).count >= 3, "\(layout) says too little")
            for other in layout.alternatives {
                #expect(other.shape == layout.shape)
                #expect(!VisualLayout.scene(for: s.drawn(as: other)).nodes.isEmpty, "\(layout) drawn as \(other)")
            }
        }
    }

    @Test("a decision tree labels its branches and a quadrant places every item")
    func decisionsAndQuadrant() throws {
        let d = try spec(#"{"title": "Ship it?", "groups": [{"title": "Do the tests pass?", "children": ["Yes: ship it", "No: fix first"]}, {"title": "Is it Friday?", "children": ["Yes: wait for Monday", "No: deploy now"]}]}"#, .decisionTree)
        let ds = VisualLayout.scene(for: d)
        let words = texts(ds).flatMap(\.lines)
        #expect(words.contains("Yes") && words.contains("No") && words.contains("ship it"))
        let q = try spec(#"{"title": "Where to spend", "axes": ["Effort", "Impact"], "items": [{"title": "Search", "x": 1, "y": 5}, {"title": "Themes", "x": 5, "y": 1}]}"#, .quadrant)
        let qs = VisualLayout.scene(for: q)
        let dots = qs.nodes.filter { if case .dot = $0 { true } else { false } }
        #expect(dots.count == 2)
        #expect(texts(qs).flatMap(\.lines).contains { $0.contains("Effort") })
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

    @Test("every theme answers every role in both appearances and reads at WCAG contrast")
    func theme() {
        #expect(VisualTheme.all.count == 4 && Set(VisualTheme.all.map(\.id)).count == 4)
        for theme in VisualTheme.all {
            for dark in [false, true] {
                for role in [VisualRole.ink, .quiet, .surface, .rule, .background, .onAccent, .scrim, .onImage, .accent(0), .accent(7), .tint(-1)] {
                    let c = theme.color(role, dark: dark)
                    #expect(c.r >= 0 && c.r <= 1 && c.a > 0 && c.a <= 1)
                }
            }
            #expect(theme.color(.tint(2), dark: false).a < 1)
            #expect(theme.contrastProblems().isEmpty, "\(theme.contrastProblems())")
            for icon in VisualIcon.allCases { #expect(!theme.symbol(for: icon).isEmpty) }
        }
        #expect(VisualTheme.RGB(0x2f6fed).hex == "#2f6fed")
        #expect(VisualTheme.RGB.contrast(VisualTheme.RGB(0xffffff), VisualTheme.RGB(0x000000)) > 20)
        #expect(VisualTheme.named("deck").id == "deck" && VisualTheme.named(nil) == .clean && VisualTheme.named("neon") == .clean)
    }

    @Test("a theme changes the face, the corners and the SVG, and the picture still fits")
    func themed() throws {
        let steps = (1...5).map { #"{"title": "Step \#($0)", "detail": "What happens in step \#($0), in one sentence."}"# }
        let s = try spec(#"{"title": "How it works", "items": [\#(steps.joined(separator: ","))]}"#, .flowchart)
        let plain = VisualLayout.scene(for: s)
        for theme in VisualTheme.all {
            let dressed = s.dressed(in: theme)
            #expect(dressed.theme == theme.id)
            let scene = VisualLayout.scene(for: dressed)
            #expect(VisualLint.check(scene).filter { $0.kind != .tiny }.isEmpty, "\(theme.id): \(VisualLint.check(scene))")
            #expect(texts(scene).allSatisfy { $0.style.design == theme.design }, Comment(rawValue: theme.id))
            let svg = VisualSlide.svg(for: dressed)
            #expect(svg.contains(theme.svgFontFamily) && svg.contains("fill='\(theme.light.background.hex)'"))
            if theme.heavyHeadings { #expect(texts(scene).contains { $0.style.weight == .heavy }) }
            if theme.outlinedCards { #expect(svg.contains("stroke='\(theme.light.ink.hex)'"), "tinted cards get an ink outline") }
        }
        #expect(VisualLayout.scene(for: s.dressed(in: .editorial)) != plain, "a serif measures differently")
        let data = try JSONEncoder().encode(s.dressed(in: .deck))
        let back = try JSONDecoder().decode(VisualSpec.self, from: data)
        #expect(back.theme == "deck")
        let old = try JSONDecoder().decode(VisualSpec.self, from: Data(#"{"layout": "mindmap", "title": "old"}"#.utf8))
        #expect(old.theme == nil && old.layout == .mindmap, "items saved before themes still open")
    }

    @Test("a backdrop takes the band's place under white words, the SVG puts the picture inline, and the forms without one are unchanged")
    func hero() throws {
        let offer = #"{"title": "One engine for every picture", "items": [{"title": "Faster", "children": ["No Python at runtime"]}, {"title": "Richer", "children": ["Seventeen forms", "Four themes"]}]}"#
        let plain = try spec(offer, .valueProposition)
        var painted = plain; painted.hero = "test-hero.png"
        let band = VisualLayout.scene(for: plain), picture = VisualLayout.scene(for: painted)
        #expect(!band.nodes.contains { if case .image = $0 { true } else { false } })
        #expect(picture.nodes.contains { if case .image = $0 { true } else { false } })
        #expect(picture.nodes.contains { if case .card(_, let fill, _, _, _) = $0, fill == .scrim { true } else { false } })
        #expect(texts(picture).contains { $0.style.role == .onImage } && !texts(band).contains { $0.style.role == .onImage })
        #expect(VisualLint.check(picture).filter { $0.kind != .tiny }.isEmpty, "\(VisualLint.check(picture))")
        // The infographic writes its title on the picture instead of above it; a flowchart never asks for one.
        let facts = #"{"title": "By the numbers", "stats": [{"value": "17", "label": "forms"}, {"value": "4", "label": "themes"}], "points": [{"title": "One engine", "detail": "The same scene on Mac and iPhone."}]}"#
        var info = try spec(facts, .infographic); info.hero = "x.png"
        let infoScene = VisualLayout.scene(for: info)
        #expect(infoScene.nodes.contains { if case .image = $0 { true } else { false } } && VisualLint.check(infoScene).filter { $0.kind != .tiny }.isEmpty)
        #expect(VisualHero.wants(.valueProposition) && VisualHero.wants(.infographic) && !VisualHero.wants(.flowchart))
        // No file on this device: the SVG draws the band; with the bytes, the picture goes inline and clipped.
        let fallback = VisualSVG.render(picture)
        #expect(!fallback.contains("<image") && fallback.contains("<rect"))
        let inline = VisualSVG.render(picture, hero: Data([0x89, 0x50, 0x4e, 0x47]))
        #expect(inline.contains("<image href='data:image/png;base64,iVBORw") && inline.contains("<clipPath id='hero1'"))
        // The prompt carries the title and forbids words; the seed is stable.
        let prompt = VisualHero.prompt(for: plain)
        #expect(prompt.contains("One engine for every picture") && prompt.contains("no text") && prompt.contains("Faster"))
        #expect(VisualHero.seed(for: plain) == VisualHero.seed(for: painted) && VisualHero.seed(for: plain) != VisualHero.seed(for: info))
        #expect(VisualHero.width % 16 == 0 && VisualHero.height % 16 == 0)
        // Saved and read back; an item saved before backdrops has none.
        let data = try JSONEncoder().encode(painted)
        #expect(try JSONDecoder().decode(VisualSpec.self, from: data).hero == "test-hero.png")
        #expect(try JSONDecoder().decode(VisualSpec.self, from: Data(#"{"layout": "value_proposition", "title": "old"}"#.utf8)).hero == nil)
    }
}
