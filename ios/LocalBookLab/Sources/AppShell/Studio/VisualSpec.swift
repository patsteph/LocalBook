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


private struct MindBounds: PreferenceKey {
    nonisolated(unsafe) static var defaultValue: [String: Anchor<CGRect>] = [:]
    static func reduce(value: inout [String: Anchor<CGRect>], nextValue: () -> [String: Anchor<CGRect>]) { value.merge(nextValue()) { $1 } }
}

private let palette: [Color] = [.blue, .orange, .green, .pink, .purple, .teal]

/// Draws a VisualSpec at phone width.
struct VisualCanvas: View {
    let spec: VisualSpec
    /// True when drawn on the 16:9 slide: steps, events and figures run across the page instead of down it.
    var slide = false
    /// Mind-map branches opened in place (tap a branch to show all of it, tap again to fold it).
    var expanded: Binding<Set<Int>> = .constant([])

    /// Indices 0..<count in rows of at most `per`, as even as possible.
    private func rowsOf(_ count: Int, maxPerRow: Int) -> [[Int]] {
        guard count > 0 else { return [] }
        let rowCount = Int((Double(count) / Double(maxPerRow)).rounded(.up))
        let per = Int((Double(count) / Double(rowCount)).rounded(.up))
        return stride(from: 0, to: count, by: per).map { Array($0..<min($0 + per, count)) }
    }

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
        .padding(slide ? 28 : 20)
        .background(Color(.systemBackground))
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
        let open = expanded.wrappedValue.contains(i)
        let hidden = max(0, item.children.count - 3)
        let canOpen = hidden > 0 || !item.detail.isEmpty || item.children.contains { $0.count > 60 }
        return Button {
            guard canOpen else { return }
            withAnimation(.snappy) {
                if open { expanded.wrappedValue.remove(i) } else { expanded.wrappedValue.insert(i) }
            }
        } label: {
            VStack(alignment: leading ? .leading : .trailing, spacing: 5) {
                HStack(spacing: 4) {
                    Text(item.title).font(.footnote.bold()).multilineTextAlignment(leading ? .leading : .trailing)
                    if canOpen { Image(systemName: open ? "chevron.up" : "chevron.down").font(.caption2.weight(.bold)) }
                }
                .foregroundStyle(.white).padding(.horizontal, 10).padding(.vertical, 6).background(c, in: Capsule())
                .anchorPreference(key: MindBounds.self, value: .bounds) { ["b\(i)": $0] }
                ForEach(Array((open ? item.children : Array(item.children.prefix(3))).enumerated()), id: \.offset) { _, child in
                    Text(child).font(.caption2).foregroundStyle(open ? .primary : .secondary).lineLimit(open ? nil : 2)
                        .multilineTextAlignment(leading ? .leading : .trailing)
                }
                if open, !item.detail.isEmpty {
                    Text(item.detail).font(.caption2).foregroundStyle(.secondary).multilineTextAlignment(leading ? .leading : .trailing)
                }
                if !open, hidden > 0 { Text("+\(hidden) more").font(.caption2.weight(.semibold)).foregroundStyle(c) }
            }
            .frame(maxWidth: .infinity, alignment: leading ? .leading : .trailing)
        }
        .buttonStyle(.plain)
        .accessibilityHint(canOpen ? (open ? "Shows less" : "Shows all of this branch") : "")
    }

    @ViewBuilder private var flowchart: some View {
        if slide { flowchartAcross } else { flowchartDown }
    }

    private var flowchartAcross: some View {
        let rows = rowsOf(spec.items.count, maxPerRow: 4)
        let per = rows.first?.count ?? 1
        return VStack(spacing: 16) {
            ForEach(rows.indices, id: \.self) { r in
                HStack(alignment: .top, spacing: 6) {
                    ForEach(rows[r], id: \.self) { i in
                        let c = palette[i % palette.count]
                        if i != rows[r].first { Image(systemName: "arrow.right").font(.callout.weight(.bold)).foregroundStyle(.secondary).padding(.top, 22) }
                        VStack(alignment: .leading, spacing: 6) {
                            Text("\(i + 1)").font(.headline).foregroundStyle(.white).frame(width: 30, height: 30).background(c, in: Circle())
                            Text(spec.items[i].title).font(.headline)
                            if !spec.items[i].detail.isEmpty { Text(spec.items[i].detail).font(.subheadline).foregroundStyle(.secondary) }
                        }
                        .padding(14).frame(maxWidth: .infinity, alignment: .topLeading)
                        .background(c.opacity(0.1), in: RoundedRectangle(cornerRadius: 16))
                    }
                    ForEach(0..<max(0, per - rows[r].count), id: \.self) { _ in Color.clear.frame(maxWidth: .infinity) }
                }
            }
        }
    }

    private var flowchartDown: some View {
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

    @ViewBuilder private var timeline: some View {
        if slide { timelineAcross } else { timelineDown }
    }

    private var timelineAcross: some View {
        let rows = rowsOf(spec.items.count, maxPerRow: 5)
        let per = rows.first?.count ?? 1
        return VStack(spacing: 22) {
            ForEach(rows.indices, id: \.self) { r in
                HStack(alignment: .top, spacing: 14) {
                    ForEach(rows[r], id: \.self) { i in
                        let c = palette[i % palette.count]
                        VStack(alignment: .leading, spacing: 5) {
                            ZStack(alignment: .leading) {
                                Rectangle().fill(c.opacity(0.3)).frame(height: 2)
                                Circle().fill(c).frame(width: 14, height: 14)
                            }
                            if !spec.items[i].value.isEmpty { Text(spec.items[i].value).font(.caption.bold()).foregroundStyle(c) }
                            Text(spec.items[i].title).font(.headline)
                            if !spec.items[i].detail.isEmpty { Text(spec.items[i].detail).font(.subheadline).foregroundStyle(.secondary) }
                        }
                        .frame(maxWidth: .infinity, alignment: .topLeading)
                    }
                    ForEach(0..<max(0, per - rows[r].count), id: \.self) { _ in Color.clear.frame(maxWidth: .infinity) }
                }
            }
        }
    }

    private var timelineDown: some View {
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
                LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 10), count: slide ? min(4, max(2, spec.stats.count)) : 2), spacing: 10) {
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
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 16, alignment: .topLeading), count: slide ? 2 : 1), alignment: .leading, spacing: 12) {
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
}

/// Every visual is one 16:9 slide: drawn 800×450 and saved at 2560×1440 (2K). A visual with a lot on it is laid out wider
/// and shrunk to fit, so the shape never changes. On the phone it is scaled to the screen and can be pinched to zoom.
struct VisualSlide: View {
    static let size = CGSize(width: 800, height: 450)
    static let exportWidth: CGFloat = 2560
    let spec: VisualSpec
    var fit: CGFloat = 1
    var expanded: Binding<Set<Int>> = .constant([])

    var body: some View {
        VisualCanvas(spec: spec, slide: true, expanded: expanded)
            .frame(width: Self.size.width / fit)
            .scaleEffect(fit)
            .frame(width: Self.size.width, height: Self.size.height)
            .background(Color(.systemBackground))
            .clipped()
    }

    /// How much to shrink the drawing so it fits the slide's height (1 when it already does).
    @MainActor static func fit(for spec: VisualSpec, expanded: Set<Int> = []) -> CGFloat {
        func height(at width: CGFloat) -> CGFloat {
            let r = ImageRenderer(content: VisualCanvas(spec: spec, slide: true, expanded: .constant(expanded)).frame(width: width).environment(\.colorScheme, .light))
            r.scale = 1
            return r.uiImage?.size.height ?? size.height
        }
        var s: CGFloat = 1
        for _ in 0..<3 {                                    // wider means fewer wrapped lines, so measure again after each shrink
            let shown = height(at: size.width / s) * s
            if shown <= size.height { break }
            s = max(0.3, s * size.height / shown)
        }
        return s
    }

    @MainActor static func image(for spec: VisualSpec, fit: CGFloat) -> UIImage? {
        let r = ImageRenderer(content: VisualSlide(spec: spec, fit: fit).environment(\.colorScheme, .light))
        r.scale = exportWidth / size.width
        return r.uiImage
    }
}

/// The visual screen: the 16:9 slide fitted to the screen, with pinch to zoom, drag to move and double-tap to zoom in or
/// fit, plus save-as-image sharing (2K).
struct VisualItemView: View {
    let spec: VisualSpec
    let title: String
    @State private var picture: UIImage?
    @State private var scale: CGFloat = 1
    @State private var committed: CGFloat = 1
    @State private var fit: CGFloat = 1
    @State private var expanded: Set<Int> = []

    var body: some View {
        GeometryReader { geo in
            let base = min(geo.size.width / VisualSlide.size.width, geo.size.height / VisualSlide.size.height)
            let w = VisualSlide.size.width * base * scale, h = VisualSlide.size.height * base * scale
            ScrollView([.horizontal, .vertical]) {
                VisualSlide(spec: spec, fit: fit, expanded: $expanded)
                    .scaleEffect(base * scale, anchor: .topLeading)
                    .frame(width: w, height: h, alignment: .topLeading)
                    .frame(minWidth: geo.size.width, minHeight: geo.size.height)       // centred when smaller than the screen
            }
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
            fit = VisualSlide.fit(for: spec)
            picture = VisualSlide.image(for: spec, fit: fit)
        }
        .onChange(of: expanded) { _, now in                     // open branches make the slide fuller: shrink it to stay 16:9
            withAnimation(.snappy) { fit = VisualSlide.fit(for: spec, expanded: now) }
        }
    }
}
