import SwiftUI

/// The LocalBook iPhone app: your Mac's library, searchable and answerable on the phone. Phone only — a question
/// is embedded, matched and answered here, and nothing about it is sent anywhere.
@main
struct LocalBookApp: App {
    @State private var library = LibraryModel()

    init() { SoakTrace.startSession("LocalBook") }

    var body: some Scene {
        WindowGroup {
            RootView(library: library)
                .task {
                    library.askMode = .phone
                    await library.open()
                }
                .onReceive(NotificationCenter.default.publisher(for: UIApplication.didReceiveMemoryWarningNotification)) { _ in
                    library.trimMemory()
                }
                .onReceive(NotificationCenter.default.publisher(for: ProcessInfo.thermalStateDidChangeNotification)) { _ in
                    library.thermalCheck()
                }
        }
    }
}
