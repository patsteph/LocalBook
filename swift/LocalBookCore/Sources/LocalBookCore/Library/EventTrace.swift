import Foundation

/// Breadcrumbs for the soak: one line per event, flushed to disk at once, so that if the app hangs or dies while
/// the screen is locked, `soak-trace.txt` in the app's Files folder still says what happened last.
public enum SoakTrace {
    private static let queue = DispatchQueue(label: "soak.trace")
    public static var url: URL { URL.documentsDirectory.appending(path: "soak-trace.txt") }

    public static func reset() { queue.sync { try? Data().write(to: url) } }

    public static func log(_ event: String) {
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
    @MainActor public static func startSession(_ name: String) {
        if let size = (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int), size > 300_000 { reset() }
        log("=== \(name) launched ===")
        let names: [(Notification.Name, String)] = [
            (AppLifecycle.willResignActive, "willResignActive"), (AppLifecycle.didEnterBackground, "didEnterBackground"),
            (AppLifecycle.willEnterForeground, "willEnterForeground"), (AppLifecycle.didBecomeActive, "didBecomeActive"),
            (AppLifecycle.protectedDataWillBecomeUnavailable, "protectedDataWillBecomeUnavailable"),
            (AppLifecycle.protectedDataDidBecomeAvailable, "protectedDataDidBecomeAvailable"),
            (AppLifecycle.didReceiveMemoryWarning, "MEMORY WARNING")]
        for (n, label) in names {
            NotificationCenter.default.addObserver(forName: n, object: nil, queue: nil) { _ in log("EVENT \(label)") }
        }
    }
}

import os

/// Intervals for Instruments (Points of Interest / os_signpost), beside the trace file: startup phases and the
/// stages of a question, so a profile shows where the seconds go.
public enum Signpost {
    private static let signposter = OSSignposter(subsystem: "com.patsteph.localbook", category: .pointsOfInterest)

    public static func measure<T>(_ name: StaticString, isolation: isolated (any Actor)? = #isolation, _ body: () async throws -> T) async rethrows -> T {
        let id = signposter.makeSignpostID()
        let state = signposter.beginInterval(name, id: id)
        defer { signposter.endInterval(name, state) }
        return try await body()
    }
}

/// Memory the OS charges this process (what jetsam looks at), in MB.
public enum MemoryProbe {
    public static func footprintMB() -> Double {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)
        let kr = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
            }
        }
        return kr == KERN_SUCCESS ? Double(info.phys_footprint) / 1_048_576 : .nan
    }
    /// Memory the app may still take. iOS says exactly (`os_proc_available_memory`); the Mac has no per-process limit,
    /// so it reports what the machine has left over its footprint.
    public static func availableMB() -> Double {
        #if os(iOS)
        return Double(os_proc_available_memory()) / 1_048_576
        #else
        return Double(ProcessInfo.processInfo.physicalMemory) / 1_048_576 - footprintMB()
        #endif
    }
}
