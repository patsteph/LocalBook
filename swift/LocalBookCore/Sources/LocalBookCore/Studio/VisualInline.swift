import CoreGraphics
import Foundation
import SwiftUI

/// Visuals inside written documents. A briefing may carry one fenced block, ```visual … ```, holding the JSON of a
/// picture the model wrote alongside the prose (one call, no second model turn); the document views draw it in
/// place with the same engine as a Studio visual, exports put the SVG inline, and plain-text sharing leaves it out.
/// A fence whose JSON draws nothing is dropped, so a reader never sees raw JSON.
public enum VisualInline {
    public static let fence = "```visual"

    public enum Piece: Equatable, Sendable {
        case text(String)
        case visual(VisualSpec)
    }

    /// What the briefing prompt asks for: one picture, as a mind map of the briefing, in a fence the parser knows.
    public static let promptClause = "Where a picture would help, add ONE block that starts with a line ```visual and ends with a line ```, holding only JSON for a mind map of the briefing: {\"title\": at most 6 words, \"items\": [3 to 5 groups {\"title\": at most 4 words, \"children\": [2 or 3 phrases of at most 6 words]}]}. Put it after the first section."

    /// The document split into prose and the visuals fenced in it, in order; fences that draw nothing vanish.
    public static func pieces(in markdown: String) -> [Piece] {
        var out: [Piece] = []
        var prose: [String] = []
        var json: [String]? = nil
        func flush() {
            let text = prose.joined(separator: "\n")
            if !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { out.append(.text(text)) }
            prose = []
        }
        for line in markdown.components(separatedBy: "\n") {
            let t = line.trimmingCharacters(in: .whitespaces)
            if json == nil, t.lowercased().hasPrefix(fence) {
                flush(); json = []
            } else if var open = json {
                if t == "```" {
                    if let spec = VisualSpec.parseAnyShape(open.joined(separator: "\n"), preferring: .mindmap) { out.append(.visual(spec)) }
                    json = nil
                } else {
                    open.append(line); json = open
                }
            } else {
                prose.append(line)
            }
        }
        if let open = json, let spec = VisualSpec.parseAnyShape(open.joined(separator: "\n"), preferring: .mindmap) { out.append(.visual(spec)) }   // an unclosed fence at the end
        flush()
        return out
    }

    public static func hasVisual(_ markdown: String) -> Bool { pieces(in: markdown).contains { if case .visual = $0 { true } else { false } } }

    /// The document with each fenced visual turned into whatever `visual` returns (an SVG, an image tag, nothing).
    public static func rendered(_ markdown: String, visual: (VisualSpec) -> String) -> String {
        pieces(in: markdown).map { piece in
            switch piece {
            case .text(let t): t
            case .visual(let spec): visual(spec)
            }
        }.filter { !$0.isEmpty }.joined(separator: "\n\n")
    }

    /// The prose alone, for plain-text sharing and for apps that cannot draw.
    public static func stripped(_ markdown: String) -> String { rendered(markdown) { _ in "" } }

    /// The document as saved: good fences kept as written, bad ones gone.
    public static func cleaned(_ markdown: String) -> String {
        rendered(markdown) { spec in
            guard let data = try? JSONEncoder().encode(spec) else { return "" }
            return fence + "\n" + String(decoding: data, as: UTF8.self) + "\n```"
        }
    }
}

/// A visual drawn inside a document: the slide at the column's width, 16:9, in the item's theme.
public struct VisualInlineView: View {
    public let spec: VisualSpec
    public init(spec: VisualSpec) { self.spec = spec }
    public var body: some View {
        VisualSceneView(scene: VisualLayout.scene(for: spec), theme: VisualTheme.named(spec.theme), hero: VisualHero.image(named: spec.hero))
            .aspectRatio(VisualScene.size.width / VisualScene.size.height, contentMode: .fit)
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(.quaternary, lineWidth: 1))
            .accessibilityLabel(spec.title)
    }
}

/// The files a visual is shared as, written on demand to a folder of their own in the temporary directory.
public enum VisualFiles {
    public enum Kind: String, CaseIterable, Sendable {
        case png, svg, pdf
        public var label: String { switch self { case .png: "Image (PNG)"; case .svg: "Vector (SVG)"; case .pdf: "PDF" } }
    }

    static func folder() -> URL {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("LocalBook Visuals", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    /// The Finder-safe file name for a title.
    static func name(_ title: String) -> String {
        let bad = CharacterSet(charactersIn: "/:\\?%*|\"<>")
        let s = title.components(separatedBy: bad).joined(separator: " ").trimmingCharacters(in: .whitespacesAndNewlines)
        return String(s.isEmpty ? "Visual" : s).prefix(80).description
    }

    /// Writes the picture as `kind` and returns the file, or nil when it could not be made.
    @MainActor public static func write(_ spec: VisualSpec, title: String, as kind: Kind) -> URL? {
        let url = folder().appendingPathComponent(name(title)).appendingPathExtension(kind.rawValue)
        switch kind {
        case .png:
            guard let data = VisualSlide.image(for: spec)?.platformPNGData else { return nil }
            try? data.write(to: url, options: .atomic)
        case .svg:
            try? Data(VisualSlide.svg(for: spec).utf8).write(to: url, options: .atomic)
        case .pdf:
            let renderer = ImageRenderer(content: VisualSlide(spec: spec).environment(\.colorScheme, .light))
            renderer.render { size, draw in
                var box = CGRect(origin: .zero, size: size)
                guard let consumer = CGDataConsumer(url: url as CFURL), let pdf = CGContext(consumer: consumer, mediaBox: &box, nil) else { return }
                pdf.beginPDFPage(nil)
                draw(pdf)
                pdf.endPDFPage()
                pdf.closePDF()
            }
        }
        return FileManager.default.fileExists(atPath: url.path) ? url : nil
    }
}
