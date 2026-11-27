import AppKit
import Combine
import LocalBookCore
import LocalBookEngines
import SwiftUI
import UniformTypeIdentifiers

/// Sidebar on the left, the selected destination on the right. One idea per window: ask a notebook.
struct MainWindow: View {
    @Environment(AppModel.self) private var model
    @State private var columns = NavigationSplitViewVisibility.all

    var body: some View {
        @Bindable var model = model
        NavigationSplitView(columnVisibility: $columns) {
            Sidebar(selection: $model.selection)
                .navigationSplitViewColumnWidth(min: 200, ideal: 240, max: 320)
        } detail: {
            Group {
                switch model.selection {
                case .notebook, nil:
                    AskView()
                case .source(let key):
                    SourceScreen(key: key).id(key)
                case .sources:
                    SourcesPane()
                case .studio:
                    StudioPane()
                case .meetings:
                    MeetingsPane()
                case .notes:
                    NotesPane()
                case .insights:
                    InsightsPane()
                case .canvas:
                    CanvasPane()
                case .constellation:
                    ConstellationPane()
                }
            }
            .acceptsDrops(for: model.currentNotebookId)
            // ⌘V with files on the clipboard (copied in the Finder) adds them to the notebook on screen; a text
            // field that has the focus still takes its own paste first.
            .onPasteCommand(of: [.fileURL, .url]) { providers in pasteFiles(providers) }
            .safeAreaInset(edge: .bottom, spacing: 0) { IntakeStrip() }      // below the pane, never over the Ask field
            .inspector(isPresented: $model.showInspector) {
                InspectorView()
                    .inspectorColumnWidth(min: 220, ideal: 280, max: 400)
            }
        }
        .navigationTitle(model.title)
        .navigationSubtitle(model.readiness)
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                if model.currentNotebookId != nil {
                    Button {
                        model.selection = model.selection == .sources ? .notebook(model.currentNotebookId!) : .sources
                    } label: {
                        Label("Sources", systemImage: "doc.text.magnifyingglass")
                    }
                    .help(Shortcuts.showSources.help("The notebook's sources"))
                }
                if model.paired {
                    Button {
                        model.syncNow()
                    } label: {
                        if case .syncing = model.library.syncState {
                            ProgressView().controlSize(.small)
                        } else {
                            Label("Sync", systemImage: "arrow.triangle.2.circlepath")
                        }
                    }
                    .help(Shortcuts.syncNow.help("Sync with your Mac now"))
                } else {
                    Button {
                        model.showPairing = true
                    } label: {
                        Label("Pair with a Mac", systemImage: "laptopcomputer.and.arrow.down")
                    }
                    .help("Pair with the Mac that holds your notebooks")
                }
            }
        }
        .modifier(EditPromptsHost())
        .sheet(isPresented: $model.showPairing) { PairingSheet(model: model) }
        .sheet(isPresented: $model.showImport) { ImportSheet() }
        .sheet(isPresented: $model.showFindSources) { FindSourcesSheet() }
        .sheet(isPresented: $model.showShortcuts) { ShortcutsSheet() }
        .sheet(isPresented: Binding(get: { !model.filesToPlace.isEmpty }, set: { if !$0 { model.filesToPlace = [] } })) { PlaceFilesSheet() }
        .sheet(isPresented: Binding(get: { model.linkDraft != nil }, set: { if !$0 { model.linkDraft = nil } })) { AddLinkSheet() }
        .sheet(isPresented: Binding(get: { model.folderToWatch != nil }, set: { if !$0 { model.folderToWatch = nil } })) { WatchFolderSheet() }
        .sheet(isPresented: $model.showVoiceNote) {
            if let id = model.currentNotebookId { NoteEditor(notebookId: id, startDictating: true).environment(model) }
        }
        .task { LaunchRequests.shared.windowReady(); await model.start() }
        .onOpenURL { url in LaunchRequests.shared.open(url) }
        .onChange(of: model.library.scopeToNotebook) { _, _ in model.syncSelectionToScope() }
        .onReceive(Self.menuActions) { note in handle(note) }
    }

    /// Every menu item speaks through one notification stream, so the body stays a short modifier chain.
    @MainActor private static let menuActions = Publishers.MergeMany(
        [Notification.Name.syncNow, .stopAnswer, .pairWithMac, .focusComposer, .showSources, .showStudio, .showInsights, .makeInsight, .writingAssistant, .showMeetings,
         .startMeetingRecording, .newNote, .voiceNote, .toggleInspector, .makeStudio, .askQuestion, .newNotebook, .notebookRenumbered, .importLibrary,
         .addSources, .openFiles, .addWebPage, .watchFolder, .exportNotebook, .findSources, .showShortcuts]
            .map { NotificationCenter.default.publisher(for: $0) }
    )

    private func handle(_ note: Notification) {
        switch note.name {
        case .syncNow: model.syncNow()
        case .newNotebook: model.prompts.text = ""; model.prompts.newNotebookSection = nil; model.prompts.newNotebook = true
        case .notebookRenumbered:
            if let from = note.userInfo?["from"] as? String, let to = note.userInfo?["to"] as? String { model.notebookRenumbered(from: from, to: to) }
        case .stopAnswer: model.library.stopAnswer()
        case .pairWithMac: model.showPairing = true
        case .importLibrary: model.showImport = true
        case .focusComposer: model.showAsk()
        case .showSources: if model.currentNotebookId != nil { model.selection = .sources }
        case .showStudio: model.selection = .studio
        case .showInsights: model.selection = .insights
        case .showCanvas: model.selection = .canvas
        case .showConstellation: model.selection = .constellation
        case .makeInsight: model.selection = .insights; model.insightRequest = Insight.Kind(rawValue: note.object as? String ?? "")
        case .writingAssistant: model.selection = .insights; model.writingRequest = true
        case .showMeetings, .startMeetingRecording: model.selection = .meetings
        case .newNote:
            // The menu and the intents post it bare (or with text to start from); the Notes pane answers only the
            // forwarded request, sent once the pane is on screen.
            guard !(note.object is NewNoteRequest), model.currentNotebookId != nil else { return }
            let request = NewNoteRequest(text: note.object as? String)
            model.selection = .notes
            Task { @MainActor in NotificationCenter.default.post(name: .newNote, object: request) }
        case .askQuestion:
            if let q = note.object as? String, !q.isEmpty { model.pendingQuestion = q; model.showAsk() }
        case .voiceNote: if model.currentNotebookId != nil { model.showVoiceNote = true }
        case .toggleInspector: model.showInspector.toggle()
        case .addSources: chooseFiles()
        case .openFiles: if let url = note.object as? URL { model.addFiles([url], ask: true) }
        case .addWebPage: model.linkDraft = ""
        case .findSources: model.showFindSources = true
        case .showShortcuts: model.showShortcuts = true
        case .watchFolder: chooseFolder()
        case .exportNotebook:
            if let nb = model.selectedNotebook { Task { if let problem = await Exporter.exportNotebook(nb, library: model.library) { model.prompts.problem = problem } } }
        case .makeStudio:
            if let raw = note.object as? String, let kind = StudioItem.Kind(rawValue: raw) { model.make(kind) }
        default: break
        }
    }
}

extension MainWindow {
    /// File › Add Sources… (⌘O): the standard open panel, then the drop's path into the notebook on screen.
    @MainActor fileprivate func chooseFiles() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true; panel.canChooseDirectories = false; panel.allowsMultipleSelection = true
        panel.allowedContentTypes = IntakeModel.openableTypes
        panel.message = model.currentNotebookId == nil ? "Choose files to add to a notebook" : "Choose files to add to \(model.title)"
        panel.prompt = "Add"
        guard panel.runModal() == .OK else { return }
        model.addFiles(panel.urls)
    }

    /// File › Watch Folder…: the standard panel, folders only; the notebook is asked in a sheet after.
    @MainActor fileprivate func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false; panel.canChooseDirectories = true; panel.allowsMultipleSelection = false
        panel.prompt = "Watch"
        panel.message = "New files in this folder become sources of a notebook. LocalBook only reads the folder."
        if panel.runModal() == .OK, let url = panel.url { model.folderToWatch = url }
    }

    /// ⌘V with files copied in the Finder, or with a web address copied from a browser.
    @MainActor fileprivate func pasteFiles(_ providers: [NSItemProvider]) {
        Task {
            var files: [URL] = [], links: [URL] = []
            for p in providers {
                let type: UTType = p.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) ? .fileURL : .url
                guard let item = try? await p.loadItem(forTypeIdentifier: type.identifier) else { continue }
                var url: URL?
                if let u = item as? URL { url = u }
                else if let data = item as? Data { url = URL(dataRepresentation: data, relativeTo: nil) }
                else if let s = item as? String { url = URL(string: s) }
                guard let url else { continue }
                if url.isFileURL { files.append(url) } else if ["http", "https"].contains(url.scheme?.lowercased() ?? "") { links.append(url) }
            }
            model.addFiles(files)
            model.addLinks(links)
        }
    }
}

/// "Which notebook?" for files that arrived without one: opened with LocalBook from the Finder, dropped on the Dock
/// icon, or added while no notebook was on screen. One picker, one button.
struct PlaceFilesSheet: View {
    @Environment(AppModel.self) private var model
    @State private var notebookId = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(model.filesToPlace.count == 1 ? "Add “\(model.filesToPlace.first?.lastPathComponent ?? "")” to" : "Add \(model.filesToPlace.count) files to")
                .font(.headline)
            Picker("Notebook", selection: $notebookId) {
                if notebookId.isEmpty { Text("Choose a notebook").tag("") }     // the first frame, before onAppear picks one
                ForEach(model.sections) { section in
                    Section(section.name) { ForEach(section.notebooks) { Text($0.title).tag($0.id) } }
                }
            }
            .labelsHidden()
            if model.filesToPlace.count > 1 {
                Text(model.filesToPlace.map(\.lastPathComponent).joined(separator: ", ")).font(.caption).foregroundStyle(.secondary).lineLimit(3)
            }
            HStack {
                Spacer()
                Button("Cancel") { model.filesToPlace = [] }.keyboardShortcut(.cancelAction)
                Button("Add") {
                    let urls = model.filesToPlace; model.filesToPlace = []
                    model.intake.take(files: urls, notebook: notebookId, library: model.library)
                }
                .keyboardShortcut(.defaultAction).disabled(notebookId.isEmpty)
            }
        }
        .padding(20).frame(width: 380)
        .onAppear { notebookId = model.currentNotebookId ?? model.library.notebooks.first?.id ?? "" }
    }
}

/// File › Watch Folder…: which notebook the folder feeds, and whether subfolders count.
struct WatchFolderSheet: View {
    @Environment(AppModel.self) private var model
    @State private var notebookId = ""
    @State private var recursive = true
    @State private var problem: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Watch “\(model.folderToWatch?.lastPathComponent ?? "")”").font(.headline)
            Text("Files already there, and every file that lands later, become sources. Changed files are read again. Nothing in the folder is moved or written.")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Picker("Notebook", selection: $notebookId) {
                if notebookId.isEmpty { Text("Choose a notebook").tag("") }     // the first frame, before onAppear picks one
                ForEach(model.sections) { section in
                    Section(section.name) { ForEach(section.notebooks) { Text($0.title).tag($0.id) } }
                }
            }
            Toggle("Include subfolders", isOn: $recursive)
            if let problem { Text(problem).font(.footnote).foregroundStyle(.orange) }
            HStack {
                Spacer()
                Button("Cancel") { model.folderToWatch = nil }.keyboardShortcut(.cancelAction)
                Button("Watch") {
                    guard let url = model.folderToWatch else { return }
                    let notebook = notebookId, deep = recursive
                    model.folderToWatch = nil
                    Task { if let p = await model.addWatchedFolder(url, notebook: notebook, recursive: deep) { model.prompts.problem = p } }
                }
                .keyboardShortcut(.defaultAction).disabled(notebookId.isEmpty)
            }
        }
        .padding(20).frame(width: 420)
        .onAppear { notebookId = model.currentNotebookId ?? model.library.notebooks.first?.id ?? "" }
    }
}

/// File › Add Web Page…: one field for web addresses (one per line) and the notebook they go to.
struct AddLinkSheet: View {
    @Environment(AppModel.self) private var model
    @State private var text = ""
    @State private var notebookId = ""
    @FocusState private var focused: Bool

    private var links: [URL] { IntakeModel.links(in: text) }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Add a web page").font(.headline)
            TextField("https://…", text: $text, axis: .vertical)
                .lineLimit(1...4).textFieldStyle(.roundedBorder).focused($focused)
            Text("A page, an article, a YouTube video, or a link to a PDF or document. One per line.")
                .font(.caption).foregroundStyle(.secondary)
            Picker("Notebook", selection: $notebookId) {
                if notebookId.isEmpty { Text("Choose a notebook").tag("") }     // the first frame, before onAppear picks one
                ForEach(model.sections) { section in
                    Section(section.name) { ForEach(section.notebooks) { Text($0.title).tag($0.id) } }
                }
            }
            HStack {
                Spacer()
                Button("Cancel") { model.linkDraft = nil }.keyboardShortcut(.cancelAction)
                Button(links.count > 1 ? "Add \(links.count) Links" : "Add") {
                    let urls = links; model.linkDraft = nil
                    model.intake.take(links: urls, notebook: notebookId, library: model.library)
                }
                .keyboardShortcut(.defaultAction).disabled(links.isEmpty || notebookId.isEmpty)
            }
        }
        .padding(20).frame(width: 420)
        .onAppear {
            text = model.linkDraft ?? ""
            notebookId = model.currentNotebookId ?? model.library.notebooks.first?.id ?? ""
            focused = true
        }
    }
}

/// A destination whose content arrives in a later phase. Says what it will be in one line and nothing else.
struct PlaceholderPane: View {
    let symbol: String
    let title: String
    let line: String

    var body: some View {
        ContentUnavailableView {
            Label(title, systemImage: symbol)
        } description: {
            Text(line)
        }
    }
}

extension Notification.Name {
    /// Posted by the Ask menu item (⌘L): put the cursor in the composer.
    static let focusComposer = Notification.Name("LocalBook.focusComposer")
    /// Notebook › Stop Answer (⌘.).
    static let stopAnswer = Notification.Name("LocalBook.stopAnswer")
    /// Notebook › Sync Now (⌘R).
    static let syncNow = Notification.Name("LocalBook.syncNow")
    /// File › New Notebook… (⌘N).
    static let newNotebook = Notification.Name("LocalBook.newNotebook")
    /// Notebook › Move My Library Here…
    static let importLibrary = Notification.Name("LocalBook.importLibrary")
    /// Notebook › Pair with a Mac….
    static let pairWithMac = Notification.Name("LocalBook.pairWithMac")
    /// View › Show Sources (⌥⌘1), Show Studio (⌥⌘2), Show Inspector (⌥⌘I).
    static let showSources = Notification.Name("LocalBook.showSources")
    static let showStudio = Notification.Name("LocalBook.showStudio")
    static let showInsights = Notification.Name("LocalBook.showInsights")
    static let showCanvas = Notification.Name("LocalBook.showCanvas")
    static let showConstellation = Notification.Name("LocalBook.showConstellation")
    static let makeInsight = Notification.Name("LocalBook.makeInsight")
    static let writingAssistant = Notification.Name("LocalBook.writingAssistant")
    static let findingsChanged = Notification.Name("LocalBook.findingsChanged")
    static let toggleInspector = Notification.Name("LocalBook.toggleInspector")
    /// Studio › Briefing … Video Explainer; `object` is the `StudioItem.Kind` raw value.
    static let makeStudio = Notification.Name("LocalBook.makeStudio")
    /// View › Show Meetings (⌥⌘3); Record › Start Meeting Recording (⇧⌘R), Voice Note (⇧⌘V); File › New Note (⇧⌘N);
    /// Notebook › Read Answer Aloud (⌥⌘R).
    static let showMeetings = Notification.Name("LocalBook.showMeetings")
    static let startMeetingRecording = Notification.Name("LocalBook.startMeetingRecording")
    static let voiceNote = Notification.Name("LocalBook.voiceNote")
    static let newNote = Notification.Name("LocalBook.newNote")
    static let readAnswerAloud = Notification.Name("LocalBook.readAnswerAloud")
    /// From an intent or `localbook://ask?q=`: `object` is the question.
    static let askQuestion = Notification.Name("LocalBook.askQuestion")
    /// Window › Open in New Window (⇧⌘D): the pane on screen opens its selected item in a window.
    static let detachItem = Notification.Name("LocalBook.detachItem")
    /// File › Add Sources… (⌘O): the open panel.
    static let addSources = Notification.Name("LocalBook.addSources")
    /// A file opened with LocalBook (Finder, Dock icon); `object` is its URL.
    static let openFiles = Notification.Name("LocalBook.openFiles")
    /// File › Add Web Page… (⇧⌘L).
    static let addWebPage = Notification.Name("LocalBook.addWebPage")
    static let findSources = Notification.Name("LocalBook.findSources")
    /// Help › Keyboard Shortcuts (⌘/).
    static let showShortcuts = Notification.Name("LocalBook.showShortcuts")
    /// File › Watch Folder….
    static let watchFolder = Notification.Name("LocalBook.watchFolder")
    /// File › Export Notebook… (⇧⌘E).
    static let exportNotebook = Notification.Name("LocalBook.exportNotebook")
}
