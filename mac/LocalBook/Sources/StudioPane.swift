import AppKit
import AVFoundation
import AVKit
import LocalBookCore
import LocalBookEngines
import SwiftUI
import WebKit

extension StudioItem.Kind {
    var blurb: String {
        switch self {
        case .doc: "A written summary"
        case .quiz: "Test yourself"
        case .cards: "Flip and learn"
        case .audio: "Two hosts talk it through"
        case .visual: "Maps, flows and infographics"
        case .video: "A narrated explainer"
        }
    }
    var tint: Color {
        switch self { case .doc: .blue; case .quiz: .orange; case .cards: .green; case .audio: .pink; case .visual: .purple; case .video: .indigo }
    }
}

/// Studio: briefings, quizzes, flashcards, audio overviews, visuals and video explainers made on this Mac from one
/// notebook, on the phone's engine (`StudioEngine`). The shelf on the left, the open item on the right; the six kinds
/// are a row of buttons above the shelf and the Studio menu (⌃⌘1 to ⌃⌘5). Items made here are sent to the paired
/// Mac the way the phone's are, so they reach its Canvas.
struct StudioPane: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openWindow) private var openWindow
    @State private var engine = StudioEngine()
    @State private var items: [StudioItem] = []
    @State private var notebookId: String?
    @State private var making: StudioItem.Kind?
    @State private var selected: String?
    @State private var pendingOpen: StudioItem?
    @State private var confirmClear = false
    @State private var renaming: StudioItem?
    @State private var newName = ""
    @State private var prefill = ""
    @State private var sent: Set<String> = StudioSync.sentIds()

    private var library: LibraryModel { model.library }
    private var notebook: LibraryNotebookInfo? { library.notebooks.first { $0.id == notebookId } }
    private var opened: StudioItem? { items.first { $0.id == selected } }

    var body: some View {
        HSplitView {
            shelf.frame(minWidth: 280, idealWidth: 340, maxWidth: 480)
            detail.frame(minWidth: 400, maxWidth: .infinity, maxHeight: .infinity)
        }
        .onAppear { if notebookId == nil || notebook == nil { notebookId = library.scopeToNotebook ?? library.notebooks.first?.id } }
        .onChange(of: library.scopeToNotebook) { _, id in if let id { notebookId = id } }
        .onChange(of: notebookId) { _, _ in reload() }
        .onChange(of: model.makeRequest) { _, kind in
            guard let kind else { return }
            model.makeRequest = nil
            prefill = model.makeSubject; model.makeSubject = ""
            if notebook != nil { making = kind }
        }
        .task { reload(); await send(); _ = await StudioPull.pull(); reload() }
        .onReceive(NotificationCenter.default.publisher(for: .librarySynced)) { _ in Task { _ = await StudioPull.pull(); reload() } }
        .onReceive(NotificationCenter.default.publisher(for: .detachItem)) { _ in
            if let item = opened { openWindow(id: "studio", value: StudioWindowKey(notebookId: item.notebookId, itemId: item.id, title: item.title)) }
        }
        .confirmationDialog("Delete all \(items.count) items made from this notebook?", isPresented: $confirmClear, titleVisibility: .visible) {
            Button("Delete all", role: .destructive) {
                for item in items { StudioSync.delete(item) }
                reload(); Task { await send() }
            }
        } message: { Text("This also removes them from your paired Mac.") }
        .alert("Rename", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
            TextField("Title", text: $newName)
            Button("Save") {
                let name = newName.trimmingCharacters(in: .whitespacesAndNewlines)
                if var item = renaming, !name.isEmpty { item.title = name; StudioStore.save(item); reload() }
                renaming = nil
            }
            Button("Cancel", role: .cancel) { renaming = nil }
        } message: { Text("Renaming changes the title on this Mac only.") }
        .sheet(item: $making, onDismiss: { prefill = ""; if let p = pendingOpen { pendingOpen = nil; selected = p.id } }) { kind in
            if let notebook {
                MakeSheet(kind: kind, notebook: notebook, library: library, engine: engine, topic: prefill) { made in
                    reload(); pendingOpen = made
                    Task { await send() }
                }
            }
        }
    }

    // MARK: shelf

    private var shelf: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 10) {
                Picker("Notebook", selection: $notebookId) {
                    ForEach(library.notebooks) { Text($0.title).tag(Optional($0.id)) }
                }
                .labelsHidden()
                LazyVGrid(columns: [GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8), GridItem(.flexible(), spacing: 8)], spacing: 8) {
                    ForEach(StudioItem.Kind.allCases) { kind in
                        Button { making = kind } label: {
                            VStack(spacing: 6) {
                                Image(systemName: kind.symbol).font(.title3).foregroundStyle(kind.tint)
                                Text(kind.short).font(.caption.weight(.medium)).foregroundStyle(.primary).lineLimit(1)
                            }
                            .frame(maxWidth: .infinity).padding(.vertical, 10)
                            .background(kind.tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 10))
                        }
                        .buttonStyle(.plain).disabled(notebook == nil || engine.working)
                        .help(Shortcuts.make(kind).map { $0.help("\(kind.label): \(kind.blurb.lowercased())") } ?? "\(kind.label): \(kind.blurb.lowercased())")
                    }
                }
            }
            .padding(12)
            Divider()
            List(selection: $selected) {
                Section("Made from this notebook") {
                    if items.isEmpty {
                        if let why = StudioPull.lastProblem {
                            Label("The Mac's Studio items could not be fetched: \(why)", systemImage: "exclamationmark.triangle")
                                .font(.callout).foregroundStyle(.orange)
                        } else {
                            Text("Nothing yet. Pick a kind above.").font(.callout).foregroundStyle(.tertiary)
                        }
                    }
                    ForEach(items) { item in
                        HStack(spacing: 10) {
                            Image(systemName: item.kind.symbol).foregroundStyle(item.kind.tint).frame(width: 22)
                            VStack(alignment: .leading, spacing: 1) {
                                Text(item.title).lineLimit(1)
                                Text(item.created.formatted(date: .abbreviated, time: .shortened) + shelfNote(item))
                                    .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                            }
                        }
                        .tag(item.id)
                        .contextMenu {
                            StudioShareButton(item: item)
                            Button { openWindow(id: "studio", value: StudioWindowKey(notebookId: item.notebookId, itemId: item.id, title: item.title)) } label: { Label("Open in Window", systemImage: "macwindow") }
                            Button { newName = item.title; renaming = item } label: { Label("Rename", systemImage: "pencil") }
                            if item.kind != .video {
                                Button { prefill = Self.subject(of: item); making = item.kind } label: { Label("Make Another Version", systemImage: "arrow.triangle.2.circlepath") }
                            }
                            Divider()
                            Button(role: .destructive) { remove(item) } label: { Label("Delete", systemImage: "trash") }
                        }
                    }
                }
            }
            .listStyle(.inset)
            .onDeleteCommand { if let item = opened { remove(item) } }
            if !items.isEmpty {
                Divider()
                HStack {
                    Spacer()
                    Button("Delete All…", role: .destructive) { confirmClear = true }.buttonStyle(.borderless).font(.callout)
                }
                .padding(8)
            }
        }
    }

    @ViewBuilder private var detail: some View {
        if let item = opened {
            StudioItemDetail(item: item).id(item.id)
        } else {
            ContentUnavailableView {
                Label("Studio", systemImage: "sparkles")
            } description: {
                Text(notebook == nil ? "Choose a notebook." : "Pick a kind above to make something from \(notebook!.title), or choose an item on the shelf.")
            }
        }
    }

    // MARK: helpers

    /// The subject an item was made about: its title without the kind prefix.
    private static func subject(of item: StudioItem) -> String {
        for prefix in ["Quiz: ", "Flashcards: ", "Audio overview: ", "Video: ", "Visual: "] where item.title.hasPrefix(prefix) { return String(item.title.dropFirst(prefix.count)) }
        return item.title
    }

    private func shelfNote(_ item: StudioItem) -> String {
        if item.origin == "mac" {
            guard let media = item.mediaFile else { return " · from your paired Mac" }
            if FileManager.default.fileExists(atPath: StudioStore.audioURL(media).path) { return " · from your paired Mac" }
            let size = item.mediaBytes.map { " (" + ByteCountFormatter.string(fromByteCount: Int64($0), countStyle: .file) + ")" } ?? ""
            return " · from your paired Mac, open to download" + size
        }
        if item.kind == .visual || item.kind == .video { return " · on this Mac" }
        return sent.contains(item.id) ? " · on your paired Mac" : " · waiting to send"
    }

    private func remove(_ item: StudioItem) {
        if selected == item.id { selected = nil }
        StudioSync.delete(item); reload(); Task { await send() }
    }

    private func reload() { items = StudioStore.all(notebook: notebookId) }

    private func send() async {
        _ = await StudioSync.flush()
        sent = StudioSync.sentIds()
    }
}

/// The setup sheet for one kind: only the options that matter, then Make. While making, it shows the animated screen.
private struct MakeSheet: View {
    let kind: StudioItem.Kind
    let notebook: LibraryNotebookInfo
    let library: LibraryModel
    let engine: StudioEngine
    let onMade: (StudioItem) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var topic = ""
    @State private var count: Int
    @State private var style: AudioStyle = .casual
    @State private var videoStyle: VideoStyle = .clean
    @State private var accent: AudioAccent = .british
    @State private var notebookOnly = true
    @State private var layout: VisualSpec.Layout? = nil           // nil: the engine chooses the form
    @State private var detail: VisualSpec.Detail = .standard
    @AppStorage("studio.visualTheme") private var theme = VisualTheme.clean.id

    init(kind: StudioItem.Kind, notebook: LibraryNotebookInfo, library: LibraryModel, engine: StudioEngine, topic: String = "", onMade: @escaping (StudioItem) -> Void) {
        _topic = State(initialValue: topic)
        self.kind = kind; self.notebook = notebook; self.library = library; self.engine = engine; self.onMade = onMade
        _count = State(initialValue: kind == .quiz ? 5 : (kind == .audio ? 4 : (kind == .video ? 6 : 10)))
    }

    private var counts: [Int] { kind == .quiz ? [5, 10, 15, 20] : (kind == .audio ? [2, 4, 7] : (kind == .video ? [4, 6, 8] : [10, 20, 30])) }
    private func countLabel(_ n: Int) -> String { kind == .audio ? "\(n == 2 ? 3 : (n == 4 ? 5 : 9)) min" : "\(n)" }

    var body: some View {
        VStack(spacing: 0) {
            if engine.working {
                ScrollView { MakingView(kind: kind, status: engine.status, progress: engine.progress, caption: engine.caption) { engine.stop() }.padding() }
            } else {
                Form {
                    Section("Subject (optional)") {
                        // A growing field with its words at the left: a labelled one-line field in a grouped form sets
                        // the text flush right, so a long ask seemed to type backwards and was cut to one line.
                        TextField("Subject", text: $topic, prompt: Text("Leave empty to cover the whole notebook"), axis: .vertical)
                            .labelsHidden()
                            .lineLimit(1...6)
                            .multilineTextAlignment(.leading)
                            .onSubmit { Task { await make() } }
                        Text("From \(notebook.title).").font(.callout).foregroundStyle(.secondary)
                    }
                    if kind == .visual {
                        VisualFormPicker(layout: $layout)
                        Text(layout?.hint ?? "The engine picks the form that fits the ask and the notes.")
                            .font(.callout).foregroundStyle(.secondary)
                        Picker("Detail", selection: $detail) { ForEach(VisualSpec.Detail.allCases) { Text($0.label).tag($0) } }.pickerStyle(.segmented)
                        Text(detail == .deep ? "More branches and facts." : (detail == .overview ? "The big picture only." : "A clear, balanced picture."))
                            .font(.callout).foregroundStyle(.secondary)
                        VisualThemePicker(theme: $theme).pickerStyle(.segmented)
                        Text(VisualTheme.named(theme).tagline + ".").font(.callout).foregroundStyle(.secondary)
                    }
                    if kind != .doc && kind != .visual {
                        Picker(kind == .quiz ? "Questions" : (kind == .audio ? "Length" : (kind == .video ? "Slides" : "Cards")), selection: $count) {
                            ForEach(counts, id: \.self) { Text(countLabel($0)).tag($0) }
                        }
                        .pickerStyle(.segmented)
                    }
                    if kind == .video {
                        Picker("Look", selection: $videoStyle) { ForEach(VideoStyle.allCases) { Text($0.label).tag($0) } }.pickerStyle(.segmented)
                        Picker("Voice", selection: $accent) { ForEach(AudioAccent.allCases) { Text($0.label).tag($0) } }.pickerStyle(.segmented)
                    }
                    if kind == .audio {
                        Picker("Style", selection: $style) { ForEach(AudioStyle.allCases) { Text($0.label).tag($0) } }
                        Picker("Voices", selection: $accent) { ForEach(AudioAccent.allCases) { Text($0.label).tag($0) } }.pickerStyle(.segmented)
                    }
                    Section {
                        Toggle("Only from this notebook", isOn: $notebookOnly)
                        Text(notebookOnly ? "Written from your notes only." : "Starts from your notes and may add general knowledge about the subject.")
                            .font(.callout).foregroundStyle(.secondary)
                    }
                    if let why = engine.error { Text(why).font(.callout).foregroundStyle(.orange) }
                }
                .formStyle(.grouped)
                Divider()
                HStack {
                    Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                    Spacer()
                    Button { Task { await make() } } label: { Label("Make \(kind.label)", systemImage: kind.symbol) }
                        .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                }
                .padding(14)
            }
        }
        .frame(width: 480, height: engine.working ? 460 : (kind == .doc ? 300 : (kind == .visual ? 500 : 400)))
        .interactiveDismissDisabled(engine.working)
    }

    private func make() async {
        if let made = await engine.make(kind, in: notebook, topic: topic, count: count, notebookOnly: notebookOnly, style: style, videoStyle: videoStyle, accent: accent, layout: layout, detail: detail, theme: kind == .visual ? theme : nil, library: library) {
            onMade(made); dismiss()
        }
    }
}

// MARK: - items

/// One item, open on the right of the Studio pane.
struct StudioItemDetail: View {
    let item: StudioItem

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Image(systemName: item.kind.symbol).foregroundStyle(item.kind.tint)
                Text(item.title).font(.headline).lineLimit(1)
                Spacer()
                StudioShareButton(item: item).labelStyle(.iconOnly).buttonStyle(.borderless).help("Share")
            }
            .padding(12)
            Divider()
            content
        }
    }

    @ViewBuilder private var content: some View {
        switch item.kind {
        case .doc: ScrollView { MarkdownText(text: item.body).frame(maxWidth: 760).padding(20).frame(maxWidth: .infinity) }
        case .quiz: QuizRun(questions: (try? JSONDecoder().decode([StudioQuestion].self, from: Data(item.body.utf8))) ?? [], subject: item.title.replacingOccurrences(of: "Quiz: ", with: ""))
        case .audio: if let script = try? JSONDecoder().decode(AudioScript.self, from: Data(item.body.utf8)) { MediaGate(item: item) { AudioOverviewView(script: script, title: item.title) } } else { Text("This audio can't be opened.") }
        case .video: if let info = try? JSONDecoder().decode(VideoInfo.self, from: Data(item.body.utf8)) { MediaGate(item: item) { VideoItemView(info: info) } } else { Text("This video can't be opened.") }
        case .visual:
            if item.body.contains("<svg") { SVGItemView(svg: item.body) }
            else if let spec = try? JSONDecoder().decode(VisualSpec.self, from: Data(item.body.utf8)) { VisualItemView(spec: spec, title: item.title, item: item) } else { Text("This visual can't be opened.") }
        case .cards: CardRun(cards: (try? JSONDecoder().decode([StudioCard].self, from: Data(item.body.utf8))) ?? [])
        }
    }
}

/// Renders Markdown-light text (headings, bullets, numbered lines, bold and italics) as real text.
struct MarkdownText: View {
    let text: String
    private struct Block: Identifiable { let id: Int; let kind: Kind; let text: String
        enum Kind { case h1, h2, h3, bullet, numbered(String), paragraph, visual(VisualSpec) } }

    /// The prose blocks, with each fenced visual (```visual … ```) as a block of its own, drawn in place.
    private var blocks: [Block] {
        var out: [Block] = []
        var n = 0
        for piece in VisualInline.pieces(in: text) {
            switch piece {
            case .visual(let spec): out.append(.init(id: n, kind: .visual(spec), text: spec.title)); n += 1
            case .text(let t): out.append(contentsOf: lines(t, from: n)); n = (out.last?.id ?? n) + 1
            }
        }
        return out
    }

    private func lines(_ text: String, from start: Int) -> [Block] {
        var out: [Block] = []
        for (k, raw) in text.components(separatedBy: "\n").enumerated() {
            let i = start + k
            let line = raw.trimmingCharacters(in: .whitespaces)
            if line.isEmpty { continue }
            if line.hasPrefix("### ") { out.append(.init(id: i, kind: .h3, text: String(line.dropFirst(4)))) }
            else if line.hasPrefix("## ") { out.append(.init(id: i, kind: .h2, text: String(line.dropFirst(3)))) }
            else if line.hasPrefix("# ") { out.append(.init(id: i, kind: .h1, text: String(line.dropFirst(2)))) }
            else if line.hasPrefix("- ") || line.hasPrefix("* ") || line.hasPrefix("• ") { out.append(.init(id: i, kind: .bullet, text: String(line.dropFirst(2)))) }
            else if let dot = line.firstIndex(of: "."), dot != line.startIndex, line[..<dot].allSatisfy(\.isNumber), line[line.index(after: dot)...].hasPrefix(" ") {
                out.append(.init(id: i, kind: .numbered(String(line[..<dot])), text: String(line[line.index(after: dot)...]).trimmingCharacters(in: .whitespaces)))
            } else if line == "---" { continue }
            else { out.append(.init(id: i, kind: .paragraph, text: line)) }
        }
        return out
    }

    private func styled(_ s: String) -> Text {
        Text((try? AttributedString(markdown: s, options: .init(interpretedSyntax: .inlineOnly))) ?? AttributedString(s))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(blocks) { b in
                switch b.kind {
                case .h1: styled(b.text).font(.title.bold()).padding(.top, 4)
                case .h2: styled(b.text).font(.title3.bold()).padding(.top, 8)
                case .h3: styled(b.text).font(.headline).padding(.top, 4)
                case .bullet: HStack(alignment: .firstTextBaseline, spacing: 8) { Text("•").foregroundStyle(.tint); styled(b.text) }
                case .numbered(let n): HStack(alignment: .firstTextBaseline, spacing: 8) { Text(n + ".").foregroundStyle(.tint).monospacedDigit(); styled(b.text) }
                case .paragraph: styled(b.text)
                case .visual(let spec): VisualInlineView(spec: spec).padding(.vertical, 6)
                }
            }
        }
        .textSelection(.enabled)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

private struct QuizRun: View {
    let questions: [StudioQuestion]
    let subject: String
    @State private var index = 0
    @State private var picked: Int?
    @State private var score = 0
    @State private var missed: [StudioQuestion] = []

    var body: some View {
        if index >= questions.count {
            QuizResult(score: score, total: questions.count, missed: missed, subject: subject) {
                index = 0; picked = nil; score = 0; missed = []
            }
        } else {
            let q = questions[index]
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text("Question \(index + 1) of \(questions.count)").font(.caption).foregroundStyle(.secondary)
                    Text(q.question).font(.title3.weight(.semibold))
                    VStack(spacing: 8) {
                        ForEach(q.choices.indices, id: \.self) { i in
                            Button {
                                guard picked == nil else { return }
                                picked = i
                                if i == q.answer { score += 1 } else { missed.append(q) }
                            } label: {
                                HStack {
                                    Text(q.choices[i]).foregroundStyle(.primary).multilineTextAlignment(.leading)
                                    Spacer()
                                    if let picked { Image(systemName: i == q.answer ? "checkmark.circle.fill" : (i == picked ? "xmark.circle.fill" : "circle")).foregroundStyle(i == q.answer ? .green : (i == picked ? .red : .secondary)) }
                                }
                                .padding(12).frame(maxWidth: .infinity)
                                .background(.quaternary, in: RoundedRectangle(cornerRadius: 10))
                            }
                            .buttonStyle(.plain)
                        }
                    }
                    if picked != nil {
                        if let why = q.why { Text(why).font(.callout).foregroundStyle(.secondary) }
                        Button(index + 1 < questions.count ? "Next" : "See how you did") { index += 1; picked = nil }
                            .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    }
                }
                .frame(maxWidth: 640).padding(24).frame(maxWidth: .infinity)
            }
        }
    }
}

private struct QuizResult: View {
    let score: Int
    let total: Int
    let missed: [StudioQuestion]
    let subject: String
    let again: () -> Void
    @State private var shown = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var fraction: Double { total == 0 ? 0 : Double(score) / Double(total) }
    private var tier: (title: String, line: String, symbol: String, color: Color) {
        switch fraction {
        case 1: ("Perfect!", "Every single one. Incredible.", "trophy.fill", .yellow)
        case 0.8...: ("Amazing!", "You really know this material.", "star.circle.fill", .orange)
        case 0.6...: ("Great job!", "Solid. A few more and you've got it.", "hand.thumbsup.fill", .green)
        case 0.4...: ("Good start", "Try again; it sticks the second time.", "arrow.triangle.2.circlepath.circle.fill", .blue)
        default: ("You'll get it next time", "Every miss is a thing learned. Have another go.", "leaf.fill", .teal)
        }
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 18) {
                ZStack {
                    Circle().stroke(tier.color.opacity(0.2), lineWidth: 14)
                    Circle().trim(from: 0, to: shown ? fraction : 0)
                        .stroke(tier.color, style: StrokeStyle(lineWidth: 14, lineCap: .round)).rotationEffect(.degrees(-90))
                    VStack(spacing: 4) {
                        Image(systemName: tier.symbol).font(.system(size: 44)).foregroundStyle(tier.color)
                            .symbolEffect(.bounce, value: shown)
                        Text("\(score) of \(total)").font(.headline).monospacedDigit()
                    }
                }
                .frame(width: 190, height: 190).padding(.top, 24)
                .accessibilityElement(children: .ignore).accessibilityLabel("Score: \(score) of \(total)")
                .overlay { if fraction >= 0.8 && shown && !reduceMotion { Confetti() } }

                Text(tier.title).font(.largeTitle.bold())
                Text(tier.line).foregroundStyle(.secondary).multilineTextAlignment(.center)

                if !missed.isEmpty {
                    VStack(alignment: .leading, spacing: 10) {
                        Text("Worth another look").font(.headline)
                        ForEach(missed) { q in
                            VStack(alignment: .leading, spacing: 2) {
                                Text(q.question).font(.subheadline)
                                Text("Answer: \(q.choices[q.answer])").font(.footnote).foregroundStyle(.green)
                            }
                        }
                        Label("Ask about “\(missed[0].question)” in the conversation, with detail, to do better next time.", systemImage: "lightbulb.fill")
                            .font(.footnote).foregroundStyle(.secondary).padding(.top, 4)
                    }
                    .padding().frame(maxWidth: .infinity, alignment: .leading)
                    .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 16))
                }

                Button(fraction == 1 ? "Play again" : "Try again", action: again).buttonStyle(.borderedProminent).controlSize(.large)
            }
            .frame(maxWidth: 560).padding().frame(maxWidth: .infinity)
        }
        .onAppear { withAnimation(reduceMotion ? nil : .easeOut(duration: 1.0)) { shown = true } }
    }
}

/// A short, calm burst of confetti for a great score.
private struct Confetti: View {
    @State private var go = false
    private let pieces: [(x: CGFloat, y: CGFloat, color: Color, size: CGFloat)] = (0..<18).map { i in
        let a = Double(i) / 18 * 2 * .pi
        return (CGFloat(cos(a)) * CGFloat.random(in: 110...170), CGFloat(sin(a)) * CGFloat.random(in: 110...170),
                [Color.yellow, .orange, .pink, .mint, .blue, .purple][i % 6], CGFloat.random(in: 6...11))
    }
    var body: some View {
        ZStack {
            ForEach(pieces.indices, id: \.self) { i in
                Circle().fill(pieces[i].color).frame(width: pieces[i].size, height: pieces[i].size)
                    .offset(x: go ? pieces[i].x : 0, y: go ? pieces[i].y : 0)
                    .opacity(go ? 0 : 1)
            }
        }
        .allowsHitTesting(false)
        .onAppear { withAnimation(.easeOut(duration: 1.4)) { go = true } }
    }
}

private struct CardRun: View {
    let cards: [StudioCard]
    @State private var index = 0
    @State private var flipped = false

    var body: some View {
        VStack(spacing: 16) {
            if cards.isEmpty { Text("No cards.") } else {
                Text("\(index + 1) of \(cards.count)").font(.caption).foregroundStyle(.secondary)
                Button { flipped.toggle() } label: {
                    Text(flipped ? cards[index].back : cards[index].front)
                        .font(.title3).multilineTextAlignment(.center).padding(24)
                        .frame(maxWidth: 560, minHeight: 240)
                        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 16))
                }
                .buttonStyle(.plain)
                .keyboardShortcut(.space, modifiers: [])
                Text(flipped ? "Answer" : "Click or press Space to see the answer").font(.footnote).foregroundStyle(.secondary)
                HStack {
                    Button("Back") { index -= 1; flipped = false }.disabled(index == 0).keyboardShortcut(.leftArrow, modifiers: [])
                    Spacer()
                    Button("Next") { index += 1; flipped = false }.disabled(index + 1 >= cards.count).keyboardShortcut(.rightArrow, modifiers: [])
                }
                .frame(maxWidth: 560)
            }
        }
        .padding(24)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// The "making it" screen: a lively animated visual with playful progress lines, shown while any Studio item is made.
struct MakingView: View {
    let kind: StudioItem.Kind
    let status: String
    let progress: Double
    let caption: String
    let stop: () -> Void

    private var quips: [String] {
        switch kind {
        case .audio: ["Tuning the microphones…", "Alex is clearing their throat…", "Sam is sipping coffee…", "Sharpening the banter…", "Rolling tape…"]
        case .quiz: ["Sharpening pencils…", "Hiding the answers…", "Thinking up tricky ones…", "Checking it twice…"]
        case .cards: ["Shuffling the deck…", "Writing both sides…", "Trimming the corners…"]
        case .visual: ["Sketching the layout…", "Picking colors…", "Connecting the dots…", "Tidying the lines…"]
        case .video: ["Setting up the lights…", "Drawing the slides…", "Recording the narration…", "Editing it together…"]
        case .doc: ["Gathering the best bits…", "Finding the right words…", "Adding the headings…", "Polishing…"]
        }
    }

    var body: some View {
        VStack(spacing: 16) {
            MakingGlyph(kind: kind).padding(.top, 24)
            TimelineView(.periodic(from: .now, by: 3)) { ctx in
                let i = Int(ctx.date.timeIntervalSinceReferenceDate / 3) % max(1, quips.count)
                Text(quips[i]).font(.headline).id(i).transition(.opacity)
            }
            Text(status).font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
            ProgressView(value: min(1, max(0.02, progress))).tint(.pink).padding(.horizontal, 24)
            if !caption.isEmpty {
                Text("“\(caption.suffix(160))”").font(.callout.italic()).foregroundStyle(.secondary)
                    .multilineTextAlignment(.center).padding(.horizontal).transition(.opacity)
            }
            Label("Made on this Mac. You can keep working in other apps meanwhile.", systemImage: "macbook").font(.caption).foregroundStyle(.secondary)
            Button("Stop", role: .cancel, action: stop).buttonStyle(.bordered)
        }
        .frame(maxWidth: .infinity).padding(.vertical, 8)
    }
}

/// The animation shown while an item is made: each kind has its own, so it matches what is being built.
struct MakingGlyph: View {
    let kind: StudioItem.Kind
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.animation(paused: reduceMotion)) { ctx in
            let t = ctx.date.timeIntervalSinceReferenceDate
            ZStack {
                switch kind {
                case .audio, .video: waves(t)
                case .visual: network(t)
                case .doc: lines(t)
                case .quiz: bubbles(t)
                case .cards: deck(t)
                }
            }
            .frame(width: 220, height: 110)
            .accessibilityHidden(true)
        }
    }

    private func waves(_ t: Double) -> some View {
        HStack(alignment: .center, spacing: 5) {
            ForEach(0..<19, id: \.self) { i in
                let phase = t * 3 + Double(i) * 0.55
                Capsule().fill(LinearGradient(colors: [.orange, .pink, .purple], startPoint: .bottom, endPoint: .top))
                    .frame(width: 7, height: 14 + 46 * abs(sin(phase) * cos(phase * 0.43 + Double(i))))
            }
        }
    }

    private func network(_ t: Double) -> some View {
        Canvas { ctx, size in
            let colors: [Color] = [.blue, .orange, .green, .pink, .purple, .teal]
            let c = CGPoint(x: size.width / 2, y: size.height / 2)
            for i in 0..<6 {
                let a = Double(i) / 6 * 2 * .pi + t * 0.25
                let grow = 0.5 + 0.5 * sin(t * 1.6 + Double(i) * 1.1)
                let r = 30 + 38 * grow
                let p = CGPoint(x: c.x + cos(a) * r * 1.5, y: c.y + sin(a) * r * 0.9)
                var line = Path(); line.move(to: c); line.addLine(to: p)
                ctx.stroke(line, with: .color(colors[i].opacity(0.45)),
                           style: StrokeStyle(lineWidth: 2, lineCap: .round, dash: [4, 4], dashPhase: -t * 16))
                let d = 10 + 12 * grow
                ctx.fill(Path(ellipseIn: CGRect(x: p.x - d / 2, y: p.y - d / 2, width: d, height: d)), with: .color(colors[i]))
            }
            let hub = 30 + 4 * sin(t * 3)
            ctx.fill(Path(ellipseIn: CGRect(x: c.x - hub / 2, y: c.y - hub / 2, width: hub, height: hub)), with: .color(.accentColor))
        }
    }

    private func lines(_ t: Double) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            ForEach(0..<5, id: \.self) { i in
                let w = 40 + 150 * abs(sin(t * 0.9 + Double(i) * 0.8))
                Capsule().fill(i == 0 ? Color.blue : Color.blue.opacity(0.35)).frame(width: i == 0 ? 90 : w, height: i == 0 ? 10 : 7)
            }
        }.frame(maxWidth: .infinity, alignment: .leading).padding(.leading, 14)
    }

    private func bubbles(_ t: Double) -> some View {
        HStack(spacing: 14) {
            ForEach(0..<3, id: \.self) { i in
                let hop = max(0, sin(t * 4 - Double(i) * 1.0))
                Text(i == 2 ? "✓" : "?").font(.title.bold()).foregroundStyle(.white)
                    .frame(width: 46, height: 46).background((i == 2 ? Color.green : Color.orange).gradient, in: Circle())
                    .offset(y: -22 * hop).scaleEffect(1 + 0.15 * hop)
            }
        }
    }

    private func deck(_ t: Double) -> some View {
        ZStack {
            ForEach(0..<3, id: \.self) { i in
                RoundedRectangle(cornerRadius: 12).fill([Color.green, .mint, .teal][i].gradient)
                    .frame(width: 96, height: 64)
                    .rotation3DEffect(.degrees(sin(t * 2 - Double(i) * 0.6) * 70), axis: (x: 0, y: 1, z: 0))
                    .offset(x: CGFloat(i - 1) * 26, y: CGFloat(i) * -4)
                    .opacity(1 - Double(i) * 0.2)
            }
        }
    }
}

// MARK: - media

/// Shows a paired-Mac-made audio or video item: the file downloads once (with progress), then `content` plays it.
struct MediaGate<Content: View>: View {
    let item: StudioItem
    @ViewBuilder var content: () -> Content
    @State private var have = false
    @State private var progress = 0.0
    @State private var loading = false
    @State private var failed = false

    private var url: URL? { item.mediaFile.map(StudioStore.audioURL) }

    var body: some View {
        Group {
            if have || item.mediaFile == nil { content() }
            else {
                VStack(spacing: 14) {
                    Image(systemName: item.kind.symbol).font(.system(size: 44)).foregroundStyle(item.kind.tint)
                    Text("Made on your paired Mac").font(.headline)
                    if let b = item.mediaBytes { Text(ByteCountFormatter.string(fromByteCount: Int64(b), countStyle: .file)).font(.footnote).foregroundStyle(.secondary) }
                    if loading { ProgressView(value: progress).frame(maxWidth: 240) }
                    else {
                        Button("Download to this Mac") { start() }.buttonStyle(.borderedProminent)
                        if failed { Text("Couldn't reach your paired Mac. Try again when it is on.").font(.footnote).foregroundStyle(.secondary) }
                    }
                }
                .padding()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .onAppear { if let url { have = FileManager.default.fileExists(atPath: url.path) } }
    }

    private func start() {
        loading = true; failed = false
        Task {
            do {
                try await StudioPull.download(item) { progress = $0 }
                have = true
            } catch { failed = true }
            loading = false
        }
    }
}

struct VideoItemView: View {
    let info: VideoInfo
    @State private var player: AVPlayer?

    var body: some View {
        Group {
            if let player { VideoPlayer(player: player) } else { ProgressView() }
        }
        .task { player = AVPlayer(url: StudioStore.audioURL(info.file)) }
        .onDisappear { player?.pause(); player = nil }
    }
}

/// Draws an SVG made on the paired Mac. JavaScript is off and nothing can navigate away, so the markup can only be drawn.
struct SVGItemView: NSViewRepresentable {
    let svg: String

    func makeCoordinator() -> Coordinator { Coordinator() }

    final class Coordinator: NSObject, WKNavigationDelegate {
        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, preferences: WKWebpagePreferences) async -> (WKNavigationActionPolicy, WKWebpagePreferences) {
            preferences.allowsContentJavaScript = false
            return (action.navigationType == .other ? .allow : .cancel, preferences)
        }
    }

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.defaultWebpagePreferences.allowsContentJavaScript = false
        let view = WKWebView(frame: .zero, configuration: config)
        view.navigationDelegate = context.coordinator
        view.setValue(false, forKey: "drawsBackground")
        let page = "<html><head><meta name=viewport content='width=device-width,initial-scale=1'><style>body{margin:0;display:flex;justify-content:center}svg{max-width:100%;height:auto}</style></head><body>\(svg)</body></html>"
        view.loadHTMLString(page, baseURL: nil)
        return view
    }

    func updateNSView(_ view: WKWebView, context: Context) {}
}

/// The visual: the 16:9 slide fitted to the pane, pinch or ⌘-scroll to zoom, double-click to zoom in or fit, share as a 2K image.
/// "Draw as" redraws the same content as another form of the same shape (no model call) and saves it over the item.
struct VisualItemView: View {
    @State private var spec: VisualSpec
    @State private var title: String
    private let item: StudioItem?
    @State private var picture: PlatformImage?
    @State private var scale: CGFloat = 1
    @State private var committed: CGFloat = 1
    @State private var fit: CGFloat = 1
    @State private var expanded: Set<Int> = []
    @State private var files: [VisualFiles.Kind: URL] = [:]

    init(spec: VisualSpec, title: String, item: StudioItem? = nil) {
        _spec = State(initialValue: spec); _title = State(initialValue: title); self.item = item
    }

    /// The SVG and PDF files to share, written once per drawing.
    private func writeFiles() {
        files = Dictionary(uniqueKeysWithValues: VisualFiles.Kind.allCases.filter { $0 != .png }.compactMap { k in VisualFiles.write(spec, title: title, as: k).map { (k, $0) } })
    }

    var body: some View {
        VStack(spacing: 0) {
            GeometryReader { geo in
                let base = min(geo.size.width / VisualSlide.size.width, geo.size.height / VisualSlide.size.height)
                let w = VisualSlide.size.width * base * scale, h = VisualSlide.size.height * base * scale
                ScrollView([.horizontal, .vertical]) {
                    VisualSlide(spec: spec, fit: fit, expanded: $expanded)
                        .scaleEffect(base * scale, anchor: .topLeading)
                        .frame(width: w, height: h, alignment: .topLeading)
                        .frame(minWidth: geo.size.width, minHeight: geo.size.height)
                }
                .simultaneousGesture(
                    MagnifyGesture()
                        .onChanged { scale = min(4, max(1, committed * $0.magnification)) }
                        .onEnded { _ in committed = scale }
                )
                .onTapGesture(count: 2) { withAnimation(.snappy) { scale = scale > 1.01 ? 1 : 2; committed = scale } }
            }
            Divider()
            HStack(spacing: 12) {
                if let picture {
                    Menu {
                        ShareLink(item: Image(platformImage: picture), preview: SharePreview(title, image: Image(platformImage: picture))) { Label("Image", systemImage: "photo") }
                        ForEach(VisualFiles.Kind.allCases.filter { $0 != .png }, id: \.self) { kind in
                            if let url = files[kind] { ShareLink(item: url) { Label(kind.label, systemImage: kind == .pdf ? "doc.richtext" : "scribble.variable") } }
                        }
                    } label: { Label("Share", systemImage: "square.and.arrow.up") }
                    .fixedSize().help("Share as an image, an SVG that opens in Keynote or Figma, or a PDF")
                    Button { copy(picture) } label: { Label("Copy Image", systemImage: "doc.on.doc") }
                }
                if !spec.layout.alternatives.isEmpty {
                    Menu { ForEach(spec.layout.alternatives) { form in Button { redraw(as: form) } label: { Label(form.label, systemImage: form.symbol) } } } label: {
                        Label("Draw as", systemImage: spec.layout.symbol)
                    }
                    .fixedSize().help("The same content as another form")
                }
                Menu { ForEach(VisualTheme.all) { t in Button { dress(in: t) } label: { Text(t.name) } } } label: {
                    Label(VisualTheme.named(spec.theme).name, systemImage: "paintpalette")
                }
                .fixedSize().help("The same picture in another theme")
                Spacer()
                if scale > 1.01 {
                    Button { withAnimation(.snappy) { scale = 1; committed = 1 } } label: { Label("Fit", systemImage: "arrow.down.right.and.arrow.up.left") }
                }
            }
            .buttonStyle(.borderless).font(.callout)
            .padding(10)
        }
        .task {
            fit = VisualSlide.fit(for: spec)
            picture = VisualSlide.image(for: spec, fit: fit)
            writeFiles()
        }
        .onChange(of: expanded) { _, now in
            withAnimation(.snappy) { fit = VisualSlide.fit(for: spec, expanded: now) }
        }
    }

    private func copy(_ image: NSImage) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.writeObjects([image])
    }

    private func redraw(as form: VisualSpec.Layout) {
        let old = spec.layout
        withAnimation(.snappy) { spec = spec.drawn(as: form); expanded = []; scale = 1; committed = 1 }
        title = VisualSpec.retitle(title, from: old, to: form)
        save()
    }

    private func dress(in theme: VisualTheme) {
        withAnimation(.snappy) { spec = spec.dressed(in: theme) }
        save()
    }

    private func save() {
        picture = VisualSlide.image(for: spec, fit: 1)
        writeFiles()
        if var saved = item, let data = try? JSONEncoder().encode(spec) {
            saved.body = String(decoding: data, as: UTF8.self); saved.title = title
            StudioStore.save(saved)
        }
    }
}

/// Plays an audio overview on this Mac and tells the transcript which line is being spoken.
@MainActor @Observable
final class AudioOverviewPlayer {
    private(set) var playing = false
    private(set) var time = 0.0
    private(set) var duration = 0.0
    private(set) var current = 0

    private var player: AVAudioPlayer?
    private var script: AudioScript?
    private var ticker: Task<Void, Never>?

    func load(_ script: AudioScript) {
        stop()
        self.script = script
        // Opened on the main actor: a local file opens in milliseconds, and AVAudioPlayer is not Sendable, so it
        // cannot come back from another task under strict concurrency.
        guard let p = try? AVAudioPlayer(contentsOf: StudioStore.audioURL(script.file)) else { return }
        player = p; duration = p.duration; time = 0; current = 0
    }

    func toggle() { playing ? pause() : play() }

    func play() {
        guard let player else { return }
        if time >= duration - 0.2 { player.currentTime = 0 }
        player.play(); playing = true
        ticker?.cancel()
        ticker = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(250))
                self?.tick()
            }
        }
    }

    func pause() { player?.pause(); playing = false }

    func seek(to t: Double) {
        guard let player else { return }
        player.currentTime = max(0, min(duration, t)); tick()
    }

    func skip(_ seconds: Double) { seek(to: time + seconds) }

    func stop() {
        ticker?.cancel(); ticker = nil
        player?.stop(); player = nil; playing = false
    }

    private func tick() {
        guard let player else { return }
        time = player.currentTime
        if let script, let i = script.lines.lastIndex(where: { $0.start <= time + 0.05 }) { current = i }
        if playing && !player.isPlaying { playing = false }
    }
}

/// The audio overview: a transcript that follows the voices, and playback controls.
struct AudioOverviewView: View {
    let script: AudioScript
    let title: String
    @State private var player = AudioOverviewPlayer()

    private func clock(_ t: Double) -> String { String(format: "%d:%02d", Int(t) / 60, Int(t) % 60) }

    var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 14) {
                        ForEach(script.lines.indices, id: \.self) { i in
                            let line = script.lines[i]
                            VStack(alignment: .leading, spacing: 2) {
                                Text(StudioEngine.hostNames[line.host]).font(.caption.bold()).foregroundStyle(line.host == 0 ? Color.orange : Color.teal)
                                Text(line.text).font(.body)
                            }
                            .opacity(i == player.current ? 1 : 0.5)
                            .id(i)
                            .onTapGesture { player.seek(to: line.start); if !player.playing { player.play() } }
                            .accessibilityElement(children: .combine).accessibilityAddTraits(.isButton).accessibilityHint("Plays from here")
                        }
                    }
                    .frame(maxWidth: 680).padding(20).frame(maxWidth: .infinity)
                }
                .onChange(of: player.current) { _, i in withAnimation { proxy.scrollTo(i, anchor: .center) } }
            }
            Divider()
            VStack(spacing: 8) {
                Slider(value: Binding(get: { player.time }, set: { player.seek(to: $0) }), in: 0...max(1, player.duration))
                HStack { Text(clock(player.time)); Spacer(); Text(clock(player.duration)) }.font(.caption).monospacedDigit().foregroundStyle(.secondary)
                HStack(spacing: 28) {
                    Button { player.skip(-15) } label: { Image(systemName: "gobackward.15").font(.title3) }.accessibilityLabel("Back 15 seconds")
                    Button { player.toggle() } label: { Image(systemName: player.playing ? "pause.circle.fill" : "play.circle.fill").font(.system(size: 40)) }
                        .accessibilityLabel(player.playing ? "Pause" : "Play").keyboardShortcut(.space, modifiers: [])
                    Button { player.skip(15) } label: { Image(systemName: "goforward.15").font(.title3) }.accessibilityLabel("Forward 15 seconds")
                }
                .buttonStyle(.borderless)
            }
            .padding(12).frame(maxWidth: 680).frame(maxWidth: .infinity).background(.bar)
        }
        .onAppear { player.load(script) }
        .onDisappear { player.stop() }
    }
}

// MARK: - sharing

/// What an item turns into when shared: a file for audio and video, readable text for the rest. Visuals share from their own view.
enum StudioShare {
    static func fileURL(_ item: StudioItem) -> URL? {
        let name: String?
        switch item.kind {
        case .audio: name = (try? JSONDecoder().decode(AudioScript.self, from: Data(item.body.utf8)))?.file
        case .video: name = (try? JSONDecoder().decode(VideoInfo.self, from: Data(item.body.utf8)))?.file
        default: name = nil
        }
        guard let name else { return nil }
        let url = StudioStore.audioURL(name)
        return FileManager.default.fileExists(atPath: url.path) ? url : nil
    }

    static func text(_ item: StudioItem) -> String? {
        switch item.kind {
        case .doc: return VisualInline.stripped(item.body)
        case .quiz:
            let qs = (try? JSONDecoder().decode([StudioQuestion].self, from: Data(item.body.utf8))) ?? []
            guard !qs.isEmpty else { return nil }
            let body = qs.enumerated().map { i, q in
                let choices = q.choices.enumerated().map { "   \(["A", "B", "C", "D", "E", "F"][min($0.offset, 5)]). \($0.element)" }.joined(separator: "\n")
                let answer = q.choices.indices.contains(q.answer) ? "Answer: \(q.choices[q.answer])" : ""
                return "\(i + 1). \(q.question)\n\(choices)\n   \(answer)" + (q.why.map { "\n   \($0)" } ?? "")
            }.joined(separator: "\n\n")
            return item.title + "\n\n" + body
        case .cards:
            let cs = (try? JSONDecoder().decode([StudioCard].self, from: Data(item.body.utf8))) ?? []
            guard !cs.isEmpty else { return nil }
            return item.title + "\n\n" + cs.map { "\($0.front)\n   \($0.back)" }.joined(separator: "\n\n")
        case .audio:
            guard let s = try? JSONDecoder().decode(AudioScript.self, from: Data(item.body.utf8)) else { return nil }
            return item.title + "\n\n" + s.lines.map { "\(StudioEngine.hostNames[$0.host]): \($0.text)" }.joined(separator: "\n\n")
        case .visual, .video: return nil
        }
    }
}

struct StudioShareButton: View {
    let item: StudioItem
    var body: some View {
        if let url = StudioShare.fileURL(item) {
            ShareLink(item: url) { Label("Share", systemImage: "square.and.arrow.up") }
        } else if let text = StudioShare.text(item) {
            ShareLink(item: text, subject: Text(item.title)) { Label("Share", systemImage: "square.and.arrow.up") }
        }
    }
}

/// A Studio item in its own window (Open in Window, ⇧⌘D).
struct StudioWindowKey: Codable, Hashable {
    let notebookId: String
    let itemId: String
    let title: String
}

struct StudioWindow: View {
    let key: StudioWindowKey

    var body: some View {
        Group {
            if let item = StudioStore.all(notebook: key.notebookId).first(where: { $0.id == key.itemId }) {
                StudioItemDetail(item: item)
            } else {
                ContentUnavailableView("This item is gone", systemImage: "sparkles", description: Text("It was deleted from this Mac."))
            }
        }
        .frame(minWidth: 480, minHeight: 360)
        .navigationTitle(key.title)
    }
}
