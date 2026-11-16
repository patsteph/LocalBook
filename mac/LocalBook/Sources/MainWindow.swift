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
            switch model.selection {
            case .notebook, nil:
                AskView()
            case .studio:
                PlaceholderPane(symbol: "sparkles", title: "Studio",
                                line: "Briefings, quizzes, flashcards, audio overviews and visuals, made on this Mac.")
            case .meetings:
                PlaceholderPane(symbol: "waveform", title: "Meetings",
                                line: "Record a meeting and get the transcript and notes in a notebook.")
            case .notes:
                PlaceholderPane(symbol: "note.text", title: "Notes",
                                line: "Your notes, with the editor and canvas.")
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
}
