import Combine
import LocalBookCore
import LocalBookEngines
import SwiftUI

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
                }
            }
            .acceptsDrops(for: model.currentNotebookId)
            .overlay(alignment: .bottom) { IntakeBanner().padding(.bottom, 14) }
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
                    .help("The notebook's sources (⌥⌘1)")
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
                    .help("Sync with your Mac now (⌘R)")
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
        [Notification.Name.syncNow, .stopAnswer, .pairWithMac, .focusComposer, .showSources, .showStudio, .showMeetings,
         .startMeetingRecording, .newNote, .voiceNote, .toggleInspector, .makeStudio, .askQuestion, .newNotebook]
            .map { NotificationCenter.default.publisher(for: $0) }
    )

    private func handle(_ note: Notification) {
        switch note.name {
        case .syncNow: model.syncNow()
        case .newNotebook: if model.paired { model.prompts.text = ""; model.prompts.newNotebook = true }
        case .stopAnswer: model.library.stopAnswer()
        case .pairWithMac: model.showPairing = true
        case .focusComposer: model.showAsk()
        case .showSources: if model.currentNotebookId != nil { model.selection = .sources }
        case .showStudio: model.selection = .studio
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
        case .makeStudio:
            if let raw = note.object as? String, let kind = StudioItem.Kind(rawValue: raw) { model.make(kind) }
        default: break
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
    /// Notebook › Pair with a Mac….
    static let pairWithMac = Notification.Name("LocalBook.pairWithMac")
    /// View › Show Sources (⌥⌘1), Show Studio (⌥⌘2), Show Inspector (⌥⌘I).
    static let showSources = Notification.Name("LocalBook.showSources")
    static let showStudio = Notification.Name("LocalBook.showStudio")
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
}
