import AppKit
import AVFoundation
import LocalBookCore
import LocalBookEngines
import Observation
import Speech
import SwiftUI
import UniformTypeIdentifiers
@preconcurrency import Vision

/// Dictation: speech to text on this Mac (on-device recognition where the language supports it). The phone's
/// `Dictation` without the audio session.
@MainActor @Observable
final class Dictation {
    private(set) var listening = false
    private(set) var failure: String?
    private let recognizer = SFSpeechRecognizer(locale: Locale.current)
    private let engine = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var onText: (@MainActor (String) -> Void)?

    func toggle(onText: @escaping @MainActor (String) -> Void) {
        if listening { stop(); return }
        failure = nil
        Self.askPermissions { [self] problem in
            if let problem { failure = problem } else { start(onText) }
        }
    }

    /// Written outside the main actor because the system calls these completions on its own queues.
    private nonisolated static func askPermissions(_ done: @escaping @MainActor (String?) -> Void) {
        SFSpeechRecognizer.requestAuthorization { status in
            guard status == .authorized else {
                Task { @MainActor in done("Allow Speech Recognition for LocalBook in System Settings › Privacy & Security to dictate.") }
                return
            }
            AVAudioApplication.requestRecordPermission { ok in
                Task { @MainActor in done(ok ? nil : "Allow the microphone for LocalBook in System Settings › Privacy & Security to dictate.") }
            }
        }
    }

    private func start(_ onText: @escaping @MainActor (String) -> Void) {
        guard let recognizer, recognizer.isAvailable else { failure = "Dictation isn't available right now."; return }
        guard recognizer.supportsOnDeviceRecognition else {
            failure = "Dictation needs on-device speech for your language. Turn on Dictation in System Settings › Keyboard, then try again."
            return
        }
        do {
            let req = SFSpeechAudioBufferRecognitionRequest()
            req.shouldReportPartialResults = true
            req.requiresOnDeviceRecognition = true
            request = req
            self.onText = onText
            let input = engine.inputNode
            input.removeTap(onBus: 0)
            input.installTap(onBus: 0, bufferSize: 4096, format: input.outputFormat(forBus: 0), block: Self.tapBlock(req))
            engine.prepare()
            try engine.start()
            listening = true
            task = recognizer.recognitionTask(with: req, resultHandler: Self.resultHandler(self))
        } catch { failure = "Couldn't start dictation: \(error.localizedDescription)"; stop() }
    }

    private nonisolated static func tapBlock(_ req: SFSpeechAudioBufferRecognitionRequest) -> @Sendable (AVAudioPCMBuffer, AVAudioTime) -> Void {
        nonisolated(unsafe) let sink = req
        return { buffer, _ in sink.append(buffer) }
    }

    private nonisolated static func resultHandler(_ owner: Dictation) -> @Sendable (SFSpeechRecognitionResult?, Error?) -> Void {
        return { result, error in
            let text = result?.bestTranscription.formattedString
            let final = result?.isFinal == true
            let message = error?.localizedDescription
            Task { @MainActor in owner.heard(text: text, final: final, error: message) }
        }
    }

    private func heard(text: String?, final: Bool, error: String?) {
        guard listening else { return }
        if let text { onText?(text) }
        if let error {
            if text?.isEmpty != false { failure = "Dictation stopped: \(error)" }
            stop()
        } else if final { stop() }
    }

    func stop() {
        guard listening || task != nil else { return }
        engine.stop()
        engine.inputNode.removeTap(onBus: 0)
        request?.endAudio()
        task?.cancel()
        request = nil; task = nil; onText = nil; listening = false
    }
}

/// Turns an audio file or a video into text on this Mac with the same on-device recognizer the meeting recorder uses.
@MainActor @Observable
final class MediaTranscriber {
    static let shared = MediaTranscriber()

    private(set) var busy = false
    private(set) var status = ""
    private(set) var progress = 0.0
    private var stopper = LongTranscriber.Stopper()

    func cancel() { stopper.stop() }

    func transcribe(_ source: URL) async throws -> String {
        guard !busy else { throw LongTranscriber.Failure(text: "Already transcribing something.") }
        busy = true; progress = 0; status = "Getting ready…"; stopper = LongTranscriber.Stopper()
        let hold = KeepAwake.begin(reason: "Transcribing")
        defer { busy = false; status = ""; hold.end() }

        let asset = AVURLAsset(url: source)
        guard try await !asset.loadTracks(withMediaType: .audio).isEmpty else { throw LongTranscriber.Failure(text: "There is no sound in that file.") }
        let total = max(1, try await asset.load(.duration).seconds)

        var audioURL = source
        var extracted: URL?
        defer { if let extracted { try? FileManager.default.removeItem(at: extracted) } }
        if ["mov", "mp4", "m4v"].contains(source.pathExtension.lowercased()) {
            status = "Taking the sound from the video…"
            let temp = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".m4a")
            guard let export = AVAssetExportSession(asset: asset, presetName: AVAssetExportPresetAppleM4A) else { throw LongTranscriber.Failure(text: "Couldn't read the video's sound.") }
            try await export.export(to: temp, as: .m4a)
            audioURL = temp; extracted = temp
        }

        status = "Transcribing…"
        let words = try await LongTranscriber.transcribe(audioURL, total: total, stopper: stopper,
            step: { text in Task { @MainActor in MediaTranscriber.shared.status = text } },
            progress: { fraction in Task { @MainActor in MediaTranscriber.shared.progress = fraction } })
        var text = ""
        for w in words { text = LongTranscriber.join(text, w.text) }
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

/// Reads the text in a picture (Vision), so a photographed page or whiteboard becomes searchable.
enum PhotoText {
    static func read(_ cg: CGImage) async -> String {
        await withCheckedContinuation { cont in
            DispatchQueue.global(qos: .userInitiated).async {
                let req = VNRecognizeTextRequest()
                req.recognitionLevel = .accurate
                req.usesLanguageCorrection = true
                try? VNImageRequestHandler(cgImage: cg).perform([req])
                let lines = req.results?.compactMap { $0.topCandidates(1).first?.string } ?? []
                cont.resume(returning: lines.joined(separator: "\n"))
            }
        }
    }
}

extension NSImage {
    /// A bitmap no larger than `side` on its longer edge.
    func scaledDown(toLongSide side: CGFloat) -> CGImage? {
        guard let cg = cgImage(forProposedRect: nil, context: nil, hints: nil) else { return nil }
        let longest = CGFloat(max(cg.width, cg.height))
        guard longest > side else { return cg }
        let f = side / longest
        let w = Int(CGFloat(cg.width) * f), h = Int(CGFloat(cg.height) * f)
        guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
        ctx.interpolationQuality = .high
        ctx.draw(cg, in: CGRect(x: 0, y: 0, width: w, height: h))
        return ctx.makeImage()
    }
}

/// Write, dictate, or add a picture or a recording to a note in a notebook. File › New Note (⇧⌘N); Record › Voice
/// Note (⇧⌘V) opens it with dictation already listening. With `editing`, the same sheet rewrites a note made here.
///
/// The text is Markdown, styled in place by `NoteTextView` (what the old app's BlockNote editor saved for search), with
/// a formatting bar and the usual shortcuts; what is saved is exactly what was typed.
struct NoteEditor: View {
    let notebookId: String
    var startDictating = false
    var startText = ""
    var editing: LibraryStore.LocalNote? = nil
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var title = ""
    @State private var text = ""
    @State private var bridge = NoteEditorBridge()
    @State private var dictation = Dictation()
    @State private var dictationBase = ""
    @State private var reading = false
    @State private var saving = false
    @State private var fromPhoto = false
    @State private var photo: (image: CGImage, jpeg: Data)?
    @State private var transcriber = MediaTranscriber.shared
    @State private var pickMedia = false
    @State private var pickPicture = false
    @State private var error: String?

    private var library: LibraryModel { model.library }
    private var notebookTitle: String { library.notebooks.first { $0.id == notebookId }?.title ?? "Notebook" }
    private var words: Int { MarkdownEdit.wordCount(text) }

    var body: some View {
        VStack(spacing: 0) {
            TextField("Title (optional)", text: $title)
                .textFieldStyle(.plain).font(.title2.weight(.semibold))
                .padding(.horizontal, 20).padding(.top, 16).padding(.bottom, 10)
            formatBar
            Divider()
            NoteTextView(text: $text, bridge: bridge, placeholder: "Write, dictate, or add a picture or a recording…")
                .frame(minHeight: 240, maxHeight: .infinity)
            Divider()
            extras
            HStack(spacing: 10) {
                Button {
                    dictationBase = text.isEmpty ? "" : text + "\n"
                    dictation.toggle { spoken in text = dictationBase + spoken }
                } label: { Label(dictation.listening ? "Stop Dictating" : "Dictate", systemImage: dictation.listening ? "stop.circle.fill" : "mic.fill") }
                    .tint(dictation.listening ? .red : nil)
                Button { dictation.stop(); pickPicture = true } label: { Label("Picture…", systemImage: "photo") }
                Button { pickMedia = true } label: { Label("Audio or Video…", systemImage: "waveform") }.disabled(transcriber.busy)
                Spacer()
                Text("\(words) \(words == 1 ? "word" : "words")").font(.footnote).foregroundStyle(.secondary).monospacedDigit()
                Button("Cancel") { dictation.stop(); dismiss() }.keyboardShortcut(.cancelAction)
                Button(editing == nil ? "Save" : "Save Changes") { Task { await save() } }
                    .buttonStyle(.borderedProminent).keyboardShortcut("s", modifiers: .command)
                    .disabled(saving || (text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && photo == nil))
            }
            .padding(12)
        }
        .frame(minWidth: 660, idealWidth: 760, minHeight: 560, idealHeight: 680)
        .fileImporter(isPresented: $pickMedia, allowedContentTypes: [.audio, .movie]) { result in
            guard case .success(let picked) = result else { return }
            let scoped = picked.startAccessingSecurityScopedResource()
            defer { if scoped { picked.stopAccessingSecurityScopedResource() } }
            let dest = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + "." + picked.pathExtension)
            guard (try? FileManager.default.copyItem(at: picked, to: dest)) != nil else { error = "Couldn't open that file."; return }
            Task { await transcribe(dest, label: picked.deletingPathExtension().lastPathComponent) }
        }
        .fileImporter(isPresented: $pickPicture, allowedContentTypes: [.image]) { result in
            guard case .success(let picked) = result else { return }
            let scoped = picked.startAccessingSecurityScopedResource()
            defer { if scoped { picked.stopAccessingSecurityScopedResource() } }
            guard let image = NSImage(contentsOf: picked), let small = image.scaledDown(toLongSide: 1600),
                  let jpeg = NSBitmapImageRep(cgImage: small).representation(using: .jpeg, properties: [.compressionFactor: 0.8]) else { error = "Couldn't open that picture."; return }
            photo = (small, jpeg); fromPhoto = true
            reading = true
            Task {
                let found = await PhotoText.read(small)
                reading = false
                if !found.isEmpty { text = text.isEmpty ? found : text + "\n\n" + found }
            }
        }
        .task {
            if let editing { title = editing.title; text = editing.text } else { text = startText }
            if startDictating { dictation.toggle { spoken in text = spoken } }
        }
        .onDisappear { dictation.stop() }
    }

    /// Headings, emphasis, lists, quote, code and link. Shortcuts as the common editors have them: ⌘B, ⌘I, ⌘K,
    /// ⇧⌘7 numbered, ⇧⌘8 bulleted, ⇧⌘9 checklist.
    private var formatBar: some View {
        HStack(spacing: 2) {
            Menu {
                Button("Heading 1") { bridge.perform(.heading(1)) }
                Button("Heading 2") { bridge.perform(.heading(2)) }
                Button("Heading 3") { bridge.perform(.heading(3)) }
                Divider()
                Button("Paragraph") { bridge.perform(.heading(0)) }
            } label: { Image(systemName: "textformat.size") }
                .menuStyle(.borderlessButton).frame(width: 44).help("Heading")
            formatButton("bold", "Bold (⌘B)") { bridge.perform(.bold) }.keyboardShortcut("b", modifiers: .command)
            formatButton("italic", "Italic (⌘I)") { bridge.perform(.italic) }.keyboardShortcut("i", modifiers: .command)
            formatButton("chevron.left.forwardslash.chevron.right", "Code") { bridge.perform(.code) }
            Divider().frame(height: 16).padding(.horizontal, 4)
            formatButton("list.bullet", "Bulleted list (⇧⌘8)") { bridge.perform(.bullets) }.keyboardShortcut("8", modifiers: [.command, .shift])
            formatButton("list.number", "Numbered list (⇧⌘7)") { bridge.perform(.numbers) }.keyboardShortcut("7", modifiers: [.command, .shift])
            formatButton("checklist", "Checklist (⇧⌘9)") { bridge.perform(.checklist) }.keyboardShortcut("9", modifiers: [.command, .shift])
            formatButton("text.quote", "Quote") { bridge.perform(.quote) }
            Divider().frame(height: 16).padding(.horizontal, 4)
            formatButton("link", "Link (⌘K)") { bridge.link() }.keyboardShortcut("k", modifiers: .command)
            Spacer()
            Text(notebookTitle).font(.footnote).foregroundStyle(.secondary).lineLimit(1)
        }
        .buttonStyle(.borderless)
        .padding(.horizontal, 14).padding(.bottom, 8)
    }

    private func formatButton(_ symbol: String, _ help: String, action: @escaping () -> Void) -> some View {
        Button(action: action) { Image(systemName: symbol).frame(width: 26, height: 22) }.help(help)
    }

    @ViewBuilder private var extras: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let photo {
                HStack(spacing: 12) {
                    Image(decorative: photo.image, scale: 1).resizable().scaledToFill().frame(width: 48, height: 48).clipShape(RoundedRectangle(cornerRadius: 8))
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Picture attached").font(.subheadline)
                        Text(library.vision != nil ? "Searchable by what it shows." : "Turn on picture search in Settings › Models to find it by what it shows.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Remove", role: .destructive) { self.photo = nil }.font(.footnote)
                }
            }
            if reading { HStack { ProgressView().controlSize(.small); Text("Reading the picture…").font(.footnote) } }
            if transcriber.busy {
                HStack {
                    Text(transcriber.status).font(.footnote)
                    ProgressView(value: min(1, max(0.02, transcriber.progress)))
                    Button("Stop", role: .cancel) { transcriber.cancel() }.font(.footnote)
                }
            }
            if let msg = error ?? dictation.failure { Text(msg).font(.footnote).foregroundStyle(.orange) }
            if editing == nil {
                Text("Saved in “\(notebookTitle)” and searchable straight away. It is sent to your paired Mac when it is reachable and comes back through sync.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        .padding(.horizontal, 14).padding(.vertical, 8)
    }

    private func transcribe(_ url: URL, label: String) async {
        defer { try? FileManager.default.removeItem(at: url) }
        error = nil
        do {
            let words = try await transcriber.transcribe(url)
            guard !words.isEmpty else { error = "No speech was found in that."; return }
            text = text.isEmpty ? words : text + "\n\n" + words
            if title.trimmingCharacters(in: .whitespaces).isEmpty {
                title = label + " " + Date().formatted(date: .abbreviated, time: .shortened)
            }
        } catch { self.error = error.localizedDescription }
    }

    private func save() async {
        dictation.stop(); saving = true; defer { saving = false }
        do {
            let kind = editing?.kind ?? (fromPhoto ? "image" : "note")
            try await library.saveNoteEdit(notebook: notebookId, title: title, text: text, kind: kind,
                                           photo: photo.map { ($0.image, $0.jpeg) }, replacing: editing?.id)
            NotificationCenter.default.post(name: .librarySynced, object: nil)
            dismiss()
        } catch { self.error = "Couldn't save: \(error.localizedDescription)" }
    }
}

/// Notes: the notes made on this Mac (every notebook) on the left, the open note on the right. The editor and the
/// canvas from the desktop app arrive in Phase 3 and 5; this slice is writing and reading them.
struct NotesPane: View {
    @Environment(AppModel.self) private var model
    @State private var notes: [LibraryStore.LocalNote] = []
    @State private var sent: Set<String> = []
    @State private var selected: String?
    @State private var writing = false
    @State private var editingNote: LibraryStore.LocalNote?
    @State private var filter = ""
    @State private var startText = ""
    @State private var confirmDelete: LibraryStore.LocalNote?
    /// The old app's Canvas notes for the current notebook, moved over by the import. Read here, kept as they were.
    @State private var imported: [LibraryStore.ImportedNote] = []

    private var library: LibraryModel { model.library }
    private var opened: LibraryStore.LocalNote? { notes.first { $0.id == selected } }
    private var shown: [LibraryStore.LocalNote] {
        let q = filter.trimmingCharacters(in: .whitespaces)
        return q.isEmpty ? notes : notes.filter { $0.title.localizedCaseInsensitiveContains(q) || $0.text.localizedCaseInsensitiveContains(q) }
    }
    private var shownImported: [LibraryStore.ImportedNote] {
        let q = filter.trimmingCharacters(in: .whitespaces)
        return q.isEmpty ? imported : imported.filter { $0.title.localizedCaseInsensitiveContains(q) || $0.text.localizedCaseInsensitiveContains(q) }
    }
    private var openedImported: LibraryStore.ImportedNote? { imported.first { $0.id == selected } }

    var body: some View {
        HSplitView {
            list.frame(minWidth: 260, idealWidth: 320, maxWidth: 460)
            detail.frame(minWidth: 320, maxWidth: .infinity, maxHeight: .infinity)
        }
        .task { await load(); await library.flushNotes(); await load() }
        .onReceive(NotificationCenter.default.publisher(for: .librarySynced)) { _ in Task { await load() } }
        .onChange(of: library.scopeToNotebook) { _, _ in Task { await load() } }
        .onReceive(NotificationCenter.default.publisher(for: .newNote)) { note in
            // Only the main window's forwarded request: it has switched to this pane and may carry text to start from.
            guard let request = note.object as? NewNoteRequest, library.scopeToNotebook != nil else { return }
            startText = request.text ?? ""
            writing = true
        }
        .sheet(isPresented: $writing) {
            if let id = library.scopeToNotebook { NoteEditor(notebookId: id, startText: startText).environment(model) }
        }
        .sheet(item: $editingNote) { n in NoteEditor(notebookId: n.notebookId, editing: n).environment(model) }
        .confirmationDialog("Delete this note?", isPresented: Binding(get: { confirmDelete != nil }, set: { if !$0 { confirmDelete = nil } }), titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                if let n = confirmDelete { Task { await library.removeNote(n); await load() } }
                confirmDelete = nil
            }
        } message: { Text("The note is removed from this Mac. If it already reached your paired Mac, it stays there.") }
    }

    private var list: some View {
        VStack(spacing: 0) {
            HStack {
                Text("My Notes").font(.headline)
                Spacer()
                Button { writing = true } label: { Label("New Note", systemImage: "square.and.pencil") }
                    .disabled(library.scopeToNotebook == nil)
                    .help("New note in the current notebook (⇧⌘N)")
            }
            .padding(12)
            HStack(spacing: 6) {
                Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                TextField("Filter notes", text: $filter).textFieldStyle(.plain)
                if !filter.isEmpty { Button { filter = "" } label: { Image(systemName: "xmark.circle.fill") }.buttonStyle(.borderless).foregroundStyle(.secondary) }
            }
            .padding(.horizontal, 12).padding(.bottom, 8)
            Divider()
            List(selection: $selected) {
                if let why = library.noteSendProblem { Label(why, systemImage: "wifi.slash").font(.footnote).foregroundStyle(.orange) }
                if notes.isEmpty { Text("No notes yet. Click New Note, or press ⇧⌘N.").font(.callout).foregroundStyle(.secondary) }
                else if shown.isEmpty, shownImported.isEmpty { Text("Nothing matches “\(filter)”.").font(.callout).foregroundStyle(.secondary) }
                ForEach(shown) { n in
                    HStack(alignment: .top, spacing: 10) {
                        if n.hasPicture, let image = NSImage(contentsOfFile: LibraryModel.photoURL(n.id).path) {
                            Image(nsImage: image).resizable().scaledToFill().frame(width: 40, height: 40).clipShape(RoundedRectangle(cornerRadius: 6))
                        } else {
                            Image(systemName: "note.text").foregroundStyle(.tint).frame(width: 22)
                        }
                        VStack(alignment: .leading, spacing: 2) {
                            Text(n.title).lineLimit(1)
                            Text((library.notebooks.first { $0.id == n.notebookId }?.title ?? "") + (sent.contains(n.id) ? " · on your paired Mac" : " · waiting to send"))
                                .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                        }
                    }
                    .tag(n.id)
                    .contextMenu {
                        Button("Edit…") { editingNote = n }
                        Button("Delete…", role: .destructive) { confirmDelete = n }
                    }
                }
                if !shownImported.isEmpty {
                    Section("From the LocalBook on this Mac") {
                        ForEach(shownImported) { n in
                            HStack(alignment: .top, spacing: 10) {
                                Image(systemName: "note.text").foregroundStyle(.secondary).frame(width: 22)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(n.title).lineLimit(1)
                                    Text(n.created.prefix(10)).font(.caption).foregroundStyle(.secondary)
                                }
                            }
                            .tag(n.id)
                        }
                    }
                }
            }
            .listStyle(.inset)
            .onDeleteCommand { if let n = opened { confirmDelete = n } }
        }
    }

    @ViewBuilder private var detail: some View {
        if let n = opened {
            VStack(spacing: 0) {
                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 1) {
                        Text(n.title).font(.headline).lineLimit(1)
                        Text(library.notebooks.first { $0.id == n.notebookId }?.title ?? "").font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button { editingNote = n } label: { Image(systemName: "pencil") }
                        .help("Edit (⌘E)")
                        .keyboardShortcut("e", modifiers: .command)
                    Button { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(n.text, forType: .string) } label: { Image(systemName: "doc.on.doc") }.help("Copy text")
                    ShareLink(item: n.text) { Image(systemName: "square.and.arrow.up") }.help("Share")
                }
                .buttonStyle(.borderless)
                .padding(12)
                Divider()
                ScrollView {
                    VStack(alignment: .leading, spacing: 12) {
                        if n.hasPicture, let image = NSImage(contentsOfFile: LibraryModel.photoURL(n.id).path) {
                            Image(nsImage: image).resizable().scaledToFit().frame(maxHeight: 360).clipShape(RoundedRectangle(cornerRadius: 10))
                        }
                        Text(AnswerText.render(n.text)).textSelection(.enabled).lineSpacing(3)
                    }
                    .frame(maxWidth: 760, alignment: .leading).padding(20).frame(maxWidth: .infinity)
                }
            }
            .id(n.id)
        } else if let n = openedImported {
            VStack(spacing: 0) {
                HStack(spacing: 12) {
                    VStack(alignment: .leading, spacing: 1) {
                        Text(n.title).font(.headline).lineLimit(1)
                        Text("From the LocalBook on this Mac · " + (library.notebooks.first { $0.id == n.notebookId }?.title ?? "")).font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(n.text, forType: .string) } label: { Image(systemName: "doc.on.doc") }.help("Copy text")
                    ShareLink(item: n.text) { Image(systemName: "square.and.arrow.up") }.help("Share")
                }
                .buttonStyle(.borderless)
                .padding(12)
                Divider()
                ScrollView {
                    Text(AnswerText.render(n.text)).textSelection(.enabled).lineSpacing(3)
                        .frame(maxWidth: 760, alignment: .leading).padding(20).frame(maxWidth: .infinity)
                }
            }
            .id(n.id)
        } else {
            ContentUnavailableView {
                Label("Notes", systemImage: "note.text")
            } description: {
                Text(notes.isEmpty ? "Notes you write, dictate or photograph on this Mac appear here." : "Choose a note to read it.")
            }
        }
    }

    private func load() async {
        notes = await library.localNotes()
        imported = await library.importedNotes(in: library.scopeToNotebook)
        var s: Set<String> = []
        for n in notes where await library.noteSentSourceId(n.id) != nil { s.insert(n.id) }
        sent = s
        if selected != nil, !notes.contains(where: { $0.id == selected }), !imported.contains(where: { $0.id == selected }) { selected = nil }
    }
}

/// File › New Note or an intent, forwarded by the main window once the Notes pane is showing.
struct NewNoteRequest { let text: String? }
