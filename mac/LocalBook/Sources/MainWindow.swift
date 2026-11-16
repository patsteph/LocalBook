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
                case .sources:
                    SourcesPane()
                case .studio:
                    StudioPane()
                case .meetings:
                PlaceholderPane(symbol: "waveform", title: "Meetings",
                                line: "Record a meeting and get the transcript and notes in a notebook.")
                case .notes:
                    PlaceholderPane(symbol: "note.text", title: "Notes",
                                    line: "Your notes, with the editor and canvas.")
                }
            }
            .inspector(isPresented: $model.showInspector) {
                InspectorView()
                    .inspectorColumnWidth(min: 220, ideal: 280, max: 400)
            }
        }
        .navigationTitle(model.title)
        .navigationSubtitle(model.readiness)
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
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
        .sheet(isPresented: $model.showPairing) { PairingSheet(model: model) }
        .task { await model.start() }
        .onChange(of: model.library.scopeToNotebook) { _, _ in model.syncSelectionToScope() }
        .onReceive(NotificationCenter.default.publisher(for: .syncNow)) { _ in model.syncNow() }
        .onReceive(NotificationCenter.default.publisher(for: .stopAnswer)) { _ in model.library.stopAnswer() }
        .onReceive(NotificationCenter.default.publisher(for: .pairWithMac)) { _ in model.showPairing = true }
        .onReceive(NotificationCenter.default.publisher(for: .focusComposer)) { _ in model.showAsk() }
        .onReceive(NotificationCenter.default.publisher(for: .showSources)) { _ in if model.currentNotebookId != nil { model.selection = .sources } }
        .onReceive(NotificationCenter.default.publisher(for: .showStudio)) { _ in model.selection = .studio }
        .onReceive(NotificationCenter.default.publisher(for: .toggleInspector)) { _ in model.showInspector.toggle() }
        .onReceive(NotificationCenter.default.publisher(for: .makeStudio)) { note in
            if let raw = note.object as? String, let kind = StudioItem.Kind(rawValue: raw) { model.make(kind) }
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
    /// Notebook › Pair with a Mac….
    static let pairWithMac = Notification.Name("LocalBook.pairWithMac")
    /// View › Show Sources (⌥⌘1), Show Studio (⌥⌘2), Show Inspector (⌥⌘I).
    static let showSources = Notification.Name("LocalBook.showSources")
    static let showStudio = Notification.Name("LocalBook.showStudio")
    static let toggleInspector = Notification.Name("LocalBook.toggleInspector")
    /// Studio › Briefing … Video Explainer; `object` is the `StudioItem.Kind` raw value.
    static let makeStudio = Notification.Name("LocalBook.makeStudio")
}
