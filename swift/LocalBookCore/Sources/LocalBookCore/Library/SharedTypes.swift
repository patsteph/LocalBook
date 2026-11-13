import Foundation

// Small types the Lab and the LocalBook app both use.

public enum LabError: LocalizedError {
    case fixture(String)
    case model(String)
    case tooLong(Int)
    case background(String)

    public var errorDescription: String? {
        switch self {
        case .fixture(let m): "Fixture: \(m)"
        case .model(let m): "Model: \(m)"
        case .tooLong(let n): "Input is \(n) tokens; the phone model takes at most 512"
        case .background(let m): "Background task: \(m)"
        }
    }
}

public struct DeviceInfo: Codable, Sendable {
    public let model: String
    public let system: String
    public let memoryGB: Double
    public let thermalState: String

    @MainActor public static func current() -> DeviceInfo {
        DeviceInfo(model: DeviceDescription.model, system: DeviceDescription.system,
                   memoryGB: DeviceDescription.memoryGB, thermalState: DeviceDescription.thermalState)
    }
}

/// A thread-safe one-way flag.
public final class CancelFlag: @unchecked Sendable {
    public init() {}
    private let lock = NSLock()
    private var value = false
    public var isSet: Bool { lock.withLock { value } }
    public func set() { lock.withLock { value = true } }
}
