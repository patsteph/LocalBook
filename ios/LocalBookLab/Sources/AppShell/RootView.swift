import SwiftUI

/// Pairing comes first; after that the app is three tabs.
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
            .task {                                    // the app is not usable until both are ready, so start them at once
                await library.prepareSearch()
                await library.prepareAnswerModel()
            }
        } else {
            OnboardingView(library: library, onDone: { paired = true })
        }
    }
}
