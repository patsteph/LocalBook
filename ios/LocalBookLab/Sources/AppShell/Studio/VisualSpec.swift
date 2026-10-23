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

    /// The JSON shape each layout is asked for.
    static func request(_ layout: Layout, about: String) -> String {
        switch layout {
        case .mindmap:
            return "Make a mind map of \(about). Reply with ONLY a JSON object: {\"title\": the central topic in at most 6 words, \"branches\": [5 or 6 items {\"title\": 1 to 4 words, \"children\": [2 to 4 short phrases of at most 8 words]}]}."
        case .flowchart:
            return "Make a flowchart of the main process or sequence in \(about). Reply with ONLY a JSON object: {\"title\": at most 6 words, \"steps\": [5 to 8 items {\"title\": at most 6 words, \"detail\": one short sentence}]}."
        case .comparison:
            return "Make a comparison table for \(about). Reply with ONLY a JSON object: {\"title\": at most 6 words, \"columns\": [2 or 3 names of the things compared], \"rows\": [4 to 6 items {\"label\": the aspect compared, \"cells\": [one short phrase per column, in column order]}]}."
        case .timeline:
            return "Make a timeline for \(about). Reply with ONLY a JSON object: {\"title\": at most 6 words, \"events\": [5 to 8 items in order {\"when\": the date or stage, \"title\": at most 6 words, \"detail\": one short sentence}]}."
        case .infographic:
            return "Make an infographic for \(about). Reply with ONLY a JSON object: {\"title\": a punchy headline of at most 8 words, \"stats\": [3 or 4 items {\"value\": a figure, number or short fact taken from the passages, \"label\": at most 5 words}], \"points\": [3 to 5 items {\"title\": at most 5 words, \"detail\": one short sentence}]}."
        }
    }
}

private let palette: [Color] = [.blue, .orange, .green, .pink, .purple, .teal]

/// Draws a VisualSpec at phone width.
struct VisualCanvas: View {
    let spec: VisualSpec

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text(spec.title).font(.title2.bold()).frame(maxWidth: .infinity, alignment: spec.layout == .mindmap ? .center : .leading)
                .multilineTextAlignment(spec.layout == .mindmap ? .center : .leading)
            switch spec.layout {
            case .mindmap: mindmap
            case .flowchart: flowchart
            case .comparison: comparison
            case .timeline: timeline
            case .infographic: infographic
            }
        }
        .padding(20)
        .background(Color(.systemBackground))
    }

    private var mindmap: some View {
        VStack(spacing: 0) {
            ForEach(spec.items.indices, id: \.self) { i in
                let c = palette[i % palette.count]
                VStack(spacing: 0) {
                    Rectangle().fill(c.opacity(0.4)).frame(width: 2, height: 14)
                    VStack(alignment: .leading, spacing: 8) {
                        Text(spec.items[i].title).font(.headline).foregroundStyle(c)
                        ForEach(spec.items[i].children, id: \.self) { child in
                            HStack(alignment: .firstTextBaseline, spacing: 8) {
                                Circle().fill(c).frame(width: 6, height: 6)
                                Text(child).font(.subheadline)
                            }
                        }
                    }
                    .padding(14).frame(maxWidth: .infinity, alignment: .leading)
                    .background(c.opacity(0.12), in: RoundedRectangle(cornerRadius: 16))
                    .overlay(RoundedRectangle(cornerRadius: 16).stroke(c.opacity(0.35), lineWidth: 1))
                }
            }
        }
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

/// The visual screen: the drawing, with save-as-image sharing.
struct VisualItemView: View {
    let spec: VisualSpec
    let title: String
    @State private var picture: UIImage?

    var body: some View {
        ScrollView { VisualCanvas(spec: spec) }
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    if let picture { ShareLink(item: Image(uiImage: picture), preview: SharePreview(title, image: Image(uiImage: picture))) }
                }
            }
            .task {
                let renderer = ImageRenderer(content: VisualCanvas(spec: spec).frame(width: 390).environment(\.colorScheme, .light))
                renderer.scale = 3
                picture = renderer.uiImage
            }
    }
}
