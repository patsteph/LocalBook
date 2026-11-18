import Foundation
import SQLite3

/// Edits made on this device, applied here first (offline-first: nothing waits on the paired Mac), and the queue of
/// those the paired Mac has yet to hear about. Sync sends the queue before it reads the Mac's manifest, so the Mac
/// already carries each change when its list comes back and the two copies agree without anything being undone.
///
/// A notebook made here gets an id of the Mac's shape and a `local_notebook:<id>` mark until the Mac has created
/// it; the Mac answers with its own id, and `renumberNotebook` moves everything over to it.
extension LibraryStore {
    static let localNotebookPrefix = "local_notebook:"
    static let localSectionsKey = "local_sections"

    /// One change waiting for the paired Mac, oldest first by `seq`. `body` is JSON with the fields the Mac's route
    /// takes, plus `notebook_id` / `source_id` where they apply so the sync can tell what is pending for what.
    public struct PendingEdit: Sendable, Identifiable {
        public let seq: Int64
        public let kind: String
        public let body: [String: String]
        public let created: String
        public var id: Int64 { seq }
    }

    func prepareEdits() throws {
        try exec("CREATE TABLE IF NOT EXISTS pending_edits (seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, body TEXT NOT NULL, created TEXT NOT NULL)")
    }

    // MARK: the queue

    public func enqueueEdit(_ kind: String, _ body: [String: String]) throws {
        let data = try JSONSerialization.data(withJSONObject: body, options: [.sortedKeys])
        let s = try statement("INSERT INTO pending_edits(kind, body, created) VALUES (?,?,?)")
        defer { sqlite3_finalize(s) }
        bind(s, 1, kind); bind(s, 2, String(decoding: data, as: UTF8.self)); bind(s, 3, ISO8601DateFormatter().string(from: Date()))
        guard sqlite3_step(s) == SQLITE_DONE else { throw LibraryError.sqlite(String(cString: sqlite3_errmsg(db))) }
    }

    public func pendingEdits() -> [PendingEdit] {
        guard let s = try? statement("SELECT seq, kind, body, created FROM pending_edits ORDER BY seq") else { return [] }
        defer { sqlite3_finalize(s) }
        var out: [PendingEdit] = []
        while sqlite3_step(s) == SQLITE_ROW {
            let json = String(cString: sqlite3_column_text(s, 2))
            let body = (try? JSONSerialization.jsonObject(with: Data(json.utf8)) as? [String: String]) ?? [:]
            out.append(.init(seq: sqlite3_column_int64(s, 0), kind: String(cString: sqlite3_column_text(s, 1)), body: body,
                             created: String(cString: sqlite3_column_text(s, 3))))
        }
        return out
    }

    public func pendingEditCount() -> Int {
        guard let s = try? statement("SELECT COUNT(*) FROM pending_edits") else { return 0 }
        defer { sqlite3_finalize(s) }
        return sqlite3_step(s) == SQLITE_ROW ? Int(sqlite3_column_int64(s, 0)) : 0
    }

    public func removeEdit(_ seq: Int64) throws {
        let s = try statement("DELETE FROM pending_edits WHERE seq=?")
        defer { sqlite3_finalize(s) }
        sqlite3_bind_int64(s, 1, seq); sqlite3_step(s)
    }

    /// Drops every queued edit about a notebook that was then deleted here: the Mac only needs the delete (or, for a
    /// notebook it never had, nothing at all). Returns whether the Mac ever heard of the notebook.
    public func dropEdits(aboutNotebook id: String) throws {
        let s = try statement("DELETE FROM pending_edits WHERE json_extract(body, '$.notebook_id')=? OR json_extract(body, '$.target_notebook_id')=?")
        defer { sqlite3_finalize(s) }
        bind(s, 1, id); bind(s, 2, id); sqlite3_step(s)
    }

    /// Drops the queued edits about one source (it was deleted here; only the delete still matters).
    public func dropEdits(aboutSource sourceId: String) throws {
        let s = try statement("DELETE FROM pending_edits WHERE json_extract(body, '$.source_id')=?")
        defer { sqlite3_finalize(s) }
        bind(s, 1, sourceId); sqlite3_step(s)
    }

    // MARK: notebooks made here

    /// Ids of the notebooks made on this device that the paired Mac has not created yet.
    public func localNotebookIds() -> Set<String> {
        Set(metaKeys(prefix: Self.localNotebookPrefix).map { String($0.dropFirst(Self.localNotebookPrefix.count)) })
    }

    public func isLocalNotebook(_ id: String) -> Bool { meta(Self.localNotebookPrefix + id) != nil }

    /// A new notebook, at the bottom of the list, usable at once. Marked as not yet on the Mac.
    public func createNotebook(id: String, title: String, color: String?) throws {
        let pos = try statement("SELECT COALESCE(MAX(position), -1) + 1 FROM notebooks")
        let position = sqlite3_step(pos) == SQLITE_ROW ? sqlite3_column_int64(pos, 0) : 0
        sqlite3_finalize(pos)
        let s = try statement("INSERT INTO notebooks(id, title, digest, position, color) VALUES (?,?,?,?,?)")
        defer { sqlite3_finalize(s) }
        bind(s, 1, id); bind(s, 2, title); bind(s, 3, "local"); sqlite3_bind_int64(s, 4, position)
        if let color { bind(s, 5, color) } else { sqlite3_bind_null(s, 5) }
        guard sqlite3_step(s) == SQLITE_DONE else { throw LibraryError.sqlite(String(cString: sqlite3_errmsg(db))) }
        try setMeta(Self.localNotebookPrefix + id, "1")
    }

    public func renameNotebook(_ id: String, title: String) throws {
        let s = try statement("UPDATE notebooks SET title=? WHERE id=?")
        defer { sqlite3_finalize(s) }
        bind(s, 1, title); bind(s, 2, id); sqlite3_step(s)
    }

    public func setNotebookColor(_ id: String, color: String?) throws {
        let s = try statement("UPDATE notebooks SET color=? WHERE id=?")
        defer { sqlite3_finalize(s) }
        if let color { bind(s, 1, color) } else { sqlite3_bind_null(s, 1) }
        bind(s, 2, id); sqlite3_step(s)
    }

    /// Puts a notebook in a section by name (nil: none). Sections here are names; the Mac gives them ids.
    public func setNotebookSection(_ id: String, section: String?) throws {
        let s = try statement("UPDATE notebooks SET section=? WHERE id=?")
        defer { sqlite3_finalize(s) }
        if let section, !section.isEmpty { bind(s, 1, section) } else { sqlite3_bind_null(s, 1) }
        bind(s, 2, id); sqlite3_step(s)
    }

    /// Deletes one notebook and everything stored about it here. Returns the ids of its sources, so the caller can
    /// drop their tables and kept files.
    public func deleteNotebook(_ id: String) throws -> [String] {
        dropMatrix()
        let ids = sources(notebook: id).map(\.id)
        for t in ["chunks", "sources", "source_text", "imported_notes", "topics", "note_text", "watched_folders"] {
            let s = try statement("DELETE FROM \(t) WHERE notebook_id=?")
            bind(s, 1, id); sqlite3_step(s); sqlite3_finalize(s)
        }
        try exec("DELETE FROM watched_files WHERE folder_id NOT IN (SELECT id FROM watched_folders)")
        let s = try statement("DELETE FROM notebooks WHERE id=?")
        bind(s, 1, id); sqlite3_step(s); sqlite3_finalize(s)
        for key in ["personal:", "created:", "untagged:", LibrarySync.importedPrefix, Self.localNotebookPrefix] { try deleteMeta(key + id) }
        return ids
    }

    /// The paired Mac created a notebook made here under its own id: every row and mark moves over to it.
    public func renumberNotebook(from old: String, to new: String) throws {
        guard old != new else { return }
        dropMatrix()
        try exec("BEGIN IMMEDIATE")
        do {
            for t in ["notebooks"] {
                let s = try statement("UPDATE \(t) SET id=? WHERE id=?")
                bind(s, 1, new); bind(s, 2, old); sqlite3_step(s); sqlite3_finalize(s)
            }
            for t in ["chunks", "sources", "source_text", "imported_notes", "topics", "note_text", "watched_folders"] {
                let s = try statement("UPDATE \(t) SET notebook_id=? WHERE notebook_id=?")
                bind(s, 1, new); bind(s, 2, old); sqlite3_step(s); sqlite3_finalize(s)
            }
            // Kept originals live under Sources/<notebook>/; the caller moves the folder, the rows follow here.
            let f = try statement("UPDATE source_text SET file=REPLACE(file, ?, ?) WHERE notebook_id=? AND file LIKE ?")
            bind(f, 1, "Sources/\(old)/"); bind(f, 2, "Sources/\(new)/"); bind(f, 3, new); bind(f, 4, "Sources/\(old)/%")
            sqlite3_step(f); sqlite3_finalize(f)
            for key in ["personal:", "created:", "untagged:"] {
                if let v = meta(key + old) { try setMeta(key + new, v); try deleteMeta(key + old) }
            }
            try deleteMeta(Self.localNotebookPrefix + old)
            // Queued edits that named the old id now name the Mac's.
            let e = try statement("UPDATE pending_edits SET body=REPLACE(body, ?, ?)")
            bind(e, 1, "\"\(old)\""); bind(e, 2, "\"\(new)\""); sqlite3_step(e); sqlite3_finalize(e)
            try exec("COMMIT")
        } catch { try? exec("ROLLBACK"); throw error }
    }

    // MARK: sections made here

    /// Section names this device knows beyond those its notebooks sit in (a section made here with nothing in it yet).
    public func localSections() -> [String] {
        guard let json = meta(Self.localSectionsKey), let list = try? JSONDecoder().decode([String].self, from: Data(json.utf8)) else { return [] }
        return list
    }

    public func setLocalSections(_ names: [String]) throws {
        var unique: [String] = []
        for n in names where !n.isEmpty && !unique.contains(n) { unique.append(n) }
        let data = try JSONEncoder().encode(unique)
        try setMeta(Self.localSectionsKey, String(decoding: data, as: UTF8.self))
    }

    /// Every section name in use or made here, in sidebar order.
    public func sectionNames() -> [String] {
        var out: [String] = []
        for n in notebooks() { if let s = n.section, !s.isEmpty, !out.contains(s) { out.append(s) } }
        for s in localSections() where !out.contains(s) { out.append(s) }
        return out
    }

    public func renameSection(_ old: String, to new: String) throws {
        let s = try statement("UPDATE notebooks SET section=? WHERE section=?")
        defer { sqlite3_finalize(s) }
        bind(s, 1, new); bind(s, 2, old); sqlite3_step(s)
        try setLocalSections(localSections().map { $0 == old ? new : $0 })
    }

    public func deleteSection(_ name: String) throws {
        let s = try statement("UPDATE notebooks SET section=NULL WHERE section=?")
        defer { sqlite3_finalize(s) }
        bind(s, 1, name); sqlite3_step(s)
        try setLocalSections(localSections().filter { $0 != name })
    }

    // MARK: sources

    /// Moves one source's rows to another notebook here (its kept original, if any, follows in the file system by the
    /// caller; the row's path is rewritten here).
    public func moveSource(notebook: String, sourceId: String, to target: String) throws {
        dropMatrix()
        try exec("BEGIN IMMEDIATE")
        do {
            for t in ["chunks", "sources", "source_text", "note_text"] {
                let s = try statement("UPDATE \(t) SET notebook_id=? WHERE notebook_id=? AND source_id=?")
                bind(s, 1, target); bind(s, 2, notebook); bind(s, 3, sourceId); sqlite3_step(s); sqlite3_finalize(s)
            }
            let f = try statement("UPDATE source_text SET file=REPLACE(file, ?, ?) WHERE notebook_id=? AND source_id=? AND file LIKE ?")
            bind(f, 1, "Sources/\(notebook)/"); bind(f, 2, "Sources/\(target)/"); bind(f, 3, target); bind(f, 4, sourceId); bind(f, 5, "Sources/\(notebook)/%")
            sqlite3_step(f); sqlite3_finalize(f)
            try exec("COMMIT")
        } catch { try? exec("ROLLBACK"); throw error }
    }

    /// Removes one source's rows (any kind: synced from the Mac, or made here).
    public func removeSource(notebook: String, sourceId: String) throws {
        dropMatrix()
        for t in ["chunks", "sources", "source_text", "note_text"] {
            let s = try statement("DELETE FROM \(t) WHERE notebook_id=? AND source_id=?")
            bind(s, 1, notebook); bind(s, 2, sourceId); sqlite3_step(s); sqlite3_finalize(s)
        }
    }
}
