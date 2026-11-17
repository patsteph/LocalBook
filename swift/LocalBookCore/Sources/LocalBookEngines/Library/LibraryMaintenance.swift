import Foundation
import LocalBookCore

/// Settings › Library: re-index what this device made, repair passages whose vector never got written, and the
/// folders this device watches. The old app's maintenance commands and linked-folder scanner (`folder_watcher.py`),
/// polled rather than event-driven for the reasons that file gives: no missed files across sleep or a closed app.
extension LibraryModel {
    public struct MaintenanceReport: Sendable { public var done = 0; public var failed: [String] = [] }

    /// Cuts, embeds (and with `enrich`, summarises and questions again) every source this device ingested itself.
    public func reindexLocalSources(enrich: Bool, progress: (@MainActor (String) -> Void)? = nil) async -> MaintenanceReport {
        var report = MaintenanceReport()
        guard let store else { report.failed.append("The library is not open."); return report }
        let locals = await store.localSources()
        for (i, src) in locals.enumerated() {
            progress?("Re-indexing \(i + 1) of \(locals.count): \(src.title)…")
            do {
                try await store.removeChunks(notebook: src.notebookId, sourceId: src.sourceId)
                try await ingestText(src.text, title: src.title, format: src.format, notebook: src.notebookId, url: src.url, enrich: enrich,
                                     sourceId: src.sourceId, file: src.file)
                report.done += 1
            } catch { report.failed.append("\(src.title): \(error.localizedDescription)") }
        }
        await refresh()
        return report
    }

    /// Embeds again every passage whose vector is empty or all zeros.
    public func repairEmptyVectors(progress: (@MainActor (String) -> Void)? = nil) async -> MaintenanceReport {
        var report = MaintenanceReport()
        guard let store else { report.failed.append("The library is not open."); return report }
        await waitForSearch()
        guard let engine, let tokenizer else { report.failed.append("The search model is not ready."); return report }
        let empties = await store.emptyVectors()
        for (i, e) in empties.enumerated() {
            if i % 10 == 0 { progress?("Repairing passage \(i + 1) of \(empties.count)…") }
            do {
                let ids = TokenRepair.spaceFallback(tokenizer.encode(text: "title: none | text: " + e.text))
                let v = try await engine.embed(tokenIds: Array(ids.prefix(EmbeddingGemma2Engine.maxTokens)))
                let half = v.map { Float16($0) }
                try await store.setVector(notebook: e.notebookId, sourceId: e.sourceId, chunkIndex: e.chunkIndex, vector: half.withUnsafeBytes { Data($0) })
                report.done += 1
            } catch { report.failed.append("passage \(e.chunkIndex) of \(e.sourceId): \(error.localizedDescription)") }
        }
        if report.done > 0 { await refresh() }
        return report
    }

    // MARK: watched folders

    public struct ScanReport: Sendable {
        public var added = 0, updated = 0, unchanged = 0, skipped = 0
        public var failed: [String] = []
        public var line: String {
            var parts: [String] = []
            if added > 0 { parts.append("\(added) added") }
            if updated > 0 { parts.append("\(updated) updated") }
            if unchanged > 0 { parts.append("\(unchanged) unchanged") }
            if skipped > 0 { parts.append("\(skipped) not readable") }
            if !failed.isEmpty { parts.append("\(failed.count) failed") }
            return parts.isEmpty ? "Nothing to read" : parts.joined(separator: ", ")
        }
    }

    /// Starts watching `url` for `notebook`: keeps a security-scoped bookmark (so the grant survives relaunches) and
    /// scans at once. Returns the folder's record.
    public func watchFolder(_ url: URL, notebook: String, recursive: Bool) async throws -> LibraryStore.WatchedFolder {
        guard let store else { throw LibraryError.badResponse("library not open") }
        var bookmark: Data?
        #if os(macOS)
        bookmark = try? url.bookmarkData(options: .withSecurityScope, includingResourceValuesForKeys: nil, relativeTo: nil)
        #endif
        let folder = LibraryStore.WatchedFolder(id: UUID().uuidString.lowercased(), notebookId: notebook, path: url.path(percentEncoded: false),
                                                bookmark: bookmark, recursive: recursive, added: ISO8601DateFormatter().string(from: Date()))
        try await store.addWatchedFolder(folder)
        return folder
    }

    public func unwatchFolder(_ id: String) async {
        try? await store?.removeWatchedFolder(id: id)
    }

    /// Scans every watched folder; returns one line per folder.
    public func scanWatchedFolders(progress: (@MainActor (String) -> Void)? = nil) async -> [String] {
        guard let store else { return [] }
        var lines: [String] = []
        for folder in await store.watchedFolders() {
            let report = await scan(folder, progress: progress)
            lines.append("\(URL(filePath: folder.path).lastPathComponent): \(report.line)")
        }
        return lines
    }

    /// One folder: read what is new or changed since the ledger, never twice, never writing into the folder.
    public func scan(_ folder: LibraryStore.WatchedFolder, progress: (@MainActor (String) -> Void)? = nil) async -> ScanReport {
        var report = ScanReport()
        guard let store else { return report }
        guard let root = Self.resolve(folder) else {
            report.failed.append("\(folder.path) could not be opened; add the folder again")
            try? await store.setWatchedScan(id: folder.id, at: ISO8601DateFormatter().string(from: Date()), report: report.line + " (no access)")
            return report
        }
        let ledger = await store.watchedFiles(folderId: folder.id)
        let keys: [URLResourceKey] = [.isRegularFileKey, .contentModificationDateKey, .fileSizeKey, .nameKey]
        var options: FileManager.DirectoryEnumerationOptions = [.skipsHiddenFiles, .skipsPackageDescendants]
        if !folder.recursive { options.insert(.skipsSubdirectoryDescendants) }
        guard let walker = FileManager.default.enumerator(at: root, includingPropertiesForKeys: keys, options: options) else { return report }
        let now = Date()
        for case let url as URL in walker {
            guard let values = try? url.resourceValues(forKeys: Set(keys)), values.isRegularFile == true else { continue }
            let name = values.name ?? url.lastPathComponent
            if name.hasPrefix("~$") || name.hasPrefix(".") { continue }
            guard TextExtract.canRead(url) else { report.skipped += 1; continue }
            let mtime = values.contentModificationDate?.timeIntervalSince1970 ?? 0
            let size = values.fileSize ?? 0
            if now.timeIntervalSince1970 - mtime < 10 { continue }                 // still being written; next scan
            let path = url.path(percentEncoded: false)
            if let known = ledger[path], known.mtime == mtime, known.size == size { report.unchanged += 1; continue }
            progress?("Reading \(name) from \(root.lastPathComponent)…")
            do {
                if let old = ledger[path]?.sourceId { try? await store.removeLocalNote(notebook: folder.notebookId, sourceId: old) }
                let made = try await ingestFile(url, notebook: folder.notebookId, keep: .reference, progress: progress)
                try await store.recordWatchedFile(folderId: folder.id, path: path, mtime: mtime, size: size, sourceId: made.sourceId)
                if ledger[path] == nil { report.added += 1 } else { report.updated += 1 }
            } catch {
                report.failed.append("\(name): \(error.localizedDescription)")
            }
        }
        try? await store.setWatchedScan(id: folder.id, at: ISO8601DateFormatter().string(from: Date()), report: report.line)
        if report.added + report.updated > 0 { await refresh() }
        return report
    }

    /// The folder's URL with its security scope open for the rest of the session (so its files open later too).
    nonisolated static func resolve(_ folder: LibraryStore.WatchedFolder) -> URL? {
        #if os(macOS)
        if let data = folder.bookmark {
            var stale = false
            if let url = try? URL(resolvingBookmarkData: data, options: .withSecurityScope, relativeTo: nil, bookmarkDataIsStale: &stale) {
                _ = url.startAccessingSecurityScopedResource()
                return url
            }
        }
        #endif
        let url = URL(filePath: folder.path)
        return FileManager.default.isReadableFile(atPath: url.path) ? url : nil
    }
}
