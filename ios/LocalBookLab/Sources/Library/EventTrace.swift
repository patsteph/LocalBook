import Foundation
import UIKit

/// Breadcrumbs for the soak: one line per event, flushed to disk at once, so that if the app hangs or dies while
/// the screen is locked, `soak-trace.txt` in the app's Files folder still says what happened last.
enum SoakTrace {
    private static let queue = DispatchQueue(label: "soak.trace")
    static var url: URL { URL.documentsDirectory.appending(path: "soak-trace.txt") }

    static func reset() { queue.sync { try? Data().write(to: url) } }

    static func log(_ event: String) {
        let line = "\(ISO8601DateFormatter().string(from: Date())) mem=\(Int(MemoryProbe.footprintMB()))MB thermal=\(ProcessInfo.processInfo.thermalState.rawValue) \(event)\n"
        queue.sync {
            guard let data = line.data(using: .utf8) else { return }
            if let h = try? FileHandle(forWritingTo: url) {
                defer { try? h.close() }
                _ = try? h.seekToEnd(); try? h.write(contentsOf: data); try? h.synchronize()
            } else { try? data.write(to: url) }
        }
    }
}

extension SoakTrace {
    /// Start a session in the trace file and log every app-lifecycle event from now on. Used by the app so that a
    /// hang while the screen is locked can be read afterwards from Files › LocalBook › soak-trace.txt.
    @MainActor static func startSession(_ name: String) {
        if let size = (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int), size > 300_000 { reset() }
        log("=== \(name) launched ===")
        let names: [(Notification.Name, String)] = [
            (UIApplication.willResignActiveNotification, "willResignActive"), (UIApplication.didEnterBackgroundNotification, "didEnterBackground"),
            (UIApplication.willEnterForegroundNotification, "willEnterForeground"), (UIApplication.didBecomeActiveNotification, "didBecomeActive"),
            (UIApplication.protectedDataWillBecomeUnavailableNotification, "protectedDataWillBecomeUnavailable"),
            (UIApplication.protectedDataDidBecomeAvailableNotification, "protectedDataDidBecomeAvailable"),
            (UIApplication.didReceiveMemoryWarningNotification, "MEMORY WARNING")]
        for (n, label) in names {
            NotificationCenter.default.addObserver(forName: n, object: nil, queue: nil) { _ in log("EVENT \(label)") }
        }
    }
}
