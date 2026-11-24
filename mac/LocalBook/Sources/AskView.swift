import AppKit
import LocalBookCore
import LocalBookEngines
import SwiftUI

/// The primary screen: a conversation with one notebook, the composer pinned at the bottom. The same conversation the
/// phone shows (`ios/LocalBookLab/Sources/AppShell/AskView.swift`), laid out for a window: a 60 to 70 character
/// measure, citations as inline anchors that open a popover, Send that becomes Stop while streaming (⌘.).
struct AskView: View {
    @Environment(AppModel.self) private var model
    @FocusState private var composerFocused: Bool
    @State private var waited = 0
    @State private var queued: String?
    @State private var atBottom = true
    @State private var openSource: SourceRef?
    @State private var length = AnswerLength.current.rawValue
    @State private var reader = ReadAloud()
    /// The slash action chosen for this send, shown as a chip at the front of the field.
    @State private var command: SlashCommand?
    @State private var highlighted = 0
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var library: LibraryModel { model.library }
    private var ready: Bool { model.ready }
    private var currentTitle: String { model.selectedNotebook?.title ?? "Notebook" }
    /// This notebook's own conversation: a turn stays with the notebook it was asked in, and switching notebooks shows
    /// that notebook's turns only (the old app's rule).
    private var turns: [AskTurn] { library.history.filter { $0.notebookId == library.scopeToNotebook } }

    var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    conversation
                        .frame(maxWidth: 720)
                        .padding(.horizontal, 24)
                        .padding(.vertical, 20)
                        .frame(maxWidth: .infinity)
                }
                .onScrollGeometryChange(for: Bool.self) { g in
                    g.contentOffset.y + g.containerSize.height >= g.contentSize.height - 60
                } action: { _, now in atBottom = now }
                .onChange(of: library.answerText) { _, _ in
                    if atBottom && library.answering { proxy.scrollTo("bottom", anchor: .bottom) }
                }
                .onChange(of: library.shownQuestion) { _, q in
                    if !q.isEmpty { withAnimation(reduceMotion ? nil : .easeOut) { proxy.scrollTo("bottom", anchor: .bottom) } }
                }
                .overlay(alignment: .bottom) {
                    if library.answering && !atBottom {
                        Button { withAnimation(reduceMotion ? nil : .easeOut) { proxy.scrollTo("bottom", anchor: .bottom) } } label: {
                            Label("Jump to latest", systemImage: "arrow.down").font(.footnote.weight(.semibold))
                                .padding(.horizontal, 14).padding(.vertical, 8).background(.regularMaterial, in: Capsule())
                        }
                        .buttonStyle(.plain)
                        .padding(.bottom, 8)
                    }
                }
            }
            Divider()
            composer
        }
        .popover(item: $openSource, arrowEdge: .bottom) { ref in
            SourcePopover(number: ref.number, hit: ref.hits[ref.number - 1])
        }
        .onReceive(NotificationCenter.default.publisher(for: .focusComposer)) { _ in composerFocused = true }
        .onChange(of: model.pendingQuestion, initial: true) { _, q in
            guard let q else { return }
            model.pendingQuestion = nil
            library.query = q
            send()
        }
        .onReceive(NotificationCenter.default.publisher(for: .readAnswerAloud)) { _ in
            let latest = library.answerText.isEmpty ? (turns.last?.answer ?? "") : library.answerText
            if !latest.isEmpty { reader.toggle(latest) }
        }
        .onDisappear { reader.stop() }
        .onChange(of: ready) { _, isReady in
            guard isReady, let q = queued else { return }
            queued = nil
            library.query = q
            Task { await library.ask() }
        }
        .onAppear { composerFocused = true }
    }

    // MARK: conversation

    @ViewBuilder private var conversation: some View {
        VStack(alignment: .leading, spacing: 16) {
            if library.notebooks.isEmpty {
                emptyLibrary
            } else {
                if !ready { ReadyBar(library: library) }
                if let q = queued {
                    HStack {
                        Label("Queued: \(q)", systemImage: "clock").lineLimit(2)
                        Spacer()
                        Button("Cancel") { queued = nil }
                    }
                    .font(.footnote).padding(12).background(.quaternary, in: RoundedRectangle(cornerRadius: 10))
                }
                ForEach(turns) { turn in pastTurn(turn) }
                if library.answering || !library.answerText.isEmpty || !library.answerStatus.isEmpty, !library.shownQuestion.isEmpty {
                    questionBubble(library.shownQuestion)
                } else if queued == nil && turns.isEmpty {
                    intro
                }
                if library.answering && library.answerText.isEmpty {
                    phases
                }
                if !library.answerText.isEmpty {
                    answerBody(library.answerText, sources: library.answerSources)
                    if !library.answering, !library.answerStatus.isEmpty {
                        Text(library.answerStatus).font(.caption2).foregroundStyle(.secondary)
                    }
                    if !library.answering { actions(for: library.answerText) }
                    sourcesRow(library.answerSources)
                    if !library.followUps.isEmpty {
                        VStack(alignment: .leading, spacing: 6) {
                            ForEach(library.followUps, id: \.self) { q in
                                Button(q) { library.query = q; send() }.font(.footnote).buttonStyle(.bordered)
                            }
                        }
                    }
                }
            }
            Color.clear.frame(height: 1).id("bottom")
        }
    }

    /// Real phases while waiting: "Searching notebook…", "Reading 6 passages…", "Writing…", with the sources as they arrive.
    private var phases: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack { ProgressView().controlSize(.small); Text(library.answerStatus.isEmpty ? "Working…" : library.answerStatus).font(.footnote).foregroundStyle(.secondary) }
            if !library.answerSources.isEmpty {
                ForEach(Array(library.answerSources.prefix(4).enumerated()), id: \.element.id) { _, h in
                    Label(h.filename, systemImage: "doc.text").font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
            }
            if waited > 30 { Text("This is taking longer than usual. You can stop (⌘.) and ask again.").font(.caption).foregroundStyle(.orange) }
        }
        .task(id: library.answering) {
            waited = 0
            while library.answering && library.answerText.isEmpty { try? await Task.sleep(for: .seconds(1)); waited += 1 }
        }
    }

    private var emptyLibrary: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("LocalBook").font(.title2.weight(.semibold))
            if model.paired {
                Text("Copying your notebooks from your Mac. The first one appears here as soon as it has arrived.")
                    .foregroundStyle(.secondary)
                if case .syncing(let line) = library.syncState { Text(line).font(.footnote).foregroundStyle(.secondary) }
                if case .failed(let why) = library.syncState {
                    Text(why).font(.footnote).foregroundStyle(.orange)
                    Button("Try again") { model.syncNow() }
                }
            } else {
                Text("Make a notebook and drop files on it, or pair with the Mac that holds your notebooks. Everything stays on your devices.")
                    .foregroundStyle(.secondary)
                HStack {
                    Button("New Notebook…") { model.prompts.text = ""; model.prompts.newNotebookSection = nil; model.prompts.newNotebook = true }.buttonStyle(.borderedProminent)
                    Button("Pair with a Mac…") { model.showPairing = true }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.top, 60)
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(currentTitle).font(.title3.bold())
            if let nb = model.selectedNotebook { NotebookSummaryLine(notebook: nb) }
            Text("Try asking:").font(.footnote).foregroundStyle(.secondary)
            ForEach(["What are the main points in this notebook?", "What changed most recently?", "Who is mentioned most often?"], id: \.self) { q in
                Button { library.query = q; send() } label: {
                    Text(q).font(.subheadline).multilineTextAlignment(.leading).frame(maxWidth: .infinity, alignment: .leading)
                        .padding(12).background(.quaternary, in: RoundedRectangle(cornerRadius: 10))
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.top, 40)
    }

    private func questionBubble(_ q: String) -> some View {
        Text(q).font(.body.weight(.medium)).textSelection(.enabled)
            .padding(.horizontal, 12).padding(.vertical, 8)
            .background(.tint.opacity(0.15), in: RoundedRectangle(cornerRadius: 12))
            .frame(maxWidth: .infinity, alignment: .trailing)
    }

    /// The answer, streaming without reflowing earlier paragraphs. `[n]` citations are anchors: a click opens the passage.
    private func answerBody(_ text: String, sources: [LibraryHit]) -> some View {
        Text(AnswerText.render(text)).textSelection(.enabled)
            .lineSpacing(3)
            .contentTransition(.opacity)
            .animation(reduceMotion ? nil : .easeOut(duration: 0.25), value: text)
            .environment(\.openURL, OpenURLAction { url in
                if let n = AnswerText.citation(in: url, count: sources.count) {
                    openSource = SourceRef(number: n, hits: sources); return .handled
                }
                return .systemAction
            })
    }

    /// A finished turn higher up in the conversation.
    @ViewBuilder private func pastTurn(_ turn: AskTurn) -> some View {
        questionBubble(turn.question)
        if turn.answer.isEmpty {
            if !turn.status.isEmpty { Text(turn.status).font(.footnote).foregroundStyle(.orange) }
        } else {
            answerBody(turn.answer, sources: turn.sources)
            actions(for: turn.answer)
            sourcesRow(turn.sources)
        }
        Divider().padding(.vertical, 4)
    }

    /// Under a finished answer: Copy, Share, Read aloud (⌥⌘R) with the phone's on-device voice.
    @ViewBuilder private func actions(for text: String) -> some View {
        HStack(spacing: 16) {
            Button { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(text, forType: .string) } label: { Label("Copy", systemImage: "doc.on.doc") }
            ShareLink(item: text) { Label("Share", systemImage: "square.and.arrow.up") }
            Image(systemName: "line.3.horizontal").foregroundStyle(.secondary).draggable(text).help("Drag the answer out as text")
            Button { reader.toggle(text) } label: {
                switch reader.state {
                case .preparing: HStack(spacing: 6) { ProgressView().controlSize(.mini); Text("Preparing voice…") }
                case .speaking: Label("Pause", systemImage: "pause.fill")
                case .paused: Label("Resume", systemImage: "play.fill")
                default: Label("Read Aloud", systemImage: "speaker.wave.2.fill")
                }
            }
            .help(Shortcuts.readAloud.help("Read the answer aloud"))
            if reader.isActive {
                Button { reader.stop() } label: { Label("Stop", systemImage: "stop.fill") }
                ProgressView(value: reader.fraction).frame(maxWidth: 80)
            }
            Spacer()
        }
        .font(.footnote).buttonStyle(.borderless)
        if case .failed(let why) = reader.state { Text(why).font(.caption).foregroundStyle(.orange) }
    }

    @ViewBuilder private func sourcesRow(_ sources: [LibraryHit]) -> some View {
        if !sources.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                Text("Sources").font(.footnote.weight(.semibold)).foregroundStyle(.secondary)
                FlowRow(spacing: 6) {
                    ForEach(Array(sources.enumerated()), id: \.element.id) { i, h in
                        Button { openSource = SourceRef(number: i + 1, hits: sources) } label: {
                            HStack(spacing: 6) {
                                Text("\(i + 1)").font(.caption.weight(.semibold)).frame(minWidth: 18, minHeight: 18)
                                    .background(.tint.opacity(0.15), in: Circle())
                                Text(h.filename).font(.caption).lineLimit(1)
                            }
                            .padding(.horizontal, 8).padding(.vertical, 4)
                            .background(.quaternary, in: Capsule())
                        }
                        .buttonStyle(.plain)
                        .help(h.filename)
                        .accessibilityLabel("Source \(i + 1): \(h.filename)")
                    }
                }
            }
        }
    }

    // MARK: composer

    /// The Ask field: two lines tall from the start, growing to six, then scrolling. Typing `/` first lists the
    /// actions in `SlashCommands`; the chosen one sits at the front as a chip and the text after it goes to that
    /// action (a Studio subject, a note's first line, a web search). Delete on an empty field removes the chip.
    private var composer: some View {
        @Bindable var lib = library
        return HStack(alignment: .bottom, spacing: 8) {
            Menu {
                Picker("Answer length", selection: $length) {
                    ForEach(AnswerLength.allCases) { Text($0.label).tag($0.rawValue) }
                }
                .pickerStyle(.inline)
            } label: { Image(systemName: "text.alignleft") }
                .menuStyle(.borderlessButton).fixedSize()
                .help("Answer length")
                .accessibilityLabel("Answer length")
                .onChange(of: length) { _, v in UserDefaults.standard.set(v, forKey: "answerLength") }
                .padding(.bottom, 4)
            HStack(alignment: .top, spacing: 8) {
                if let command {
                    CommandChip(command: command) { withAnimation(reduceMotion ? nil : .easeOut(duration: 0.15)) { self.command = nil } }
                        .padding(.top, 1)
                }
                TextField(placeholder, text: $lib.query, axis: .vertical)
                    .textFieldStyle(.plain)
                    .lineLimit(2...6)
                    .focused($composerFocused)
                    .onSubmit(send)
                    .disabled(library.notebooks.isEmpty)
                    .onChange(of: library.query) { _, text in queryChanged(text) }
                    .onKeyPress(.delete) { command != nil && library.query.isEmpty ? removeChip() : .ignored }
                    .onKeyPress(.upArrow) { menuShown ? moveMenu(-1) : .ignored }
                    .onKeyPress(.downArrow) { menuShown ? moveMenu(1) : .ignored }
                    .onKeyPress(.tab) { menuShown ? pickHighlighted() : .ignored }
                    .onKeyPress(.return) { menuShown ? pickHighlighted() : .ignored }
                    .onKeyPress(.escape) { menuShown ? closeMenu() : .ignored }
            }
            if library.answering {
                Button { library.stopAnswer() } label: { Image(systemName: "stop.circle.fill").font(.title2) }
                    .buttonStyle(.plain)
                    .help(Shortcuts.stopAnswer.help("Stop"))
                    .accessibilityLabel("Stop")
                    .padding(.bottom, 2)
            } else {
                Button(action: send) { Image(systemName: command == nil ? "arrow.up.circle.fill" : "return") }
                    .font(command == nil ? .title2 : .body.weight(.semibold))
                    .buttonStyle(.plain)
                    .disabled(!canSend)
                    .help(command == nil ? "Send (Return)" : "\(command!.title) (Return)")
                    .accessibilityLabel(command == nil ? "Send" : command!.title)
                    .padding(.bottom, 2)
            }
        }
        .padding(12)
        .background(.bar)
        .overlay(alignment: .topLeading) {
            if menuShown {
                SlashMenu(matches: matches, highlighted: highlighted) { pick($0) }
                    .alignmentGuide(.top) { $0[.bottom] + 4 }     // sits just above the bar, over the conversation
                    .padding(.leading, 40)
                    .transition(.opacity)
            }
        }
        .zIndex(1)
    }

    private var placeholder: String {
        if library.notebooks.isEmpty { return model.paired ? "Your notebooks are on their way" : "Make a notebook to ask" }
        if let command { return command.hint }
        return ready ? "Ask \(currentTitle), or type / for actions" : "Ask now, the answer starts when ready"
    }

    // MARK: slash menu

    /// The slash menu is open while the field holds `/` and a partial word, and no chip is in place yet.
    private var menuShown: Bool { command == nil && library.query.hasPrefix("/") && !library.query.contains(where: \.isNewline) && !matches.isEmpty }
    private var matches: [SlashCommand] {
        guard command == nil, library.query.hasPrefix("/") else { return [] }
        let word = library.query.dropFirst().prefix { !$0.isWhitespace }
        guard word.count == library.query.count - 1 || library.query.last == " " else { return [] }   // "/qui" or "/quiz "
        return SlashCommands.matching(String(word))
    }

    /// "/quiz " (a known word and a space) becomes the chip at once, so the menu never needs a click.
    private func queryChanged(_ text: String) {
        guard command == nil, text.hasPrefix("/"), text.last == " ", let parsed = SlashCommands.parse(text) else {
            highlighted = min(highlighted, max(matches.count - 1, 0)); return
        }
        pick(parsed.0, rest: parsed.1)
    }

    private func pick(_ cmd: SlashCommand, rest: String? = nil) {
        let text = rest ?? SlashCommands.parse(library.query).map { $0.1 } ?? ""
        withAnimation(reduceMotion ? nil : .easeOut(duration: 0.15)) { command = cmd }
        library.query = text
        highlighted = 0
        composerFocused = true
    }

    private func pickHighlighted() -> KeyPress.Result {
        guard matches.indices.contains(highlighted) else { return .ignored }
        pick(matches[highlighted]); return .handled
    }

    private func moveMenu(_ by: Int) -> KeyPress.Result {
        guard !matches.isEmpty else { return .ignored }
        highlighted = (highlighted + by + matches.count) % matches.count; return .handled
    }

    private func closeMenu() -> KeyPress.Result {
        library.query = ""; return .handled
    }

    private func removeChip() -> KeyPress.Result {
        withAnimation(reduceMotion ? nil : .easeOut(duration: 0.15)) { command = nil }; return .handled
    }

    private var canSend: Bool {
        !library.notebooks.isEmpty && (command != nil || !library.query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
    }

    private func send() {
        let q = library.query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !library.notebooks.isEmpty else { return }
        if let command {
            // The action runs with the text after the chip; the field is empty again, ready for the next ask.
            library.query = ""
            self.command = nil
            command.run(q, model)
            return
        }
        guard !q.isEmpty, !library.answering else { return }
        reader.stop()
        if !ready { queued = q; library.query = ""; return }
        library.query = q
        Task { await library.ask() }
    }
}

struct SourceRef: Identifiable { let id = UUID(); let number: Int; let hits: [LibraryHit] }

/// The chosen slash action, at the front of the Ask field: its symbol and name in a capsule, with an × to drop it.
struct CommandChip: View {
    let command: SlashCommand
    let remove: () -> Void
    var body: some View {
        HStack(spacing: 5) {
            Image(systemName: command.symbol).font(.caption.weight(.semibold))
            Text(command.title).font(.caption.weight(.semibold))
            Button(action: remove) { Image(systemName: "xmark").font(.caption2.weight(.bold)) }
                .buttonStyle(.plain).foregroundStyle(.secondary)
                .help("Remove (Delete on an empty field)")
                .accessibilityLabel("Remove \(command.title)")
        }
        .padding(.horizontal, 9).padding(.vertical, 4)
        .foregroundStyle(.tint)
        .background(.tint.opacity(0.14), in: Capsule())
        .fixedSize()
        .accessibilityElement(children: .combine)
    }
}

/// The small list that opens above the Ask field after `/`: actions, then agents once there are any. ↑↓ move,
/// Return or Tab picks, Esc closes; a click picks too.
struct SlashMenu: View {
    let matches: [SlashCommand]
    let highlighted: Int
    let pick: (SlashCommand) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            ForEach(SlashCommand.Group.allCases, id: \.self) { group in
                let rows = matches.enumerated().filter { $0.element.group == group }
                if !rows.isEmpty {
                    Text(group.rawValue).font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
                        .padding(.horizontal, 10).padding(.top, 6).padding(.bottom, 2)
                    ForEach(rows, id: \.element.id) { i, cmd in
                        Button { pick(cmd) } label: {
                            HStack(spacing: 8) {
                                Image(systemName: cmd.symbol).frame(width: 18).foregroundStyle(i == highlighted ? .primary : .secondary)
                                Text(cmd.title)
                                Spacer(minLength: 16)
                                Text("/" + cmd.name).font(.callout).monospaced().foregroundStyle(.tertiary)
                            }
                            .font(.callout)
                            .padding(.horizontal, 10).padding(.vertical, 5)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(i == highlighted ? AnyShapeStyle(.tint.opacity(0.16)) : AnyShapeStyle(.clear), in: RoundedRectangle(cornerRadius: 6))
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
        .padding(4)
        .frame(width: 300)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 10))
        .overlay(RoundedRectangle(cornerRadius: 10).strokeBorder(.separator))
        .shadow(color: .black.opacity(0.12), radius: 10, y: 4)
        .accessibilityLabel("Actions")
    }
}

/// The cited passage, in a popover; the window button (⌘↓) opens the whole source in the source viewer.
struct SourcePopover: View {
    let number: Int
    let hit: LibraryHit
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("Source \(number)").font(.headline)
                Spacer()
                Text(hit.filename).font(.footnote).foregroundStyle(.secondary).lineLimit(1)
                SourceOpenButton(key: SourceWindowKey(notebookId: hit.notebookId, sourceId: hit.sourceId, name: hit.filename))
                    .labelStyle(.iconOnly).buttonStyle(.borderless).help("Open in Window (⌘↓)")
            }
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    if hit.sourceId.hasPrefix(LocalNotes.prefix), let image = NSImage(contentsOfFile: LibraryModel.photoURL(hit.sourceId).path) {
                        Image(nsImage: image).resizable().scaledToFit().clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                    Text(hit.text).textSelection(.enabled)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .padding(16)
        .frame(width: 440)
        .frame(minHeight: 120, maxHeight: 420)
    }
}

/// One calm line while the Mac gets ready; the question field stays usable.
struct ReadyBar: View {
    let library: LibraryModel
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let why = failure {
                Label(why, systemImage: "exclamationmark.triangle.fill").font(.footnote).foregroundStyle(.orange)
            } else {
                Text("Getting ready — about a minute the first time. You can ask now; the answer starts when ready.").font(.footnote).foregroundStyle(.secondary)
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

/// Lays its children out left to right, wrapping to the next line when the width runs out.
struct FlowRow: Layout {
    var spacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 600
        var x: CGFloat = 0, y: CGFloat = 0, rowHeight: CGFloat = 0
        for s in subviews {
            let size = s.sizeThatFits(.unspecified)
            if x > 0, x + size.width > width { x = 0; y += rowHeight + spacing; rowHeight = 0 }
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
        return CGSize(width: width, height: y + rowHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, rowHeight: CGFloat = 0
        for s in subviews {
            let size = s.sizeThatFits(.unspecified)
            if x > bounds.minX, x + size.width > bounds.maxX { x = bounds.minX; y += rowHeight + spacing; rowHeight = 0 }
            s.place(at: CGPoint(x: x, y: y), proposal: .unspecified)
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
    }
}


/// "12 sources · 1,480 passages" under the notebook's name, with the way to the Sources pane: what is actually here.
struct NotebookSummaryLine: View {
    @Environment(AppModel.self) private var model
    let notebook: LibraryNotebookInfo
    @State private var sourceCount: Int?

    var body: some View {
        HStack(spacing: 10) {
            if notebook.chunks == 0 {
                Text("Nothing copied yet").font(.footnote).foregroundStyle(.secondary)
            } else {
                Text("\(sourceCount.map { "\($0) source\($0 == 1 ? "" : "s") · " } ?? "")\(notebook.chunks.formatted()) passages")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            Button("Show Sources") { model.selection = .sources }.font(.footnote).buttonStyle(.link)
        }
        .task(id: notebook.id) { sourceCount = await model.library.sources(in: notebook.id).count }
        .onReceive(NotificationCenter.default.publisher(for: .librarySynced)) { _ in
            Task { sourceCount = await model.library.sources(in: notebook.id).count }
        }
    }
}
