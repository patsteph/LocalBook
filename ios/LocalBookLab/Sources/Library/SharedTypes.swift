import Foundation
import UIKit

// Small types the Lab and the LocalBook app both use.

enum LabError: LocalizedError {
    case fixture(String)
    case model(String)
    case tooLong(Int)
    case background(String)

    var errorDescription: String? {
        switch self {
        case .fixture(let m): "Fixture: \(m)"
        case .model(let m): "Model: \(m)"
        case .tooLong(let n): "Input is \(n) tokens; the phone model takes at most 512"
        case .background(let m): "Background task: \(m)"
        }
    }
}

struct DeviceInfo: Codable, Sendable {
    let model: String
    let system: String
    let memoryGB: Double
    let thermalState: String

    @MainActor static func current() -> DeviceInfo {
        var sys = utsname()
        uname(&sys)
        let machine = withUnsafeBytes(of: &sys.machine) { raw in
            String(decoding: raw.prefix { $0 != 0 }, as: UTF8.self)
        }
        let thermal: String = switch ProcessInfo.processInfo.thermalState {
        case .nominal: "nominal"
        case .fair: "fair"
        case .serious: "serious"
        case .critical: "critical"
        @unknown default: "unknown"
        }
        return DeviceInfo(model: machine,
                          system: "\(UIDevice.current.systemName) \(UIDevice.current.systemVersion)",
                          memoryGB: (Double(ProcessInfo.processInfo.physicalMemory) / 1_073_741_824 * 10).rounded() / 10,
                          thermalState: thermal)
    }
}

/// A thread-safe one-way flag.
final class CancelFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var value = false
    var isSet: Bool { lock.withLock { value } }
    func set() { lock.withLock { value = true } }
}
