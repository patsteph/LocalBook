import SwiftUI

/// Pairing comes first; after that the app is three tabs, usable at once. Models load in the background.
struct RootView: View {
    let library: LibraryModel
    @State private var paired = MacLink.paired?.verified == true

    var body: some View {
        if paired {
            TabView {
                AskView(library: library)
                    .tabItem { Label("Ask", systemImage: "text.bubble") }
                NotebooksView(library: library)
                    .tabItem { Label("Notebooks", systemImage: "books.vertical") }
                SettingsView(library: library, onForget: { paired = false })
                    .tabItem { Label("Settings", systemImage: "gearshape") }
            }
            .task {
                await library.prepareSearch()
                await library.prepareAnswerModel()
            }
        } else {
            OnboardingView(library: library, onDone: { paired = true })
        }
    }
}
