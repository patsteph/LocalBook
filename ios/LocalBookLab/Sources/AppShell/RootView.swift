import SwiftUI

/// Pairing comes first; after that the app is four tabs, usable at once. Models load in the background.
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
                StudioView(library: library)
                    .tabItem { Label("Studio", systemImage: "sparkles.rectangle.stack") }.tag(2)
                SettingsView(library: library, onForget: { paired = false })
                    .tabItem { Label("Settings", systemImage: "gearshape") }.tag(3)
            }
            .task {
                // Search runs on the Neural Engine and the answer model on the GPU, so start both at once.
                let t0 = Date()
                async let search: Void = {
                    await library.prepareSearch()
                    await library.loadVisionIfInstalled()
                    SoakTrace.log("startup: search ready after \(String(format: "%.1f", Date().timeIntervalSince(t0))) s")
                }()
                async let answers: Void = {
                    await library.prepareAnswerModel()
                    SoakTrace.log("startup: answer model ready after \(String(format: "%.1f", Date().timeIntervalSince(t0))) s")
                }()
                _ = await (search, answers)
            }
        } else {
            OnboardingView(library: library, onDone: { paired = true })
        }
    }
}
