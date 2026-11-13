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
                AskView(notebook: model.selectedNotebook)
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
        .navigationTitle(model.selectedNotebook?.name ?? "LocalBook")
        .navigationSubtitle(model.readiness)
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
