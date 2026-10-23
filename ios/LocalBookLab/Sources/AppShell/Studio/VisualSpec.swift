import SwiftUI
import UIKit

/// A phone-made visual: the structure the answer model wrote (not a picture), drawn natively so it is crisp, follows
/// light and dark mode, and can be saved as an image.
struct VisualSpec: Codable, Hashable, Sendable {
    enum Layout: String, Codable, CaseIterable, Identifiable, Sendable {
        case mindmap, flowchart, comparison, timeline, infographic
        var id: String { rawValue }
        var label: String {
            switch self { case .mindmap: "Mind map"; case .flowchart: "Flowchart"; case .comparison: "Comparison"; case .timeline: "Timeline"; case .infographic: "Infographic" }
        }
        var symbol: String {
            switch self { case .mindmap: "point.3.connected.trianglepath.dotted"; case .flowchart: "arrow.triangle.branch"; case .comparison: "tablecells"; case .timeline: "clock.arrow.circlepath"; case .infographic: "chart.bar.doc.horizontal" }
        }
    }
    struct Item: Codable, Hashable, Sendable {
        var title: String
        var detail: String = ""
        var value: String = ""
        var children: [String] = []
    }
    struct Row: Codable, Hashable, Sendable { var label: String; var cells: [String] }

    var layout: Layout
    var title: String
    var items: [Item] = []          // branches, steps, events or key points
    var stats: [Item] = []          // infographic figures (value + title as the label)
    var columns: [String] = []      // comparison
    var rows: [Row] = []            // comparison

    /// Reads the model's JSON object leniently. nil when there is nothing usable to draw.
    static func parse(_ raw: String, layout: Layout) -> VisualSpec? {
        guard let o = StudioEngine.jsonObjects(in: raw).first else { return nil }
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
    enum Detail: String, CaseIterable, Identifiable, Sendable {
        case overview, standard, deep
        var id: String { rawValue }
        var label: String { switch self { case .overview: "Overview"; case .standard: "Standard"; case .deep: "Deep" } }
        /// Reply budget for the model.
        var tokens: Int { switch self { case .overview: 800; case .standard: 1_300; case .deep: 2_400 } }
        /// How many notebook passages to read.
        var passages: Int { switch self { case .overview: 8; case .standard: 14; case .deep: 24 } }
        fileprivate func pick(_ a: String, _ b: String, _ c: String) -> String { switch self { case .overview: a; case .standard: b; case .deep: c } }
    }

    /// The JSON shape each layout is asked for.
    static func request(_ layout: Layout, about: String, detail: Detail = .standard) -> String {
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

private struct BranchRef: Identifiable { let id: Int; let item: VisualSpec.Item }

private struct MindBounds: PreferenceKey {
    nonisolated(unsafe) static var defaultValue: [String: Anchor<CGRect>] = [:]
    static func reduce(value: inout [String: Anchor<CGRect>], nextValue: () -> [String: Anchor<CGRect>]) { value.merge(nextValue()) { $1 } }
}

private let palette: [Color] = [.blue, .orange, .green, .pink, .purple, .teal]

/// Draws a VisualSpec at phone width.
struct VisualCanvas: View {
    let spec: VisualSpec
    @State private var reading: BranchRef?

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            if spec.layout != .mindmap {              // a mind map carries its title in the middle
                Text(spec.title).font(.title2.bold()).frame(maxWidth: .infinity, alignment: .leading)
            }
            switch spec.layout {
            case .mindmap: mindmap
            case .flowchart: flowchart
            case .comparison: comparison
            case .timeline: timeline
            case .infographic: infographic
            }
        }
        .fixedSize(horizontal: false, vertical: true)      // text wraps; it is never squeezed to fit a height
        .padding(20)
        .background(Color(.systemBackground))
        .sheet(item: $reading) { ref in
            NavigationStack {
                List {
                    ForEach(Array(ref.item.children.enumerated()), id: \.offset) { _, child in Text(child).font(.body) }
                    if !ref.item.detail.isEmpty { Text(ref.item.detail) }
                }
                .navigationTitle(ref.item.title).navigationBarTitleDisplayMode(.inline)
                .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { reading = nil } } }
            }
            .presentationDetents([.medium, .large])
        }
    }

    /// A classic mind map: the topic in the middle, branches fanning out to both sides on curved lines.
    private var mindmap: some View {
        let left = Array(spec.items.enumerated()).filter { $0.offset % 2 == 1 }
        let right = Array(spec.items.enumerated()).filter { $0.offset % 2 == 0 }
        return HStack(alignment: .center, spacing: 0) {
            VStack(spacing: 14) { ForEach(left, id: \.offset) { branch($0.offset, $0.element, leading: false) } }.frame(maxWidth: .infinity)
            Text(spec.title).font(.subheadline.bold()).multilineTextAlignment(.center).foregroundStyle(.white)
                .padding(8).frame(width: 84, height: 84)
                .background(Circle().fill(LinearGradient(colors: [.indigo, .purple], startPoint: .topLeading, endPoint: .bottomTrailing)))
                .anchorPreference(key: MindBounds.self, value: .bounds) { ["center": $0] }
                .padding(.horizontal, 14)
            VStack(spacing: 14) { ForEach(right, id: \.offset) { branch($0.offset, $0.element, leading: true) } }.frame(maxWidth: .infinity)
        }
        .overlayPreferenceValue(MindBounds.self) { anchors in
            GeometryReader { geo in
                if let c = anchors["center"].map({ geo[$0] }) {
                    ForEach(spec.items.indices, id: \.self) { i in
                        if let a = anchors["b\(i)"] {
                            let b = geo[a]
                            let toRight = i % 2 == 0
                            let from = CGPoint(x: toRight ? c.maxX : c.minX, y: c.midY)
                            let to = CGPoint(x: toRight ? b.minX : b.maxX, y: b.midY)
                            Path { p in
                                p.move(to: from)
                                p.addCurve(to: to, control1: CGPoint(x: (from.x + to.x) / 2, y: from.y), control2: CGPoint(x: (from.x + to.x) / 2, y: to.y))
                            }
                            .stroke(palette[i % palette.count].opacity(0.7), style: StrokeStyle(lineWidth: 3, lineCap: .round))
                        }
                    }
                }
            }
        }
    }

    private func branch(_ i: Int, _ item: VisualSpec.Item, leading: Bool) -> some View {
        let c = palette[i % palette.count]
        return Button { reading = BranchRef(id: i, item: item) } label: {
            VStack(alignment: leading ? .leading : .trailing, spacing: 5) {
                HStack(spacing: 4) {
                    Text(item.title).font(.footnote.bold()).multilineTextAlignment(leading ? .leading : .trailing)
                    if !item.children.isEmpty { Image(systemName: "text.magnifyingglass").font(.caption2) }
                }
                .foregroundStyle(.white).padding(.horizontal, 10).padding(.vertical, 6).background(c, in: Capsule())
                .anchorPreference(key: MindBounds.self, value: .bounds) { ["b\(i)": $0] }
                ForEach(Array(item.children.prefix(3).enumerated()), id: \.offset) { _, child in
                    Text(child).font(.caption2).foregroundStyle(.secondary).lineLimit(2)
                        .multilineTextAlignment(leading ? .leading : .trailing)
                }
                if item.children.count > 3 { Text("+\(item.children.count - 3) more").font(.caption2).foregroundStyle(c) }
            }
            .frame(maxWidth: .infinity, alignment: leading ? .leading : .trailing)
        }
        .buttonStyle(.plain)
    }

    private var flowchart: some View {
        VStack(spacing: 0) {
            ForEach(spec.items.indices, id: \.self) { i in
                let c = palette[i % palette.count]
                if i > 0 { Image(systemName: "arrow.down").font(.callout.weight(.bold)).foregroundStyle(.secondary).padding(.vertical, 6) }
                HStack(alignment: .top, spacing: 12) {
                    Text("\(i + 1)").font(.headline).foregroundStyle(.white).frame(width: 30, height: 30).background(c, in: Circle())
                    VStack(alignment: .leading, spacing: 3) {
                        Text(spec.items[i].title).font(.headline)
                        if !spec.items[i].detail.isEmpty { Text(spec.items[i].detail).font(.subheadline).foregroundStyle(.secondary) }
                    }
                    Spacer(minLength: 0)
                }
                .padding(14).background(c.opacity(0.1), in: RoundedRectangle(cornerRadius: 16))
            }
        }
    }

    private var comparison: some View {
        VStack(spacing: 0) {
            HStack(alignment: .bottom, spacing: 8) {
                Color.clear.frame(width: 84, height: 1)
                ForEach(spec.columns.indices, id: \.self) { i in
                    Text(spec.columns[i]).font(.subheadline.bold()).foregroundStyle(palette[i % palette.count])
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
            }.padding(.bottom, 8)
            ForEach(spec.rows.indices, id: \.self) { r in
                Divider()
                HStack(alignment: .top, spacing: 8) {
                    Text(spec.rows[r].label).font(.footnote.weight(.semibold)).foregroundStyle(.secondary).frame(width: 84, alignment: .leading)
                    ForEach(spec.columns.indices, id: \.self) { i in
                        Text(i < spec.rows[r].cells.count ? spec.rows[r].cells[i] : "").font(.footnote)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }.padding(.vertical, 10)
            }
        }
    }

    private var timeline: some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(spec.items.indices, id: \.self) { i in
                let c = palette[i % palette.count]
                HStack(alignment: .top, spacing: 14) {
                    VStack(spacing: 0) {
                        Circle().fill(c).frame(width: 14, height: 14).padding(.top, 4)
                        if i < spec.items.count - 1 { Rectangle().fill(c.opacity(0.3)).frame(width: 2).frame(maxHeight: .infinity) }
                    }.frame(width: 14)
                    VStack(alignment: .leading, spacing: 3) {
                        if !spec.items[i].value.isEmpty { Text(spec.items[i].value).font(.caption.bold()).foregroundStyle(c) }
                        Text(spec.items[i].title).font(.headline)
                        if !spec.items[i].detail.isEmpty { Text(spec.items[i].detail).font(.subheadline).foregroundStyle(.secondary) }
                    }.padding(.bottom, 20)
                    Spacer(minLength: 0)
                }
            }
        }
    }

    private var infographic: some View {
        VStack(alignment: .leading, spacing: 18) {
            if !spec.stats.isEmpty {
                LazyVGrid(columns: [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)], spacing: 10) {
                    ForEach(spec.stats.indices, id: \.self) { i in
                        let c = palette[i % palette.count]
                        VStack(alignment: .leading, spacing: 4) {
                            Text(spec.stats[i].value).font(.system(.title, design: .rounded).bold()).foregroundStyle(c).minimumScaleFactor(0.5).lineLimit(2)
                            Text(spec.stats[i].title).font(.caption).foregroundStyle(.secondary)
                        }
                        .padding(14).frame(maxWidth: .infinity, minHeight: 84, alignment: .leading)
                        .background(c.opacity(0.12), in: RoundedRectangle(cornerRadius: 16))
                    }
                }
            }
            ForEach(spec.items.indices, id: \.self) { i in
                let c = palette[(i + 2) % palette.count]
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: "sparkle").foregroundStyle(c).frame(width: 24)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(spec.items[i].title).font(.headline)
                        if !spec.items[i].detail.isEmpty { Text(spec.items[i].detail).font(.subheadline).foregroundStyle(.secondary) }
                    }
                }
            }
        }
    }
}

private struct CanvasSize: PreferenceKey {
    nonisolated(unsafe) static var defaultValue = CGSize.zero
    static func reduce(value: inout CGSize, nextValue: () -> CGSize) { value = nextValue() }
}

/// The visual screen: the drawing with pinch to zoom, drag to move and double-tap to zoom in or fit, plus
/// save-as-image sharing.
struct VisualItemView: View {
    let spec: VisualSpec
    let title: String
    @State private var picture: UIImage?
    @State private var scale: CGFloat = 1
    @State private var committed: CGFloat = 1
    @State private var natural = CGSize.zero

    var body: some View {
        GeometryReader { geo in
            ScrollView([.horizontal, .vertical]) {
                VisualCanvas(spec: spec)
                    .frame(width: geo.size.width)
                    .background(GeometryReader { g in Color.clear.preference(key: CanvasSize.self, value: g.size) })
                    .scaleEffect(scale, anchor: .topLeading)
                    .frame(width: geo.size.width * scale, height: max(1, natural.height) * scale, alignment: .topLeading)
            }
            .onPreferenceChange(CanvasSize.self) { natural = $0 }
            .simultaneousGesture(
                MagnifyGesture()
                    .onChanged { scale = min(4, max(1, committed * $0.magnification)) }
                    .onEnded { _ in committed = scale }
            )
            .onTapGesture(count: 2) { withAnimation(.snappy) { scale = scale > 1.01 ? 1 : 2; committed = scale } }
        }
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                if let picture { ShareLink(item: Image(uiImage: picture), preview: SharePreview(title, image: Image(uiImage: picture))) }
            }
            ToolbarItem(placement: .topBarLeading) {
                if scale > 1.01 {
                    Button { withAnimation(.snappy) { scale = 1; committed = 1 } } label: { Image(systemName: "arrow.down.right.and.arrow.up.left") }
                        .accessibilityLabel("Fit to screen")
                }
            }
        }
        .task {
            let renderer = ImageRenderer(content: VisualCanvas(spec: spec).frame(width: 390).environment(\.colorScheme, .light))
            renderer.scale = 3
            picture = renderer.uiImage
        }
    }
}
