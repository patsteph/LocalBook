import SwiftUI

/// Studio: briefings, quizzes and flashcards made on this phone from one notebook.
struct StudioView: View {
    let library: LibraryModel
    @State private var engine = StudioEngine()
    @State private var items: [StudioItem] = []
    @State private var notebookId: String?
    @State private var kind: StudioItem.Kind = .doc
    @State private var topic = ""
    @State private var count = 5
    @State private var notebookOnly = true
    @State private var opened: StudioItem?

    private var notebook: LibraryNotebookInfo? { library.notebooks.first { $0.id == notebookId } }
    private var counts: [Int] { kind == .quiz ? [5, 10, 15, 20] : [10, 20, 30] }

    var body: some View {
        NavigationStack {
            List {
                Section("Notebook") {
                    Picker("Notebook", selection: $notebookId) {
                        ForEach(library.notebooks) { Text($0.title).tag(Optional($0.id)) }
                    }
                }
                Section {
                    Picker("Make", selection: $kind) {
                        ForEach(StudioItem.Kind.allCases, id: \.self) { Text($0.label).tag($0) }
                    }.pickerStyle(.segmented)
                    TextField("Subject (optional)", text: $topic)
                    if kind != .doc {
                        Picker(kind == .quiz ? "Questions" : "Cards", selection: $count) {
                            ForEach(counts, id: \.self) { Text("\($0)").tag($0) }
                        }
                    }
                    Toggle("Only from this notebook", isOn: $notebookOnly)
                    Button { if let notebook { Task { await make(notebook) } } } label: {
                        Label("Make \(kind.label.lowercased())", systemImage: kind.symbol).frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .disabled(engine.working || notebook == nil)
                } footer: {
                    Text(notebookOnly ? "Written from this notebook's notes only." : "Starts from this notebook's notes and may add general knowledge about the subject.")
                }
                if engine.working {
                    Section {
                        HStack { ProgressView(); Text(engine.status).font(.footnote); Spacer(); Button("Stop") { engine.stop() }.font(.footnote) }
                        if !engine.preview.isEmpty { Text(String(engine.preview.suffix(400))).font(.caption).foregroundStyle(.secondary) }
                    } footer: { Text("Keep the app open while it works.") }
                }
                if let why = engine.error { Section { Text(why).font(.footnote).foregroundStyle(.orange) } }
                Section("Saved") {
                    if items.isEmpty { Text("Nothing yet.").foregroundStyle(.secondary) }
                    ForEach(items) { item in
                        Button { opened = item } label: {
                            VStack(alignment: .leading, spacing: 2) {
                                Label(item.title, systemImage: item.kind.symbol).foregroundStyle(.primary)
                                Text(item.created.formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundStyle(.secondary)
                            }
                        }
                        .swipeActions { Button("Delete", role: .destructive) { StudioStore.delete(item); reload() } }
                    }
                }
            }
            .navigationTitle("Studio")
            .onAppear { if notebookId == nil || notebook == nil { notebookId = library.scopeToNotebook ?? library.notebooks.first?.id } }
            .onChange(of: library.scopeToNotebook) { _, id in if let id { notebookId = id } }
            .onChange(of: notebookId) { _, _ in reload() }
            .onChange(of: kind) { _, _ in count = kind == .quiz ? 5 : 10 }
            .task { reload() }
            .sheet(item: $opened) { StudioItemView(item: $0) }
        }
    }

    private func reload() { items = StudioStore.all(notebook: notebookId) }

    private func make(_ notebook: LibraryNotebookInfo) async {
        if let made = await engine.make(kind, in: notebook, topic: topic, count: count, notebookOnly: notebookOnly, library: library) { reload(); opened = made }
    }
}

/// Renders Markdown-light text (headings, bullets, numbered lines, bold and italics) as real text.
struct MarkdownText: View {
    let text: String
    private struct Block: Identifiable { let id: Int; let kind: Kind; let text: String
        enum Kind { case h1, h2, h3, bullet, numbered(String), paragraph } }

    private var blocks: [Block] {
        var out: [Block] = []
        for (i, raw) in text.components(separatedBy: "\n").enumerated() {
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
                }
            }
        }
        .textSelection(.enabled)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct StudioItemView: View {
    let item: StudioItem
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Group {
                switch item.kind {
                case .doc: ScrollView { MarkdownText(text: item.body).padding() }
                case .quiz: QuizRun(questions: (try? JSONDecoder().decode([StudioQuestion].self, from: Data(item.body.utf8))) ?? [], subject: item.title.replacingOccurrences(of: "Quiz: ", with: ""))
                case .cards: CardRun(cards: (try? JSONDecoder().decode([StudioCard].self, from: Data(item.body.utf8))) ?? [])
                }
            }
            .navigationTitle(item.title).navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
        }
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
            List {
                Section("Question \(index + 1) of \(questions.count)") { Text(q.question).font(.headline) }
                Section {
                    ForEach(q.choices.indices, id: \.self) { i in
                        Button {
                            guard picked == nil else { return }
                            picked = i
                            if i == q.answer { score += 1 } else { missed.append(q) }
                        } label: {
                            HStack {
                                Text(q.choices[i]).foregroundStyle(.primary)
                                Spacer()
                                if let picked { Image(systemName: i == q.answer ? "checkmark.circle.fill" : (i == picked ? "xmark.circle.fill" : "circle")).foregroundStyle(i == q.answer ? .green : (i == picked ? .red : .secondary)) }
                            }
                        }
                    }
                }
                if picked != nil {
                    Section {
                        if let why = q.why { Text(why).font(.footnote) }
                        Button(index + 1 < questions.count ? "Next" : "See how you did") { index += 1; picked = nil }
                    }
                }
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
                .overlay { if fraction >= 0.8 && shown { Confetti() } }

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
                        Label("Ask about “\(missed[0].question)” on the Ask tab, with detail, to do better next time.", systemImage: "lightbulb.fill")
                            .font(.footnote).foregroundStyle(.secondary).padding(.top, 4)
                    }
                    .padding().frame(maxWidth: .infinity, alignment: .leading)
                    .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 16))
                }

                Button(fraction == 1 ? "Play again" : "Try again", action: again).buttonStyle(.borderedProminent).controlSize(.large)
            }
            .padding()
        }
        .onAppear { withAnimation(.easeOut(duration: 1.0)) { shown = true } }
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
                        .frame(maxWidth: .infinity, minHeight: 240)
                        .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 16))
                }.buttonStyle(.plain)
                Text(flipped ? "Answer" : "Tap to see the answer").font(.footnote).foregroundStyle(.secondary)
                HStack {
                    Button("Back") { index -= 1; flipped = false }.disabled(index == 0)
                    Spacer()
                    Button("Next") { index += 1; flipped = false }.disabled(index + 1 >= cards.count)
                }
            }
        }.padding()
    }
}
