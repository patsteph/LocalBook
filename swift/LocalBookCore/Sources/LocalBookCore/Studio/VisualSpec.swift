import SwiftUI

/// A visual: the structure the answer model wrote (not a picture). `VisualLayout` places it as a `VisualScene`, which
/// `VisualSceneView` draws natively (crisp, light and dark), `VisualSlide.image` saves as a PNG and `VisualSVG` writes as
/// a vector file.
public struct VisualSpec: Codable, Hashable, Sendable {
    public enum Layout: String, Codable, CaseIterable, Identifiable, Sendable {
        case mindmap, flowchart, comparison, timeline, infographic
        public var id: String { rawValue }
        public var label: String {
            switch self { case .mindmap: "Mind map"; case .flowchart: "Flowchart"; case .comparison: "Comparison"; case .timeline: "Timeline"; case .infographic: "Infographic" }
        }
        public var symbol: String {
            switch self { case .mindmap: "point.3.connected.trianglepath.dotted"; case .flowchart: "arrow.triangle.branch"; case .comparison: "tablecells"; case .timeline: "clock.arrow.circlepath"; case .infographic: "chart.bar.doc.horizontal" }
        }
    }
    public struct Item: Codable, Hashable, Sendable {
        public var title: String
        public var detail: String = ""
        public var value: String = ""
        public var children: [String] = []
    }
    public struct Row: Codable, Hashable, Sendable { public var label: String; public var cells: [String] }

    public var layout: Layout
    public var title: String
    public var items: [Item] = []          // branches, steps, events or key points
    public var stats: [Item] = []          // infographic figures (value + title as the label)
    public var columns: [String] = []      // comparison
    public var rows: [Row] = []            // comparison

    /// Reads the model's JSON object leniently. nil when there is nothing usable to draw.
    public static func parse(_ raw: String, layout: Layout) -> VisualSpec? {
        guard let o = JSONObjects.scan(in: raw).first else { return nil }
        func text(_ v: Any?) -> String { (v as? String ?? (v as? NSNumber).map { "\($0)" } ?? "").trimmingCharacters(in: .whitespacesAndNewlines) }
        func objects(_ key: String) -> [[String: Any]] { o[key] as? [[String: Any]] ?? [] }
        var spec = VisualSpec(layout: layout, title: text(o["title"]))
        switch layout {
        case .mindmap:
            spec.items = objects("branches").map { b in Item(title: text(b["title"]), children: (b["children"] as? [Any] ?? []).map(text).filter { !$0.isEmpty }) }
        case .flowchart:
            spec.items = objects("steps").map { Item(title: text($0["title"]), detail: text($0["detail"])) }
        case .timeline:
            spec.items = objects("events").map { Item(title: text($0["title"]), detail: text($0["detail"]), value: text($0["when"])) }
        case .infographic:
            spec.stats = objects("stats").map { Item(title: text($0["label"]), value: text($0["value"])) }
            spec.items = objects("points").map { Item(title: text($0["title"]), detail: text($0["detail"])) }
        case .comparison:
            spec.columns = (o["columns"] as? [Any] ?? []).map(text).filter { !$0.isEmpty }
            spec.rows = objects("rows").map { Row(label: text($0["label"]), cells: ($0["cells"] as? [Any] ?? []).map(text)) }
        }
        spec.items.removeAll { $0.title.isEmpty }; spec.stats.removeAll { $0.value.isEmpty }
        spec.rows.removeAll { $0.label.isEmpty }
        let usable = layout == .comparison ? (!spec.rows.isEmpty && !spec.columns.isEmpty) : (!spec.items.isEmpty || !spec.stats.isEmpty)
        return usable ? spec : nil
    }

    /// How much each visual carries. Standard is the default; Deep is for when a summary isn't enough.
    public enum Detail: String, CaseIterable, Identifiable, Sendable {
        case overview, standard, deep
        public var id: String { rawValue }
        public var label: String { switch self { case .overview: "Overview"; case .standard: "Standard"; case .deep: "Deep" } }
        /// Reply budget for the model.
        public var tokens: Int { switch self { case .overview: 800; case .standard: 1_300; case .deep: 2_400 } }
        /// How many notebook passages to read.
        public var passages: Int { switch self { case .overview: 8; case .standard: 14; case .deep: 24 } }
        fileprivate func pick(_ a: String, _ b: String, _ c: String) -> String { switch self { case .overview: a; case .standard: b; case .deep: c } }
    }

    /// The JSON shape each layout is asked for.
    public static func request(_ layout: Layout, about: String, detail: Detail = .standard) -> String {
        let d = detail
        switch layout {
        case .mindmap:
            let n = d.pick("4 or 5", "5 or 6", "7 or 8"), k = d.pick("1 or 2", "2 to 4", "4 to 6"), w = d.pick("6", "8", "12")
            return "Make a mind map of \(about). Reply with ONLY a JSON object: {\"title\": the central topic in at most 6 words, \"branches\": [\(n) items {\"title\": 1 to 4 words, \"children\": [\(k) short phrases of at most \(w) words]}]}."
        case .flowchart:
            let n = d.pick("4 or 5", "5 to 8", "8 to 12"), s = d.pick("a few words", "one short sentence", "one or two sentences")
            return "Make a flowchart of the main process or sequence in \(about). Reply with ONLY a JSON object: {\"title\": at most 6 words, \"steps\": [\(n) items {\"title\": at most 6 words, \"detail\": \(s)}]}."
        case .comparison:
            let n = d.pick("3 or 4", "4 to 6", "7 to 10")
            return "Make a comparison table for \(about). Reply with ONLY a JSON object: {\"title\": at most 6 words, \"columns\": [2 or 3 names of the things compared], \"rows\": [\(n) items {\"label\": the aspect compared, \"cells\": [one short phrase per column, in column order]}]}."
        case .timeline:
            let n = d.pick("4 or 5", "5 to 8", "8 to 12"), s = d.pick("a few words", "one short sentence", "one or two sentences")
            return "Make a timeline for \(about). Reply with ONLY a JSON object: {\"title\": at most 6 words, \"events\": [\(n) items in order {\"when\": the date or stage, \"title\": at most 6 words, \"detail\": \(s)}]}."
        case .infographic:
            let st = d.pick("3", "3 or 4", "4 to 6"), p = d.pick("2 or 3", "3 to 5", "5 to 8"), s = d.pick("a few words", "one short sentence", "one or two sentences")
            return "Make an infographic for \(about). Reply with ONLY a JSON object: {\"title\": a punchy headline of at most 8 words, \"stats\": [\(st) items {\"value\": a figure, number or short fact taken from the passages, \"label\": at most 5 words}], \"points\": [\(p) items {\"title\": at most 5 words, \"detail\": \(s)}]}."
        }
    }
}


/// Draws a scene with SwiftUI's `Canvas`, scaled to whatever frame it is given, in the theme's colours for the current
/// appearance. Cards that carry a `hit` get an invisible button over them (mind-map branches open in place).
public struct VisualSceneView: View {
    public let scene: VisualScene
    public var theme: VisualTheme = .clean
    public var hit: ((Int) -> Void)? = nil
    @Environment(\.colorScheme) private var colorScheme

    public init(scene: VisualScene, theme: VisualTheme = .clean, hit: ((Int) -> Void)? = nil) {
        self.scene = scene; self.theme = theme; self.hit = hit
    }

    private func color(_ role: VisualRole) -> Color {
        let c = theme.color(role, dark: colorScheme == .dark)
        return Color(red: c.r, green: c.g, blue: c.b, opacity: c.a)
    }

    private static func font(_ style: VisualTextStyle) -> Font {
        let w: Font.Weight = switch style.weight { case .regular: .regular; case .semibold: .semibold; case .bold: .bold }
        return .system(size: style.size, weight: w, design: style.rounded ? .rounded : .default)
    }

    public var body: some View {
        GeometryReader { geo in
            let k = min(geo.size.width / scene.size.width, geo.size.height / scene.size.height)
            Canvas(rendersAsynchronously: false) { ctx, _ in
                ctx.scaleBy(x: k, y: k)
                ctx.fill(Path(CGRect(origin: .zero, size: scene.size)), with: .color(color(.background)))
                for node in scene.nodes {
                    switch node {
                    case .card(let r, let fill, let stroke, let radius, _):
                        let p = Path(roundedRect: r, cornerRadius: radius)
                        ctx.fill(p, with: .color(color(fill)))
                        if let stroke { ctx.stroke(p, with: .color(color(stroke)), lineWidth: 2) }
                    case .text(let t):
                        let font = Self.font(t.style)
                        for (i, line) in t.lines.enumerated() {
                            let y = t.frame.minY + CGFloat(i) * t.lineHeight
                            let text = Text(line).font(font).foregroundStyle(color(t.style.role))
                            switch t.style.align {
                            case .leading: ctx.draw(text, at: CGPoint(x: t.frame.minX, y: y), anchor: .topLeading)
                            case .center: ctx.draw(text, at: CGPoint(x: t.frame.midX, y: y), anchor: .top)
                            case .trailing: ctx.draw(text, at: CGPoint(x: t.frame.maxX, y: y), anchor: .topTrailing)
                            }
                        }
                    case .stroke(let segments, let role, let width, let dashed):
                        var p = Path()
                        for seg in segments {
                            switch seg {
                            case .move(let pt): p.move(to: pt)
                            case .line(let pt): p.addLine(to: pt)
                            case .curve(let to, let c1, let c2): p.addCurve(to: to, control1: c1, control2: c2)
                            }
                        }
                        ctx.stroke(p, with: .color(color(role)), style: StrokeStyle(lineWidth: width, lineCap: .round, lineJoin: .round, dash: dashed ? [6, 6] : []))
                    case .fill(let points, let role):
                        var p = Path()
                        if let first = points.first { p.move(to: first); for pt in points.dropFirst() { p.addLine(to: pt) }; p.closeSubpath() }
                        ctx.fill(p, with: .color(color(role)))
                    case .dot(let centre, let radius, let fill, let stroke):
                        let p = Path(ellipseIn: CGRect(x: centre.x - radius, y: centre.y - radius, width: radius * 2, height: radius * 2))
                        ctx.fill(p, with: .color(color(fill)))
                        if let stroke { ctx.stroke(p, with: .color(color(stroke)), lineWidth: 2) }
                    case .symbol(let name, let r, let role):
                        var image = ctx.resolve(Image(systemName: name))
                        image.shading = .color(color(role))
                        ctx.draw(image, in: r)
                    }
                }
            }
            .overlay(alignment: .topLeading) {
                if let hit {
                    ForEach(Array(scene.nodes.enumerated()), id: \.offset) { _, node in
                        if case .card(let r, _, _, _, let tag) = node, let tag {
                            Color.clear.contentShape(Rectangle())
                                .frame(width: r.width * k, height: r.height * k)
                                .offset(x: r.minX * k, y: r.minY * k)
                                .onTapGesture { hit(tag) }
                                .accessibilityLabel("Branch \(tag + 1)")
                                .accessibilityHint("Shows all of this branch, or folds it")
                        }
                    }
                }
            }
        }
        .accessibilityLabel(scene.title)
    }
}

/// Every visual is one 16:9 slide: shown at 800×450 and saved at 2560×1440 (2K). The layout fits itself to the
/// frame (shrinking the type in steps when it must), so the shape never changes. On the phone it is scaled to the
/// screen and can be pinched to zoom. `fit` is kept for callers from the old slide and is always 1 now.
public struct VisualSlide: View {
    public static let size = CGSize(width: 800, height: 450)
    public static let exportWidth: CGFloat = 2560
    public let spec: VisualSpec
    public var fit: CGFloat = 1
    public var expanded: Binding<Set<Int>> = .constant([])

    public init(spec: VisualSpec, fit: CGFloat = 1, expanded: Binding<Set<Int>> = .constant([])) {
        self.spec = spec; self.fit = fit; self.expanded = expanded
    }

    public var body: some View {
        VisualSceneView(scene: VisualLayout.scene(for: spec, expanded: expanded.wrappedValue)) { i in
            withAnimation(.snappy) {
                if expanded.wrappedValue.contains(i) { expanded.wrappedValue.remove(i) } else { expanded.wrappedValue.insert(i) }
            }
        }
        .frame(width: Self.size.width, height: Self.size.height)
    }

    /// The layout fits the slide itself; 1, kept so the apps' views need no change.
    @MainActor public static func fit(for spec: VisualSpec, expanded: Set<Int> = []) -> CGFloat { 1 }

    @MainActor public static func image(for spec: VisualSpec, fit: CGFloat = 1) -> PlatformImage? {
        let r = ImageRenderer(content: VisualSlide(spec: spec).environment(\.colorScheme, .light))
        r.scale = exportWidth / size.width
        return r.platformImage
    }

    /// The vector file of the same picture, in light appearance.
    public static func svg(for spec: VisualSpec, expanded: Set<Int> = []) -> String {
        VisualSVG.render(VisualLayout.scene(for: spec, expanded: expanded))
    }
}
