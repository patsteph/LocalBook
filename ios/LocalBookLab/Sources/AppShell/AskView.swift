import SwiftUI

struct AskView: View {
    let library: LibraryModel
    @FocusState private var focused: Bool
    @State private var picking = false
    @State private var reader = ReadAloud()
    @State private var waited = 0
    @State private var queued: String?
    @State private var writingNote = false
    @State private var recordingMeeting = false
    @State private var atBottom = true
    @State private var openSource: SourceRef?
    @State private var length = AnswerLength.current.rawValue
    @State private var dictation = Dictation()
    @State private var dictationBase = ""
    @State private var finishTick = 0
    @State private var failTick = 0
    @State private var sendTick = 0
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.dynamicTypeSize) private var typeSize
    @ScaledMetric(relativeTo: .body) private var hit: CGFloat = 44          // tap targets grow with the text size

    var body: some View {
        @Bindable var model = library
        NavigationStack {
            ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    if library.notebooks.isEmpty {
                        ContentUnavailableView("No notebooks yet", systemImage: "books.vertical",
                                               description: Text("Open the Notebooks tab and sync with your Mac."))
                    } else {
                        if !ready { ReadyBar(library: library) }
                        if let q = queued {
                            HStack {
                                Label("Queued: \(q)", systemImage: "clock").lineLimit(2)
                                Spacer()
                                Button("Cancel") { queued = nil }
                            }
                            .font(.footnote).padding(12).background(.quaternary, in: RoundedRectangle(cornerRadius: 12))
                        }
                        ForEach(Array(library.history.enumerated()), id: \.element.id) { i, turn in
                            pastTurn(turn, previous: i > 0 ? library.history[i - 1].notebookId : nil)
                        }
                        if !library.shownQuestion.isEmpty, let nb = library.liveNotebookId, nb != (library.history.last?.notebookId ?? nb) {
                            notebookTag(library.notebooks.first { $0.id == nb }?.title ?? "Notebook")
                        }
                        if library.answering || !library.answerText.isEmpty || !library.answerStatus.isEmpty, !library.shownQuestion.isEmpty {
                            Text(library.shownQuestion).font(.subheadline.weight(.medium))
                                .padding(.horizontal, 12).padding(.vertical, 8)
                                .background(.tint.opacity(0.15), in: RoundedRectangle(cornerRadius: 14))
                                .frame(maxWidth: .infinity, alignment: .trailing)
                        } else if queued == nil && library.history.isEmpty {
                            intro
                        }
                        if library.answering && library.answerText.isEmpty {
                            VStack(alignment: .leading, spacing: 8) {
                                HStack { ProgressView(); Text(library.answerStatus.isEmpty ? "Working…" : library.answerStatus).font(.footnote).foregroundStyle(.secondary) }
                                if !library.answerSources.isEmpty {
                                    ForEach(Array(library.answerSources.prefix(4).enumerated()), id: \.element.id) { i, h in
                                        Label(h.filename, systemImage: "doc.text").font(.caption).foregroundStyle(.secondary).lineLimit(1)
                                    }
                                }
                                if waited > 30 { Text("This is taking longer than usual. You can stop and ask again.").font(.caption).foregroundStyle(.orange) }
                            }
                            .task(id: library.answering) {
                                waited = 0
                                while library.answering && library.answerText.isEmpty { try? await Task.sleep(for: .seconds(1)); waited += 1 }
                            }
                        }
                        if !library.answerText.isEmpty {
                            Text(AnswerText.render(library.answerText)).textSelection(.enabled)
                                .contentTransition(.opacity)                          // new words ease in instead of popping
                                .animation(reduceMotion ? nil : .easeOut(duration: 0.25), value: library.answerText)
                                .environment(\.openURL, OpenURLAction { url in
                                    if url.scheme == "lb-source", let n = Int(url.host ?? ""), n >= 1, n <= library.answerSources.count {
                                        openSource = SourceRef(number: n, hits: library.answerSources); return .handled
                                    }
                                    return .systemAction
                                })
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
                    Color.clear.frame(height: 1).id("bottom")
                }
                .padding()
            }
            .onScrollGeometryChange(for: Bool.self) { g in
                g.contentOffset.y + g.containerSize.height >= g.contentSize.height - 60
            } action: { _, now in atBottom = now }
            .onChange(of: library.answerText) { _, _ in
                if atBottom && library.answering { proxy.scrollTo("bottom", anchor: .bottom) }
            }
            .onChange(of: library.shownQuestion) { _, q in
                if !q.isEmpty { withAnimation(reduceMotion ? nil : .easeOut) { proxy.scrollTo("bottom", anchor: .bottom) } }     // a new turn starts at the bottom
            }
            .overlay(alignment: .bottom) {
                if library.answering && !atBottom {
                    Button { withAnimation(reduceMotion ? nil : .easeOut) { proxy.scrollTo("bottom", anchor: .bottom) } } label: {
                        Label("Jump to latest", systemImage: "arrow.down").font(.footnote.weight(.semibold))
                            .padding(.horizontal, 14).padding(.vertical, 10).background(.regularMaterial, in: Capsule())
                    }
                    .padding(.bottom, 8)
                }
            }
            .scrollDismissesKeyboard(.interactively)
            }
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
                ToolbarItem(placement: .topBarTrailing) {
                    Menu {
                        Button { writingNote = true } label: { Label("New note", systemImage: "square.and.pencil") }
                        Button { recordingMeeting = true } label: { Label("Record a meeting", systemImage: "person.2.wave.2") }
                    } label: { Image(systemName: "square.and.pencil") }
                        .disabled(library.scopeToNotebook == nil || !ready || library.answering)
                        .accessibilityLabel("New note in \(currentTitle)")
                }
            }
            .sheet(isPresented: $picking) { NotebookPicker(library: library) }
            .sheet(isPresented: $recordingMeeting) { if let id = library.scopeToNotebook { MeetingView(library: library, notebookId: id) } }
            .sheet(isPresented: $writingNote) { if let id = library.scopeToNotebook { NoteEditor(library: library, notebookId: id) } }
            .sheet(item: $openSource) { ref in
                SourceSheet(number: ref.number, hit: ref.hits[ref.number - 1])
                    .presentationDetents([.medium, .large])
            }
            .sensoryFeedback(.impact(weight: .light), trigger: sendTick)
            .sensoryFeedback(.success, trigger: finishTick)
            .sensoryFeedback(.warning, trigger: failTick)
            .onChange(of: library.answering) { was, now in
                guard was, !now else { return }
                if library.answerText.isEmpty { failTick += 1 } else { finishTick += 1 }
            }
            .onChange(of: ready) { _, isReady in
                guard isReady, let q = queued else { return }
                queued = nil
                library.query = q
                Task { await library.ask() }
            }
        }
    }

    /// The notebook a run of turns was asked in, shown where the conversation moves to another notebook.
    private func notebookTag(_ title: String) -> some View {
        Label(title, systemImage: "book.closed").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            .padding(.top, 6).frame(maxWidth: .infinity, alignment: .center)
    }

    /// A finished turn higher up in the conversation.
    @ViewBuilder private func pastTurn(_ turn: AskTurn, previous: String?) -> some View {
        if turn.notebookId != previous { notebookTag(turn.notebookTitle) }
        Text(turn.question).font(.subheadline.weight(.medium))
            .padding(.horizontal, 12).padding(.vertical, 8)
            .background(.tint.opacity(0.15), in: RoundedRectangle(cornerRadius: 14))
            .frame(maxWidth: .infinity, alignment: .trailing)
        if turn.answer.isEmpty {
            if !turn.status.isEmpty { Text(turn.status).font(.footnote).foregroundStyle(.orange) }
        } else {
            Text(AnswerText.render(turn.answer)).textSelection(.enabled)
                .environment(\.openURL, OpenURLAction { url in
                    if url.scheme == "lb-source", let n = Int(url.host ?? ""), n >= 1, n <= turn.sources.count {
                        openSource = SourceRef(number: n, hits: turn.sources); return .handled
                    }
                    return .systemAction
                })
            HStack(spacing: 16) {
                Button { reader.toggle(turn.answer) } label: { Label("Listen", systemImage: "speaker.wave.2.fill") }
                Button { UIPasteboard.general.string = turn.answer } label: { Label("Copy", systemImage: "doc.on.doc") }
                Spacer()
                if !turn.sources.isEmpty {
                    ForEach(Array(turn.sources.enumerated()), id: \.element.id) { i, _ in
                        Button { openSource = SourceRef(number: i + 1, hits: turn.sources) } label: {
                            Text("\(i + 1)").font(.caption.weight(.semibold)).frame(minWidth: 28, minHeight: 28)
                                .background(.tint.opacity(0.15), in: Circle())
                        }.buttonStyle(.plain)
                    }
                }
            }
            .font(.footnote).buttonStyle(.borderless)
        }
        Divider().padding(.vertical, 4)
    }

    private var ready: Bool { library.searchState == .ready && library.answerModelState == .ready }

    private var currentTitle: String {
        library.notebooks.first { $0.id == library.scopeToNotebook }?.title ?? "Notebook"
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(currentTitle).font(.title3.bold())
            Text("Try asking:").font(.footnote).foregroundStyle(.secondary)
            ForEach(["What are the main points in this notebook?", "What changed most recently?", "Who is mentioned most often?"], id: \.self) { q in
                Button { library.query = q; send() } label: {
                    Text(q).font(.subheadline).multilineTextAlignment(.leading).frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12).background(.quaternary, in: RoundedRectangle(cornerRadius: 12))
                }
                .buttonStyle(.plain)
            }
        }
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
            VStack(alignment: .leading, spacing: 4) {
                Text("Sources").font(.footnote.weight(.semibold)).foregroundStyle(.secondary)
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(Array(library.answerSources.enumerated()), id: \.element.id) { i, h in
                            Button { openSource = SourceRef(number: i + 1, hits: library.answerSources) } label: {
                                Text("\(i + 1)").font(.subheadline.weight(.semibold)).frame(minWidth: hit, minHeight: hit)
                                    .background(.tint.opacity(0.15), in: Circle())
                            }
                            .buttonStyle(.plain)
                            .accessibilityLabel("Source \(i + 1): \(h.filename)")
                        }
                    }
                }
            }
        }
    }

    private var composer: some View {
        VStack(alignment: .leading, spacing: 4) {
            if let why = dictation.failure { Text(why).font(.caption).foregroundStyle(.orange) }
            composerRow
        }
        .padding(.horizontal).padding(.vertical, 8).background(.bar)
    }

    private var composerRow: some View {
        @Bindable var model = library
        // At the largest text sizes a row of three controls leaves the field a sliver: stack them instead.
        let layout = typeSize.isAccessibilitySize ? AnyLayout(VStackLayout(alignment: .trailing, spacing: 8))
                                                  : AnyLayout(HStackLayout(alignment: .bottom, spacing: 8))
        return layout {
            Menu {
                Picker("Answer length", selection: $length) {
                    ForEach(AnswerLength.allCases) { Text($0.label).tag($0.rawValue) }
                }
            } label: { Image(systemName: "text.alignleft").font(.title3).frame(width: hit, height: hit) }
            .accessibilityLabel("Answer length")
            .onChange(of: length) { _, v in UserDefaults.standard.set(v, forKey: "answerLength") }
            TextField(ready ? "Ask your notebook" : "Ask now, answers when ready", text: $model.query, axis: .vertical)
                .lineLimit(1...4).focused($focused)
                .padding(10).background(.quaternary, in: RoundedRectangle(cornerRadius: 18))
                .submitLabel(.send).onSubmit(send)
                // A multi-line field turns Return into a new line; make Return send, like the arrow button.
                .onChange(of: library.query) { _, v in
                    guard v.hasSuffix("\n") else { return }
                    library.query = v.trimmingCharacters(in: .whitespacesAndNewlines)
                    send()
                }
            Button {
                dictationBase = library.query.isEmpty ? "" : library.query + " "
                dictation.toggle { spoken in library.query = dictationBase + spoken }
            } label: {
                Image(systemName: dictation.listening ? "mic.fill" : "mic").font(.title3)
                    .foregroundStyle(dictation.listening ? Color.red : Color.accentColor).frame(width: hit, height: hit)
            }
            .disabled(library.answering)
            .accessibilityLabel(dictation.listening ? "Stop dictating" : "Dictate your question")
            if library.answering {
                Button { library.stopAnswer() } label: { Image(systemName: "stop.circle.fill").font(.title).frame(width: hit, height: hit) }
                    .accessibilityLabel("Stop")
            } else {
                Button(action: send) { Image(systemName: "arrow.up.circle.fill").font(.title).frame(width: hit, height: hit) }
                    .disabled(library.query.trimmingCharacters(in: .whitespaces).isEmpty)
                    .accessibilityLabel("Send")
            }
        }
    }

    private func send() {
        let q = library.query.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty, !library.answering else { return }
        reader.stop()
        dictation.stop()
        focused = false
        sendTick += 1
        if !ready { queued = q; library.query = ""; return }
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
            text = text.replacingOccurrences(of: #"\[(\d{1,2})\]"#, with: "[\\[$1\\]](lb-source://$1)", options: .regularExpression)
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

struct SourceRef: Identifiable { let id = UUID(); let number: Int; let hits: [LibraryHit] }

struct SourceSheet: View {
    let number: Int
    let hit: LibraryHit
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    if hit.sourceId.hasPrefix(LocalNotes.prefix), let ui = UIImage(contentsOfFile: LibraryModel.photoURL(hit.sourceId).path) {
                        Image(uiImage: ui).resizable().scaledToFit().clipShape(RoundedRectangle(cornerRadius: 12))
                    }
                    Text(hit.text).textSelection(.enabled)
                }
                .frame(maxWidth: .infinity, alignment: .leading).padding()
            }
            .navigationTitle("Source \(number)")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .safeAreaInset(edge: .top) {
                Text(hit.filename).font(.footnote).foregroundStyle(.secondary).padding(.horizontal).frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}

/// One calm line while the phone gets ready; the question field stays usable.
struct ReadyBar: View {
    let library: LibraryModel
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let why = failure {
                Label(why, systemImage: "exclamationmark.triangle.fill").font(.footnote).foregroundStyle(.orange)
            } else {
                Text("Getting ready — about a minute. You can ask now; the answer starts when ready.").font(.footnote).foregroundStyle(.secondary)
                ProgressView(value: progress)
            }
        }
        .accessibilityElement(children: .combine)
    }
    private var progress: Double {
        var p = library.notebooks.isEmpty ? 0.0 : 0.2
        if library.searchState == .ready { p += 0.3 }
        if library.answerModelState == .ready { p += 0.5 }
        return p
    }
    private var failure: String? {
        if case .failed(let why) = library.searchState { return why }
        if case .failed(let why) = library.answerModelState { return why }
        return nil
    }
}
