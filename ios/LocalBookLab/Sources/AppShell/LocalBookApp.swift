import SwiftUI

/// The LocalBook iPhone app: your Mac's library, searchable and answerable on the phone. Phone only — a question
/// is embedded, matched and answered here, and nothing about it is sent anywhere.
@main
struct LocalBookApp: App {
    @State private var library = LibraryModel()

    var body: some Scene {
        WindowGroup {
            RootView(library: library)
                .task {
                    library.askMode = .phone
                    await library.open()
                }
        }
    }
}
