import Foundation

/// A phone (or a Mac pairing as a peer) this Mac has pinned: its certificate is the only thing that authenticates its
/// requests, so losing this file means pairing again, nothing worse.
public struct PairedPhone: Codable, Sendable, Identifiable, Equatable, Hashable {
    public var deviceId: String
    public var name: String
    public var certPEM: String
    public var fingerprint: String
    public var pairedAt: Date
    public var lastSeen: Date?
    public var id: String { deviceId }

    public init(deviceId: String, name: String, certPEM: String, fingerprint: String, pairedAt: Date = Date(), lastSeen: Date? = nil) {
        self.deviceId = deviceId; self.name = name; self.certPEM = certPEM; self.fingerprint = fingerprint; self.pairedAt = pairedAt; self.lastSeen = lastSeen
    }
}

/// A device that said hello while the pairing window was open and is waiting for the person to compare the code.
public struct PairingRequest: Sendable, Identifiable, Equatable, Hashable {
    public let id: String
    public let deviceId: String
    public let name: String
    public let certPEM: String
    public let fingerprint: String
    /// The six digits both screens show.
    public let code: String
    public let host: String
    public let createdAt: Date
}

/// The pinned devices, as a small JSON file beside the library. Reads are cheap (the file is tiny); writes are whole.
public final class PhoneHostStore: @unchecked Sendable {
    public static let shared = PhoneHostStore()
    private let lock = NSLock()
    private var cache: [PairedPhone]?
    private var lastTouchWrite: [String: Date] = [:]
    private let fileURL: URL?

    /// `url` nil: the file under the app's folder (`Sync/phones.json`). Tests pass their own.
    public init(url: URL? = nil) { fileURL = url }

    private var url: URL { fileURL ?? AppFolders.base.appending(path: "Sync", directoryHint: .isDirectory).appending(path: "phones.json") }

    public func phones() -> [PairedPhone] {
        lock.lock(); defer { lock.unlock() }
        return load()
    }

    public func phone(_ deviceId: String) -> PairedPhone? {
        phones().first { $0.deviceId == deviceId }
    }

    /// Pins a device; a device pairing again replaces its old certificate.
    public func pin(_ phone: PairedPhone) {
        lock.lock(); defer { lock.unlock() }
        var all = load().filter { $0.deviceId != phone.deviceId }
        all.append(phone)
        save(all)
    }

    public func forget(_ deviceId: String) {
        lock.lock(); defer { lock.unlock() }
        save(load().filter { $0.deviceId != deviceId })
    }

    /// Records that the device was heard from; written at most once a minute per device.
    public func touch(_ deviceId: String, at now: Date = Date()) {
        lock.lock(); defer { lock.unlock() }
        var all = load()
        guard let i = all.firstIndex(where: { $0.deviceId == deviceId }) else { return }
        all[i].lastSeen = now
        cache = all
        if let last = lastTouchWrite[deviceId], now.timeIntervalSince(last) < 60 { return }
        lastTouchWrite[deviceId] = now
        save(all)
    }

    private func load() -> [PairedPhone] {
        if let cache { return cache }
        let decoder = JSONDecoder(); decoder.dateDecodingStrategy = .iso8601
        let list = (try? Data(contentsOf: url)).flatMap { try? decoder.decode([PairedPhone].self, from: $0) } ?? []
        cache = list
        return list
    }

    private func save(_ list: [PairedPhone]) {
        cache = list
        let encoder = JSONEncoder(); encoder.dateEncodingStrategy = .iso8601; encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(list) else { return }
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try? data.write(to: url, options: .atomic)
    }
}
