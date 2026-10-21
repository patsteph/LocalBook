import SwiftUI

struct AskView: View {
    let library: LibraryModel
    @FocusState private var focused: Bool
    @State private var showSources = false
    @State private var picking = false
    @State private var reader = ReadAloud()
    @State private var waited = 0

    var body: some View {
        @Bindable var model = library
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if library.notebooks.isEmpty {
                        ContentUnavailableView("No notebooks yet", systemImage: "books.vertical",
                                               description: Text("Open the Notebooks tab and sync with your Mac."))
                    } else {
                        if !ready {
                            PreparingCard(library: library)
                        } else if library.answerText.isEmpty && !library.answering && library.answerStatus.isEmpty {
                            intro
                        }
                        if library.answering && library.answerText.isEmpty {
                            VStack(alignment: .leading, spacing: 6) {
                                HStack { ProgressView(); Text("Reading your notebook… \(waited) s").font(.footnote).foregroundStyle(.secondary) }
                                if waited > 30 { Text("This is taking longer than usual. You can stop and ask again.").font(.caption).foregroundStyle(.orange) }
                            }
                            .task(id: library.answering) {
                                waited = 0
                                while library.answering && library.answerText.isEmpty { try? await Task.sleep(for: .seconds(1)); waited += 1 }
                            }
                        }
                        if !library.answerText.isEmpty {
                            Text(AnswerText.render(library.answerText)).textSelection(.enabled)
                            if !library.answering, !library.answerStatus.isEmpty {
                                Text(library.answerStatus).font(.caption2).foregroundStyle(.secondary)
                            }
                            if !library.answering { actions }
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
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Button { picking = true } label: {
                        HStack(spacing: 4) {
                            Text(currentTitle).font(.headline).lineLimit(1)
                            Image(systemName: "chevron.down").font(.caption.weight(.semibold))
                        }
                    }
                    .buttonStyle(.plain).disabled(library.notebooks.isEmpty || library.answering)
                    .accessibilityLabel("Notebook: \(currentTitle). Tap to change.")
                }
            }
            .sheet(isPresented: $picking) { NotebookPicker(library: library) }
        }
    }

    private var ready: Bool { library.searchState == .ready && library.answerModelState == .ready }

    private var currentTitle: String {
        library.notebooks.first { $0.id == library.scopeToNotebook }?.title ?? "Notebook"
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 8) {
            Label("Private by design", systemImage: "lock.fill").font(.footnote.weight(.semibold))
            Text("Ask about anything in “\(currentTitle)”. The question is searched and answered on this phone; nothing is sent to your Mac or anywhere else.")
                .font(.footnote).foregroundStyle(.secondary)
        }
        .padding().frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 12))
    }

    @ViewBuilder private var actions: some View {
        HStack(spacing: 18) {
            Button { reader.toggle(library.answerText) } label: {
                switch reader.state {
                case .preparing: HStack(spacing: 6) { ProgressView().controlSize(.small); Text("Preparing voice…") }
                case .speaking: Label("Pause", systemImage: "pause.fill")
                case .paused: Label("Resume", systemImage: "play.fill")
                default: Label("Listen", systemImage: "speaker.wave.2.fill")
                }
            }
            if reader.isActive {
                Button { reader.stop() } label: { Label("Stop", systemImage: "stop.fill") }
                ProgressView(value: reader.fraction).frame(maxWidth: 80)
            }
            Spacer()
            Button { UIPasteboard.general.string = library.answerText } label: { Label("Copy", systemImage: "doc.on.doc") }
        }
        .font(.footnote).buttonStyle(.borderless)
        if case .failed(let why) = reader.state { Text(why).font(.caption).foregroundStyle(.orange) }
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
                    .disabled(library.query.trimmingCharacters(in: .whitespaces).isEmpty || !ready)
            }
        }
        .padding(.horizontal).padding(.vertical, 8).background(.bar)
    }

    private func send() {
        guard ready, !library.answering else { return }
        reader.stop()
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

/// What the phone is getting ready before the first question: a short, honest checklist.
struct PreparingCard: View {
    let library: LibraryModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Getting ready").font(.headline)
            row("Your notebooks", done: !library.notebooks.isEmpty)
            row("Search", done: library.searchState == .ready, failed: failure(library.searchState))
            row("Answers", done: library.answerModelState == .ready, failed: answerFailure)
            Text("This happens once each time the app opens, and takes about a minute. You can read your notebooks meanwhile.")
                .font(.footnote).foregroundStyle(.secondary)
        }
        .padding().frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 12))
    }

    private func failure(_ s: LibraryModel.SearchState) -> String? { if case .failed(let why) = s { return why } else { return nil } }
    private var answerFailure: String? { if case .failed(let why) = library.answerModelState { return why } else { return nil } }

    @ViewBuilder private func row(_ title: String, done: Bool, failed: String? = nil) -> some View {
        HStack(alignment: .top, spacing: 10) {
            if let failed {
                Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                VStack(alignment: .leading) { Text(title); Text(failed).font(.caption).foregroundStyle(.secondary) }
            } else if done {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(.green); Text(title)
            } else {
                ProgressView().controlSize(.small); Text(title).foregroundStyle(.secondary)
            }
        }
    }
}
