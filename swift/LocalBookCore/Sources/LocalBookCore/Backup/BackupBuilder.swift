import CryptoKit
import Foundation
import SQLite3

/// Gathers this app's folder into the files a backup holds. SQLite databases are copied with the backup API, so a
/// database in use is captured whole; the models, caches and scratch files are left out because they download again.
public enum BackupBuilder {
    public struct Staged: Sendable {
        public var files: [Tar.Entry]
        public var rowCounts: [String: [String: Int]]
        public var skipped: [String]
        public var mediaBytesLeftOut: Int
    }

    /// Folders under the app folder that are never backed up (relative paths, `/`-separated).
    public static let excludedTrees: Set<String> = ["LocalBookLab/hub", "LocalBookLab/flux", "LocalBookLab/flux-schnell", "LocalBookLab/models",
                                                    "LocalBookLab/Diagnostics", "Diagnostics", "Caches", "tmp"]
    public static let excludedSuffixes = ["-wal", "-shm", ".tmp", ".log", ".part", ".download"]
    public static let excludedNames: Set<String> = [".DS_Store", "companion_calls.json"]
    /// Generated audio and video (podcasts, slide videos, meeting recordings): big, and left out unless asked for.
    public static let mediaExtensions: Set<String> = ["m4a", "mp4", "mov", "wav", "caf", "aac", "mp3"]
    public static let restorePendingName = "Restore-pending"
    static let restoreReplacedPrefix = "Restore-replaced-"

    public static func isExcluded(_ relative: String) -> Bool {
        let parts = relative.split(separator: "/").map(String.init)
        guard let first = parts.first else { return true }
        if first == restorePendingName || first.hasPrefix(restoreReplacedPrefix) || first == "keys" { return true }
        for i in 1...parts.count where excludedTrees.contains(parts[..<i].joined(separator: "/")) { return true }
        let name = parts[parts.count - 1]
        if excludedNames.contains(name) || excludedSuffixes.contains(where: { name.hasSuffix($0) }) { return true }
        return false
    }

    public static func isMedia(_ relative: String) -> Bool {
        mediaExtensions.contains((relative as NSString).pathExtension.lowercased())
    }

    /// Everything under `base` that a backup keeps, read into memory (a library of thousands of items is tens of MB;
    /// media is only read when `includeMedia`).
    public static func stage(base: URL, includeMedia: Bool, progress: ((String) -> Void)? = nil) throws -> Staged {
        let fm = FileManager.default
        var files: [Tar.Entry] = []
        var rowCounts: [String: [String: Int]] = [:]
        var skipped: [String] = []
        var mediaLeft = 0
        guard let walk = fm.enumerator(at: base, includingPropertiesForKeys: [.isRegularFileKey, .fileSizeKey, .contentModificationDateKey], options: [.skipsHiddenFiles]) else {
            return Staged(files: [], rowCounts: [:], skipped: [], mediaBytesLeftOut: 0)
        }
        let basePath = base.standardizedFileURL.path
        for case let url as URL in walk {
            let path = url.standardizedFileURL.path
            guard path.hasPrefix(basePath + "/") else { continue }
            let rel = String(path.dropFirst(basePath.count + 1))
            if isExcluded(rel) {
                if (try? url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true { walk.skipDescendants() }
                continue
            }
            let values = try? url.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey, .contentModificationDateKey])
            guard values?.isRegularFile == true else { continue }
            if isMedia(rel), !includeMedia { mediaLeft += values?.fileSize ?? 0; continue }
            let mtime = Int((values?.contentModificationDate ?? Date()).timeIntervalSince1970)
            progress?(rel)
            if rel.hasSuffix(".sqlite") || rel.hasSuffix(".db") {
                do {
                    let (data, counts) = try snapshot(database: url)
                    files.append(Tar.Entry(name: rel, data: data, mtime: mtime))
                    rowCounts[rel] = counts
                } catch { skipped.append(rel) }
            } else if let data = try? Data(contentsOf: url) {
                files.append(Tar.Entry(name: rel, data: data, mtime: mtime))
            } else { skipped.append(rel) }
        }
        files.sort { $0.name < $1.name }
        return Staged(files: files, rowCounts: rowCounts, skipped: skipped, mediaBytesLeftOut: mediaLeft)
    }

    /// A consistent copy of a live SQLite file (the backup API, so readers and writers go on), and each table's rows.
    public static func snapshot(database url: URL) throws -> (Data, [String: Int]) {
        var source: OpaquePointer?
        guard sqlite3_open_v2(url.path, &source, SQLITE_OPEN_READONLY, nil) == SQLITE_OK, let source else {
            sqlite3_close(source); throw BackupError.database(url.lastPathComponent, "could not be opened")
        }
        defer { sqlite3_close(source) }
        let tmp = FileManager.default.temporaryDirectory.appending(path: "lb-snapshot-\(UUID().uuidString).sqlite")
        defer { try? FileManager.default.removeItem(at: tmp) }
        var dest: OpaquePointer?
        guard sqlite3_open_v2(tmp.path, &dest, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE, nil) == SQLITE_OK, let dest else {
            sqlite3_close(dest); throw BackupError.database(url.lastPathComponent, "snapshot file could not be made")
        }
        guard let backup = sqlite3_backup_init(dest, "main", source, "main") else {
            sqlite3_close(dest); throw BackupError.database(url.lastPathComponent, String(cString: sqlite3_errmsg(dest)))
        }
        var rc = sqlite3_backup_step(backup, -1)
        while rc == SQLITE_BUSY || rc == SQLITE_LOCKED { usleep(50_000); rc = sqlite3_backup_step(backup, -1) }
        sqlite3_backup_finish(backup)
        guard rc == SQLITE_DONE else { sqlite3_close(dest); throw BackupError.database(url.lastPathComponent, "the copy stopped (\(rc))") }
        var counts: [String: Int] = [:]
        var stmt: OpaquePointer?
        if sqlite3_prepare_v2(dest, "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'", -1, &stmt, nil) == SQLITE_OK {
            var tables: [String] = []
            while sqlite3_step(stmt) == SQLITE_ROW { if let c = sqlite3_column_text(stmt, 0) { tables.append(String(cString: c)) } }
            sqlite3_finalize(stmt)
            for t in tables {
                var count: OpaquePointer?
                if sqlite3_prepare_v2(dest, "SELECT COUNT(*) FROM \"\(t.replacingOccurrences(of: "\"", with: "\"\""))\"", -1, &count, nil) == SQLITE_OK,
                   sqlite3_step(count) == SQLITE_ROW { counts[t] = Int(sqlite3_column_int64(count, 0)) }
                sqlite3_finalize(count)
            }
        }
        sqlite3_close(dest)
        return (try Data(contentsOf: tmp), counts)
    }

    /// The manifest for what was staged.
    public static func manifest(staged: Staged, deviceId: String, deviceName: String, includeMedia: Bool, recoveryPublicKey: Data?, createdAt: String) -> BackupManifest {
        var files: [String: Any] = [:]
        for f in staged.files { files[f.name] = ["sha256": SHA256.hash(data: f.data).map { String(format: "%02x", $0) }.joined(), "bytes": f.data.count] as [String: Any] }
        var raw: [String: Any] = [
            "format_version": 1, "created_at": createdAt, "device_id": deviceId, "device_name": deviceName,
            "schema": ["version": "swift-1", "app": "LocalBook (Swift)"] as [String: Any],
            "includes_blobs": includeMedia,
            "excluded": ["trees": Array(excludedTrees).sorted(), "media": includeMedia ? [] : Array(mediaExtensions).sorted(), "models": "download again on first use"] as [String: Any],
            "row_counts": staged.rowCounts, "skipped": staged.skipped, "files": files,
        ]
        if let pub = recoveryPublicKey { raw["recovery_public_key"] = pub.base64EncodedString() } else { raw["recovery_public_key"] = NSNull() }
        return BackupManifest(raw: raw)
    }

    /// The whole archive for `base`: staged, listed, packed and sealed.
    public static func makeArchive(base: URL, deviceId: String, deviceName: String, deviceKey: SymmetricKey?, recoveryPublicKey: Data?, includeMedia: Bool,
                                   progress: ((String) -> Void)? = nil) throws -> (archive: Data, manifest: BackupManifest) {
        let staged = try stage(base: base, includeMedia: includeMedia, progress: progress)
        let created = Date().ISO8601Format()
        let manifest = manifest(staged: staged, deviceId: deviceId, deviceName: deviceName, includeMedia: includeMedia, recoveryPublicKey: recoveryPublicKey, createdAt: created)
        progress?("Sealing…")
        let tar = try BackupPayload.pack(manifest: manifest, files: staged.files)
        let archive = try BackupEnvelope.seal(tar: tar, createdAt: created, deviceId: deviceId, deviceKey: deviceKey, recoveryPublicKey: recoveryPublicKey)
        return (archive, manifest)
    }

    /// `LocalBook-<device>-<yyyyMMdd-HHmmss>.lbbackup`.
    public static func fileName(deviceName: String, at date: Date = Date()) -> String {
        let f = DateFormatter(); f.dateFormat = "yyyyMMdd-HHmmss"; f.locale = Locale(identifier: "en_US_POSIX")
        let safe = deviceName.map { $0.isLetter || $0.isNumber ? $0 : "-" }.reduce(into: "") { if !($0.last == "-" && $1 == "-") { $0.append($1) } }
        return "LocalBook-\(safe.trimmingCharacters(in: CharacterSet(charactersIn: "-")))-\(f.string(from: date)).\(BackupEnvelope.suffix)"
    }
}

public enum BackupError: LocalizedError, Equatable {
    case database(String, String), oldApp, pendingExists, mismatch([String])
    public var errorDescription: String? {
        switch self {
        case .database(let n, let why): "\(n) \(why)."
        case .oldApp: "This backup was made by the old LocalBook app. Bring that library over with Settings › Library › Move my library here instead."
        case .pendingExists: "A restore is already waiting; relaunch LocalBook to finish it first."
        case .mismatch(let names): "The backup is damaged: \(names.prefix(3).joined(separator: ", "))\(names.count > 3 ? " and \(names.count - 3) more" : "") do not match the manifest."
        }
    }
}

/// Putting a backup back. The files are staged under `Restore-pending/` while the app runs and swapped in at the next
/// launch, before anything opens the library; what they replace is kept beside them in `Restore-replaced-<when>/`.
public enum BackupRestore {
    /// Opens, checks and stages an archive. Returns its manifest; the swap happens in `applyPending` at launch.
    @discardableResult
    public static func stage(archive: Data, base: URL, deviceKey: SymmetricKey?, phrase: String?) throws -> BackupManifest {
        let pending = base.appending(path: BackupBuilder.restorePendingName, directoryHint: .isDirectory)
        if FileManager.default.fileExists(atPath: pending.path) { throw BackupError.pendingExists }
        let (_, tar) = try BackupEnvelope.open(archive, deviceKey: deviceKey, phrase: phrase)
        let (manifest, files) = try BackupPayload.unpack(tar: tar)
        guard manifest.isFromThisApp else { throw BackupError.oldApp }
        let bad = BackupPayload.mismatches(manifest: manifest, files: files)
        guard bad.isEmpty else { throw BackupError.mismatch(bad) }
        let staging = base.appending(path: BackupBuilder.restorePendingName + ".partial", directoryHint: .isDirectory)
        try? FileManager.default.removeItem(at: staging)
        try FileManager.default.createDirectory(at: staging, withIntermediateDirectories: true)
        for f in files {
            let url = staging.appending(path: f.name)
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try f.data.write(to: url)
        }
        try manifest.json().write(to: staging.appending(path: BackupPayload.manifestName))
        try FileManager.default.moveItem(at: staging, to: pending)
        return manifest
    }

    /// Checks an archive without staging it: its manifest, and whether this Mac's key opens it.
    public static func verify(archive: Data, deviceKey: SymmetricKey?, phrase: String?) throws -> BackupManifest {
        let (_, tar) = try BackupEnvelope.open(archive, deviceKey: deviceKey, phrase: phrase)
        let (manifest, files) = try BackupPayload.unpack(tar: tar)
        let bad = BackupPayload.mismatches(manifest: manifest, files: files)
        guard bad.isEmpty else { throw BackupError.mismatch(bad) }
        return manifest
    }

    public static func hasPending(base: URL) -> Bool {
        FileManager.default.fileExists(atPath: base.appending(path: BackupBuilder.restorePendingName).appending(path: BackupPayload.manifestName).path)
    }

    public static func cancelPending(base: URL) {
        try? FileManager.default.removeItem(at: base.appending(path: BackupBuilder.restorePendingName))
    }

    /// At launch, before the library opens: moves the staged files into place. Returns the folder holding what they
    /// replaced, nil when nothing was pending.
    @discardableResult
    public static func applyPending(base: URL) throws -> URL? {
        let fm = FileManager.default
        let pending = base.appending(path: BackupBuilder.restorePendingName, directoryHint: .isDirectory)
        guard hasPending(base: base) else { return nil }
        let stamp = DateFormatter(); stamp.dateFormat = "yyyyMMdd-HHmmss"; stamp.locale = Locale(identifier: "en_US_POSIX")
        let replaced = base.appending(path: BackupBuilder.restoreReplacedPrefix + stamp.string(from: Date()), directoryHint: .isDirectory)
        try fm.createDirectory(at: replaced, withIntermediateDirectories: true)
        guard let walk = fm.enumerator(at: pending, includingPropertiesForKeys: [.isRegularFileKey], options: []) else { return nil }
        let pendingPath = pending.standardizedFileURL.path
        for case let url as URL in walk {
            guard (try? url.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile) == true else { continue }
            let rel = String(url.standardizedFileURL.path.dropFirst(pendingPath.count + 1))
            let target = rel == BackupPayload.manifestName ? replaced.appending(path: "restored-" + BackupPayload.manifestName) : base.appending(path: rel)
            if rel != BackupPayload.manifestName, fm.fileExists(atPath: target.path) {
                let keep = replaced.appending(path: rel)
                try fm.createDirectory(at: keep.deletingLastPathComponent(), withIntermediateDirectories: true)
                try fm.moveItem(at: target, to: keep)
            }
            // A database's stale journal would be replayed over the restored copy.
            for suffix in ["-wal", "-shm"] { try? fm.removeItem(at: URL(filePath: target.path + suffix)) }
            try fm.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
            try fm.moveItem(at: url, to: target)
        }
        try? fm.removeItem(at: pending)
        return replaced
    }
}
