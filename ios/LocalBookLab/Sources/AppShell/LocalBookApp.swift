import SwiftUI

/// The LocalBook iPhone app: your Mac's library, searchable and answerable on the phone. Phone only — a question
/// is embedded, matched and answered here, and nothing about it is sent anywhere.
/// The app stays portrait everywhere except while a Studio item is open, where it may turn sideways.
final class AppDelegate: NSObject, UIApplicationDelegate {
    nonisolated(unsafe) static var allowed: UIInterfaceOrientationMask = .portrait
    func application(_ application: UIApplication, supportedInterfaceOrientationsFor window: UIWindow?) -> UIInterfaceOrientationMask { Self.allowed }
}

enum OrientationLock {
    @MainActor static func allowRotation(_ on: Bool) {
        AppDelegate.allowed = on ? .allButUpsideDown : .portrait
        guard let scene = UIApplication.shared.connectedScenes.first as? UIWindowScene else { return }
        scene.keyWindow?.rootViewController?.setNeedsUpdateOfSupportedInterfaceOrientations()
        if !on { scene.requestGeometryUpdate(.iOS(interfaceOrientations: .portrait)) { _ in } }       // turn back upright
    }
}

@main
struct LocalBookApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @State private var library = LibraryModel()

    init() { SoakTrace.startSession("LocalBook"); PhoneIdentity.migrateIfNeeded() }

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
