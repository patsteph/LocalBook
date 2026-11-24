import Foundation
#if canImport(UIKit)
import UIKit
#endif

/// What this device is, for the parity reports and the device list on the Mac.
public enum DeviceDescription {
    /// The hardware model string (`iPhone18,2`, `Mac16,7`).
    public static var model: String {
        var sys = utsname()
        uname(&sys)
        return withUnsafeBytes(of: &sys.machine) { raw in
            String(decoding: raw.prefix { $0 != 0 }, as: UTF8.self)
        }
    }

    /// The operating system and version (`iOS 27.2`, `macOS 26.1`).
    public static var system: String {
        #if canImport(UIKit)
        // UIDevice is main-actor only on the iOS 27 SDK; the version needs no device object.
        let v = ProcessInfo.processInfo.operatingSystemVersion
        return "iOS \(v.majorVersion).\(v.minorVersion)" + (v.patchVersion > 0 ? ".\(v.patchVersion)" : "")
        #else
        let v = ProcessInfo.processInfo.operatingSystemVersion
        return "macOS \(v.majorVersion).\(v.minorVersion)" + (v.patchVersion > 0 ? ".\(v.patchVersion)" : "")
        #endif
    }

    /// Physical memory in GB to one decimal.
    public static var memoryGB: Double {
        (Double(ProcessInfo.processInfo.physicalMemory) / 1_073_741_824 * 10).rounded() / 10
    }

    /// The thermal state as the word the parity reports use.
    public static var thermalState: String {
        switch ProcessInfo.processInfo.thermalState {
        case .nominal: "nominal"
        case .fair: "fair"
        case .serious: "serious"
        case .critical: "critical"
        @unknown default: "unknown"
        }
    }

    #if canImport(UIKit)
    nonisolated(unsafe) private static var cachedUserName: String?
    #endif

    /// The name the user gave the device (`Commodore 64`, `Patrick's MacBook Pro`).
    public static var userName: String {
        #if canImport(UIKit)
        // UIDevice is main-actor only on the iOS 27 SDK. The main thread reads it directly; any other thread gets the
        // last name read there (asking the main thread to refresh it) rather than waiting on the main thread, which
        // may itself be waiting on this thread.
        if Thread.isMainThread {
            let name = MainActor.assumeIsolated { UIDevice.current.name }
            cachedUserName = name
            return name
        }
        if let name = cachedUserName { return name }
        Task { @MainActor in cachedUserName = UIDevice.current.name }
        return "iPhone"
        #else
        return Host.current().localizedName ?? ProcessInfo.processInfo.hostName
        #endif
    }
}
