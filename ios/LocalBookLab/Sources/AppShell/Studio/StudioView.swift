import SwiftUI

/// Studio: briefings, quizzes and flashcards made on this phone from the selected notebook.
struct StudioView: View {
    let library: LibraryModel
    @State private var engine = StudioEngine()
    @State private var items: [StudioItem] = []
    @State private var topic = ""
    @State private var opened: StudioItem?

    private var notebook: LibraryNotebookInfo? { library.notebooks.first { $0.id == library.scopeToNotebook } }

    var body: some View {
        NavigationStack {
            List {
                if let notebook {
                    Section {
                        TextField("Topic (optional)", text: $topic)
                        ForEach(StudioItem.Kind.allCases, id: \.self) { kind in
                            Button { Task { await make(kind, notebook) } } label: { Label("Make a \(kind.label.lowercased())", systemImage: kind.symbol) }
                                .disabled(engine.working)
                        }
                    } header: { Text(notebook.title) } footer: {
                        Text("Made on this phone from this notebook only. Keep the app open while it works.")
                    }
                    if engine.working {
                        Section {
                            HStack { ProgressView(); Text(engine.status).font(.footnote); Spacer(); Button("Stop") { engine.stop() }.font(.footnote) }
                            if !engine.preview.isEmpty { Text(engine.preview.suffix(400)).font(.caption).foregroundStyle(.secondary) }
                        }
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
                } else {
                    Text("Pick a notebook on the Ask screen first.").foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Studio")
            .task(id: library.scopeToNotebook) { reload() }
            .sheet(item: $opened) { StudioItemView(item: $0) }
        }
    }

    private func reload() { items = StudioStore.all(notebook: library.scopeToNotebook) }

    private func make(_ kind: StudioItem.Kind, _ notebook: LibraryNotebookInfo) async {
        if let made = await engine.make(kind, in: notebook, topic: topic, library: library) { reload(); opened = made }
    }
}

struct StudioItemView: View {
    let item: StudioItem
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Group {
                switch item.kind {
                case .doc: ScrollView { Text((try? AttributedString(markdown: item.body, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(item.body)).padding().frame(maxWidth: .infinity, alignment: .leading) }
                case .quiz: QuizRun(questions: (try? JSONDecoder().decode([StudioQuestion].self, from: Data(item.body.utf8))) ?? [])
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
    @State private var index = 0
    @State private var picked: Int?
    @State private var score = 0

    var body: some View {
        if index >= questions.count {
            VStack(spacing: 8) { Text("\(score) of \(questions.count)").font(.largeTitle); Text("correct").foregroundStyle(.secondary) }.frame(maxHeight: .infinity)
        } else {
            let q = questions[index]
            List {
                Section("Question \(index + 1) of \(questions.count)") { Text(q.question).font(.headline) }
                Section {
                    ForEach(q.choices.indices, id: \.self) { i in
                        Button {
                            guard picked == nil else { return }
                            picked = i; if i == q.answer { score += 1 }
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
                        Button(index + 1 < questions.count ? "Next" : "Finish") { index += 1; picked = nil }
                    }
                }
            }
        }
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
