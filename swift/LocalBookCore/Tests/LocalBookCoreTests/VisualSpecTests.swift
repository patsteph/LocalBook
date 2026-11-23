import Foundation
import SwiftUI
import Testing
@testable import LocalBookCore

/// `VisualSpec` reads the answer model's JSON leniently and draws one 16:9 slide. These cases pin the parser and
/// the slide geometry so a change shows up in CI before it shows up on a phone.
@Suite("Visual spec")
struct VisualSpecTests {
    @Test("a mind map survives prose and fences around the JSON")
    func mindmap() throws {
        let raw = """
        Here is the map you asked for:
        ```json
        {"title": "Photosynthesis", "branches": [
          {"title": "Inputs", "children": ["Light", "Water", "CO2"]},
          {"title": "Outputs", "children": ["Glucose", "Oxygen", ""]},
          {"title": "", "children": ["dropped"]}
        ]}
        ```
        """
        let spec = try #require(VisualSpec.parse(raw, layout: .mindmap))
        #expect(spec.title == "Photosynthesis")
        #expect(spec.items.map(\.title) == ["Inputs", "Outputs"])
        #expect(spec.items[1].children == ["Glucose", "Oxygen"])
    }

    @Test("a comparison needs both columns and rows")
    func comparison() {
        let good = #"{"title": "Cats vs dogs", "columns": ["Cats", "Dogs"], "rows": [{"label": "Noise", "cells": ["quiet", "loud"]}]}"#
        let spec = VisualSpec.parse(good, layout: .comparison)
        #expect(spec?.columns == ["Cats", "Dogs"])
        #expect(spec?.rows.count == 1)
        #expect(VisualSpec.parse(#"{"title": "x", "columns": ["A"]}"#, layout: .comparison) == nil)
    }

    @Test("numbers in stats are kept as text")
    func infographic() throws {
        let spec = try #require(VisualSpec.parse(#"{"title": "Coffee", "stats": [{"value": 42, "label": "cups"}], "points": []}"#, layout: .infographic))
        #expect(spec.stats.first?.value == "42")
        #expect(spec.stats.first?.title == "cups")
    }

    @Test("a reply in another shape is still drawn, as that shape's plain form")
    func anyShape() throws {
        let groupsAsItems = #"{"title": "Themes", "items": [{"title": "One", "children": ["a", "b"]}, {"title": "Two", "children": ["c"]}]}"#
        let m = try #require(VisualSpec.parse(groupsAsItems, layout: .mindmap))
        #expect(m.items.count == 2 && m.items[0].children == ["a", "b"])
        let steps = #"{"title": "Steps", "steps": [{"title": "One", "detail": "first"}, {"title": "Two", "detail": "second"}]}"#
        #expect(VisualSpec.parse(steps, layout: .quadrant) == nil)
        let q = try #require(VisualSpec.parseAnyShape(steps, preferring: .quadrant))
        #expect(q.layout == .flowchart && q.items.count == 2)
        #expect(VisualSpec.parseAnyShape(steps, preferring: .cycle)?.layout == .cycle)
        #expect(VisualSpec.parseAnyShape("no json", preferring: .mindmap) == nil)
    }

    @Test("nothing to draw gives nil")
    func empty() {
        #expect(VisualSpec.parse("no json here", layout: .timeline) == nil)
        #expect(VisualSpec.parse(#"{"title": "t", "events": []}"#, layout: .timeline) == nil)
    }

    @Test("the request names every layout's shape at every detail")
    func requests() {
        for layout in VisualSpec.Layout.allCases {
            for detail in VisualSpec.Detail.allCases {
                let r = VisualSpec.request(layout, about: "the notebook", detail: detail)
                #expect(r.contains("JSON object"))
                #expect(r.contains("\"title\""))
            }
        }
    }

    @Test("a slide renders at 2K in 16:9")
    @MainActor func slide() throws {
        let spec = try #require(VisualSpec.parse(#"{"title": "Steps", "steps": [{"title": "One", "detail": "first"}, {"title": "Two", "detail": "second"}]}"#, layout: .flowchart))
        let fit = VisualSlide.fit(for: spec)
        #expect(fit > 0.29 && fit <= 1)
        let image = try #require(VisualSlide.image(for: spec, fit: fit))
        let w = Double(image.size.width), h = Double(image.size.height)
        #expect(abs(w / h - 16.0 / 9.0) < 0.01)
    }
}
