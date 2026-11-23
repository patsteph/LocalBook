import Foundation
import Testing
@testable import LocalBookCore

/// The 15 prompts both apps draw for the side-by-side judgement.
@Suite("Visual golden set")
struct VisualGoldenSetTests {
    @Test("fifteen prompts, three per layout, one at each depth, with distinct ids")
    func shape() {
        let p = VisualGoldenSet.prompts
        #expect(p.count == 15)
        #expect(Set(p.map(\.id)).count == 15)
        for layout in VisualSpec.Layout.allCases {
            let mine = p.filter { $0.layout == layout }
            #expect(mine.count == 3, "\(layout) has \(mine.count)")
            #expect(Set(mine.map(\.detail)) == Set(VisualSpec.Detail.allCases), "\(layout) misses a depth")
        }
        #expect(p.allSatisfy { !$0.oldTemplate.isEmpty })
    }

    @Test("the index lists every prompt with its picture or its failure")
    func index() {
        let p = VisualGoldenSet.prompts
        let md = VisualGoldenSet.index(notebook: "Trip", device: "MBP", date: Date(timeIntervalSince1970: 1_700_000_000),
                                       made: [.init(prompt: p[0], file: "01-mindmap-overview.png", seconds: 41.4), .init(prompt: p[1], problem: "model stopped")])
        #expect(md.hasPrefix("# Visual golden set: Trip"))
        #expect(md.contains("| 1 | Mind map | Overview | (the whole notebook) | [01-mindmap-overview.png](01-mindmap-overview.png) | 41 s | clean |"))
        #expect(md.contains("| 2 | Mind map | Standard | the main ideas and how they connect | failed: model stopped |  |  |"))
        let html = VisualGoldenSet.indexHTML(notebook: "Trip & co", device: "MBP", date: Date(timeIntervalSince1970: 1_700_000_000),
                                             made: [.init(prompt: p[0], file: "01-mindmap-overview.png", seconds: 41.4, lint: ["cut: “x” lost lines"]), .init(prompt: p[1], problem: "model <stopped>")])
        #expect(html.contains("<title>Visual golden set: Trip &amp; co</title>"))
        #expect(html.contains("(the whole notebook) · 41 s · cut: “x” lost lines</p>"))
        #expect(html.contains("<img src='01-mindmap-overview.png' alt='01-mindmap-overview'>"))
        #expect(html.contains("<p class='err'>failed: model &lt;stopped&gt;</p>"))
    }
}
