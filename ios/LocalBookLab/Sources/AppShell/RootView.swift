import SwiftUI

/// Pairing comes first; after that the app is three tabs, usable at once. Models load in the background.
struct RootView: View {
    let library: LibraryModel
    @State private var tab = 0
    @State private var paired = MacLink.paired?.verified == true

    var body: some View {
        if paired {
            TabView(selection: $tab) {
                AskView(library: library)
                    .tabItem { Label("Ask", systemImage: "text.bubble") }.tag(0)
                NotebooksView(library: library, onAsk: { tab = 0 })
                    .tabItem { Label("Notebooks", systemImage: "books.vertical") }.tag(1)
                SettingsView(library: library, onForget: { paired = false })
                    .tabItem { Label("Settings", systemImage: "gearshape") }.tag(2)
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
