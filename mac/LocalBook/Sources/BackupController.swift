import AppKit
import CryptoKit
import LocalBookCore
import Observation

/// Backups of the app folder to a folder the user picks (sealed to this Mac's Keychain key and the recovery phrase),
/// the recovery phrase, and restores. The archive format is the old app's `.lbbackup`, so a phrase written down under
/// the old app opens backups from this one.
@Observable @MainActor
final class BackupController {
    struct Archive: Identifiable {
        let url: URL
        let bytes: Int
        let created: Date?
        let device: String
        var id: URL { url }
        var name: String { url.lastPathComponent }
    }

    private(set) var folder: URL?
    private(set) var archives: [Archive] = []
    private(set) var working = false
    private(set) var statusLine = ""
    private(set) var errorLine: String?
    private(set) var lastBackup: Date? = UserDefaults.standard.object(forKey: "backupLast") as? Date
    private(set) var restorePending = false
    private(set) var restoredLine: String?
    /// A phrase just made, shown once until the user says it is written down.
    private(set) var freshPhrase: String?
    private(set) var hasRecoveryKey = RecoveryKey.storedPublicKey() != nil
    private(set) var deviceKeyLine: String?

    var includeMedia: Bool {
        get { UserDefaults.standard.bool(forKey: "backupIncludeMedia") }
        set { UserDefaults.standard.set(newValue, forKey: "backupIncludeMedia") }
    }
    var keep: Int {
        get { max(1, UserDefaults.standard.object(forKey: "backupKeep") as? Int ?? 3) }
        set { UserDefaults.standard.set(newValue, forKey: "backupKeep"); prune() }
    }
    var daily: Bool {
        get { UserDefaults.standard.bool(forKey: "backupDaily") }
        set { UserDefaults.standard.set(newValue, forKey: "backupDaily"); if newValue { scheduleDaily() } }
    }

    private static let bookmarkKey = "backupFolderBookmark"
    private var dailyTask: Task<Void, Never>?

    init() {
        folder = Self.resolveBookmark()
        restorePending = BackupRestore.hasPending(base: AppFolders.base)
        restoredLine = UserDefaults.standard.string(forKey: "backupRestoredLine")
        refresh()
        if daily { scheduleDaily() }
    }

    // MARK: launch

    /// Before the library opens: a staged restore is swapped in, and what it replaced is kept beside it.
    func applyPendingRestore() {
        guard BackupRestore.hasPending(base: AppFolders.base) else { return }
        do {
            if let replaced = try BackupRestore.applyPending(base: AppFolders.base) {
                let line = "Restored on \(Date().formatted(date: .abbreviated, time: .shortened)). What was replaced is kept in \(replaced.lastPathComponent)."
                UserDefaults.standard.set(line, forKey: "backupRestoredLine")
                restoredLine = line
            }
        } catch {
            restoredLine = "The restore could not finish: \(error.localizedDescription)"
            UserDefaults.standard.set(restoredLine, forKey: "backupRestoredLine")
        }
        restorePending = false
        hasRecoveryKey = RecoveryKey.storedPublicKey() != nil
    }

    func dismissRestoredLine() { restoredLine = nil; UserDefaults.standard.removeObject(forKey: "backupRestoredLine") }

    // MARK: the folder

    func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true; panel.canChooseFiles = false; panel.canCreateDirectories = true
        panel.prompt = "Use this folder"; panel.message = "Backups are written here as .lbbackup files."
        guard panel.runModal() == .OK, let url = panel.url else { return }
        do {
            let bookmark = try url.bookmarkData(options: .withSecurityScope, includingResourceValuesForKeys: nil, relativeTo: nil)
            UserDefaults.standard.set(bookmark, forKey: Self.bookmarkKey)
            folder = url
            errorLine = nil
            refresh()
        } catch { errorLine = "That folder could not be kept: \(error.localizedDescription)" }
    }

    private static func resolveBookmark() -> URL? {
        guard let data = UserDefaults.standard.data(forKey: bookmarkKey) else { return nil }
        var stale = false
        return try? URL(resolvingBookmarkData: data, options: .withSecurityScope, relativeTo: nil, bookmarkDataIsStale: &stale)
    }

    /// Runs `body` with the folder readable and writable inside the sandbox.
    private func withFolder<T>(_ body: (URL) throws -> T) throws -> T {
        guard let folder else { throw BackupError.database("The backup folder", "has not been chosen") }
        let scoped = folder.startAccessingSecurityScopedResource()
        defer { if scoped { folder.stopAccessingSecurityScopedResource() } }
        return try body(folder)
    }

    func refresh() {
        archives = (try? withFolder { folder in
            let urls = try FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: [.fileSizeKey], options: [.skipsHiddenFiles])
            return urls.filter { $0.pathExtension == BackupEnvelope.suffix }.map { url in
                let bytes = (try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0
                let header = Self.header(of: url)
                return Archive(url: url, bytes: bytes, created: header.flatMap { try? Date($0.created_at, strategy: .iso8601) } ?? Self.dateFromName(url), device: header?.device_id ?? "?")
            }.sorted { ($0.created ?? .distantPast) > ($1.created ?? .distantPast) }
        }) ?? []
    }

    /// The header without reading the whole archive.
    private static func header(of url: URL) -> BackupEnvelope.Header? {
        guard let handle = try? FileHandle(forReadingFrom: url), let head = try? handle.read(upToCount: 256 * 1024) else { return nil }
        try? handle.close()
        return try? BackupEnvelope.header(of: head + Data(count: 64))
    }

    private static func dateFromName(_ url: URL) -> Date? {
        let stamp = url.deletingPathExtension().lastPathComponent.split(separator: "-").suffix(2).joined(separator: "-")
        let f = DateFormatter(); f.dateFormat = "yyyyMMdd-HHmmss"; f.locale = Locale(identifier: "en_US_POSIX")
        return f.date(from: stamp)
    }

    // MARK: backing up

    func backUpNow() async {
        guard !working else { return }
        guard folder != nil else { errorLine = "Choose a folder for backups first."; return }
        working = true; errorLine = nil; statusLine = "Gathering the library…"
        defer { working = false }
        let base = AppFolders.base
        let includeMedia = includeMedia
        let deviceId = PhoneIdentity.deviceId, deviceName = PhoneIdentity.deviceName
        let recovery = RecoveryKey.storedPublicKey()
        let deviceKey: SymmetricKey?
        do { deviceKey = try DeviceKeys.backupKey(create: true); deviceKeyLine = nil }
        catch { deviceKey = nil; deviceKeyLine = "This Mac's Keychain key is not available (\(error.localizedDescription)); backups are sealed to the recovery phrase only." }
        guard deviceKey != nil || recovery != nil else {
            errorLine = "Set up a recovery phrase first: without this Mac's Keychain key there is nothing to seal the backup to."; return
        }
        let result: Result<(archive: Data, manifest: BackupManifest), Error> = await Task.detached(priority: .userInitiated) {
            do { return .success(try BackupBuilder.makeArchive(base: base, deviceId: deviceId, deviceName: deviceName, deviceKey: deviceKey, recoveryPublicKey: recovery, includeMedia: includeMedia)) }
            catch { return .failure(error) }
        }.value
        switch result {
        case .failure(let error): errorLine = error.localizedDescription
        case .success(let made):
            statusLine = "Writing…"
            do {
                let url = try withFolder { folder in
                    let url = folder.appending(path: BackupBuilder.fileName(deviceName: deviceName))
                    try made.archive.write(to: url, options: .atomic)
                    return url
                }
                lastBackup = Date()
                UserDefaults.standard.set(lastBackup, forKey: "backupLast")
                let mb = Double(made.archive.count) / 1_048_576
                statusLine = "Backed up \(made.manifest.files.count) files (\(mb < 10 ? String(format: "%.1f", mb) : String(Int(mb))) MB) to \(url.lastPathComponent)."
                    + (recovery == nil ? " No recovery phrase is set, so only this Mac can open it." : "")
                prune()
                refresh()
            } catch { errorLine = "The backup could not be written: \(error.localizedDescription)" }
        }
    }

    /// Keeps the newest `keep` archives from this device in the folder.
    private func prune() {
        refresh()
        let mine = archives.filter { $0.device == PhoneIdentity.deviceId }
        guard mine.count > keep else { return }
        _ = try? withFolder { _ in for old in mine.dropFirst(keep) { try? FileManager.default.removeItem(at: old.url) } }
        refresh()
    }

    func delete(_ archive: Archive) {
        _ = try? withFolder { _ in try FileManager.default.removeItem(at: archive.url) }
        refresh()
    }

    private func scheduleDaily() {
        dailyTask?.cancel()
        dailyTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(90))
                guard let self, self.daily, self.folder != nil, !self.working else { if self == nil { return } else { continue } }
                if (self.lastBackup ?? .distantPast) < Date().addingTimeInterval(-24 * 3_600) { await self.backUpNow() }
                try? await Task.sleep(for: .seconds(3_600))
            }
        }
    }

    // MARK: the recovery phrase

    func createPhrase() { freshPhrase = BIP39.generate() }

    /// The user has written the fresh phrase down: its public key is kept and every backup from now on is sealed to it.
    func confirmFreshPhrase() {
        guard let phrase = freshPhrase else { return }
        do { try RecoveryKey.store(phrase: phrase); hasRecoveryKey = true; freshPhrase = nil; errorLine = nil }
        catch { errorLine = error.localizedDescription }
    }

    func discardFreshPhrase() { freshPhrase = nil }

    /// A phrase from before (the old app's, or this Mac's from another install).
    func useExistingPhrase(_ phrase: String) -> Bool {
        do { try RecoveryKey.store(phrase: phrase); hasRecoveryKey = true; errorLine = nil; return true }
        catch { errorLine = error.localizedDescription; return false }
    }

    func forgetPhrase() { RecoveryKey.forget(); hasRecoveryKey = false }

    // MARK: restoring

    enum Unlock { case opened(BackupManifest), needsPhrase, failed(String) }

    /// Reads and checks an archive with this Mac's key, or the phrase when given.
    func verify(_ url: URL, phrase: String?) async -> Unlock {
        let deviceKey = try? DeviceKeys.backupKey(create: false)
        let data: Data
        do { data = try withScope(url) { try Data(contentsOf: $0) } } catch { return .failed("The file could not be read: \(error.localizedDescription)") }
        let outcome: Result<BackupManifest, Error> = await Task.detached {
            do { return .success(try BackupRestore.verify(archive: data, deviceKey: deviceKey, phrase: phrase)) } catch { return .failure(error) }
        }.value
        switch outcome {
        case .success(let m): return .opened(m)
        case .failure(let e):
            if case BackupEnvelope.OpenError.locked(let tried) = e, !tried { return .needsPhrase }
            return .failed(e.localizedDescription)
        }
    }

    /// Stages the restore; it is applied at the next launch.
    func restore(_ url: URL, phrase: String?) async -> String? {
        let deviceKey = try? DeviceKeys.backupKey(create: false)
        let data: Data
        do { data = try withScope(url) { try Data(contentsOf: $0) } } catch { return "The file could not be read: \(error.localizedDescription)" }
        let base = AppFolders.base
        let outcome: Result<BackupManifest, Error> = await Task.detached {
            do { return .success(try BackupRestore.stage(archive: data, base: base, deviceKey: deviceKey, phrase: phrase)) } catch { return .failure(error) }
        }.value
        switch outcome {
        case .success: restorePending = true; return nil
        case .failure(let e): return e.localizedDescription
        }
    }

    func cancelRestore() { BackupRestore.cancelPending(base: AppFolders.base); restorePending = false }

    /// Quits so the next launch finishes the restore.
    func quitToRestore() { NSApp.terminate(nil) }

    /// A file inside the chosen folder is covered by its bookmark; one picked elsewhere by the open panel's grant.
    private func withScope<T>(_ url: URL, _ body: (URL) throws -> T) throws -> T {
        if let folder, url.path.hasPrefix(folder.path) { return try withFolder { _ in try body(url) } }
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        return try body(url)
    }
}
