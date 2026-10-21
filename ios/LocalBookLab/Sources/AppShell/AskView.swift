import SwiftUI

struct AskView: View {
    let library: LibraryModel
    @FocusState private var focused: Bool
    @State private var showSources = false

    var body: some View {
        @Bindable var model = library
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if library.notebooks.isEmpty {
                        ContentUnavailableView("No notebooks yet", systemImage: "books.vertical",
                                               description: Text("Open the Notebooks tab and sync with your Mac."))
                    } else {
                        if library.answerText.isEmpty && !library.answering && library.answerStatus.isEmpty {
                            intro
                        }
                        if library.answering && library.answerText.isEmpty {
                            HStack { ProgressView(); Text(library.answerStatus.isEmpty ? "Thinking…" : library.answerStatus).font(.footnote).foregroundStyle(.secondary) }
                        }
                        if !library.answerText.isEmpty {
                            Text(AnswerText.render(library.answerText)).textSelection(.enabled)
                            if !library.answering, !library.answerStatus.isEmpty {
                                Text(library.answerStatus).font(.caption2).foregroundStyle(.secondary)
                            }
                            sources
                            if !library.followUps.isEmpty {
                                ForEach(library.followUps, id: \.self) { q in
                                    Button(q) { library.query = q; Task { await library.ask() } }.font(.footnote).buttonStyle(.bordered)
                                }
                            }
                        }
                    }
                }
                .padding()
            }
            .scrollDismissesKeyboard(.interactively)
            .safeAreaInset(edge: .bottom) { composer }
            .navigationTitle("Ask")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Picker("Notebook", selection: $model.scopeToNotebook) {
                            ForEach(library.notebooks) { Text($0.title).tag(String?.some($0.id)) }
                        }
                    } label: {
                        Label(currentTitle, systemImage: "books.vertical").labelStyle(.titleAndIcon).font(.footnote)
                    }
                    .disabled(library.notebooks.isEmpty)
                }
            }
        }
    }

    private var currentTitle: String {
        library.notebooks.first { $0.id == library.scopeToNotebook }?.title ?? "Notebook"
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Private by design", systemImage: "lock.fill").font(.footnote.weight(.semibold))
            Text("Ask about anything in “\(currentTitle)”. The question is searched and answered on this phone; nothing is sent to your Mac or anywhere else.")
                .font(.footnote).foregroundStyle(.secondary)
            if case .preparing(let what) = library.searchState { HStack { ProgressView(); Text(what).font(.caption) } }
            if case .failed(let why) = library.searchState { Text(why).font(.caption).foregroundStyle(.red) }
        }
        .padding().frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 12))
    }

    @ViewBuilder private var sources: some View {
        if !library.answerSources.isEmpty {
            DisclosureGroup("Sources (\(library.answerSources.count))", isExpanded: $showSources) {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(Array(library.answerSources.enumerated()), id: \.element.id) { i, h in
                        VStack(alignment: .leading, spacing: 2) {
                            Text("[\(i + 1)] \(h.filename)").font(.footnote.weight(.medium))
                            Text(h.text.count > 240 ? String(h.text.prefix(240)) + "…" : h.text).font(.footnote).foregroundStyle(.secondary)
                        }
                    }
                }
                .padding(.top, 6)
            }
            .font(.footnote)
        }
    }

    private var composer: some View {
        @Bindable var model = library
        return HStack(alignment: .bottom, spacing: 8) {
            TextField("Ask your notebook", text: $model.query, axis: .vertical)
                .lineLimit(1...4).focused($focused)
                .padding(10).background(.quaternary, in: RoundedRectangle(cornerRadius: 18))
                .submitLabel(.send).onSubmit(send)
            if library.answering {
                Button { library.stopAnswer() } label: { Image(systemName: "stop.circle.fill").font(.title) }
            } else {
                Button(action: send) { Image(systemName: "arrow.up.circle.fill").font(.title) }
                    .disabled(library.query.trimmingCharacters(in: .whitespaces).isEmpty || library.searchState != .ready)
            }
        }
        .padding(.horizontal).padding(.vertical, 8).background(.bar)
    }

    private func send() {
        guard library.searchState == .ready, !library.answering else { return }
        focused = false
        showSources = false
        Task { await library.ask() }
    }
}

/// The model writes light markdown (headings, bullets, bold). Show it readably without a full renderer.
enum AnswerText {
    static func render(_ raw: String) -> AttributedString {
        var out = AttributedString()
        for (i, line) in raw.split(separator: "\n", omittingEmptySubsequences: false).enumerated() {
            var text = String(line)
            var heading = false
            if let r = text.range(of: #"^#{1,4}\s+"#, options: .regularExpression) { text.removeSubrange(r); heading = true }
            if let r = text.range(of: #"^(\s*)[\*\-]\s+"#, options: .regularExpression) {
                let indent = text[r].prefix { $0 == " " }
                text.replaceSubrange(r, with: indent + "•  ")
            }
            var piece = (try? AttributedString(markdown: text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(text)
            if heading { piece.font = .headline }
            if i > 0 { out.append(AttributedString("\n")) }
            out.append(piece)
        }
        return out
    }
}
