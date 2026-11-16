import Foundation
import LocalBookCore

/// Changes to the library that the paired Mac makes on this app's behalf (it is the system of record until Phase 3):
/// deleting and moving sources, and the notebook list. Each call goes to the Mac first, then the copy here follows,
/// so the two never disagree for longer than a sync. Every method returns a problem to show, or nil when done.
extension LibraryModel {
    public struct Section: Identifiable, Sendable, Hashable { public let id: String; public let name: String }

    private var problemNotPaired: String { "Connect to the Mac to change this." }

    private func mac(_ path: String, _ json: [String: Any]) async throws -> [String: Any] {
        guard let mac = MacLink.paired else { throw LabError.model(problemNotPaired) }
        let body = try JSONSerialization.data(withJSONObject: json, options: [.sortedKeys])  // Data is Sendable; the dictionary is not
        let data = try await MacLink.signedCall(mac, path: path, body: body, timeout: 120)
        return (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
    }

    // MARK: sources

    /// Deletes a source on the Mac and here. A note made here that never reached the Mac is simply removed.
    public func deleteSource(_ source: LibraryStore.SourceInfo) async -> String? {
        guard let store else { return "The library is not open." }
        if source.isLocal {
            if let macId = await noteSentSourceId(source.id) {
                do { _ = try await mac("/phone/library/source/delete", ["notebook_id": source.notebookId, "source_id": macId]) }
                catch { return "The Mac couldn't delete it, so it stays. (\(error.localizedDescription))" }
            }
            try? await store.removeLocalNote(notebook: source.notebookId, sourceId: source.id)
        } else {
            do { _ = try await mac("/phone/library/source/delete", ["notebook_id": source.notebookId, "source_id": source.id]) }
            catch { return "The Mac couldn't delete it, so it stays. (\(error.localizedDescription))" }
            await dropLocally(notebook: source.notebookId, sourceId: source.id)
        }
        await refresh()
        return nil
    }

    /// Moves a source to another notebook on the Mac (it is re-indexed there), then syncs so it appears here.
    public func moveSource(_ source: LibraryStore.SourceInfo, to notebook: String) async -> String? {
        guard notebook != source.notebookId else { return nil }
        var id = source.id
        if source.isLocal {
            guard let macId = await noteSentSourceId(source.id) else { return "This note hasn't reached the Mac yet; it can move once it has." }
            id = macId
        }
        do {
            let r = try await mac("/phone/library/source/move", ["notebook_id": source.notebookId, "source_id": id, "target_notebook_id": notebook])
            if let problem = r["problem"] as? String, !problem.isEmpty { return "Moved, but the Mac could not index it there: \(problem)" }
        } catch { return "The Mac couldn't move it. (\(error.localizedDescription))" }
        if source.isLocal { try? await store?.removeLocalNote(notebook: source.notebookId, sourceId: source.id) }
        else { await dropLocally(notebook: source.notebookId, sourceId: source.id) }
        await sync()
        return nil
    }

    private func dropLocally(notebook: String, sourceId: String) async {
        guard let store else { return }
        let info = await store.notebooks().first { $0.id == notebook }
        try? await store.apply(notebook: notebook, title: info?.title ?? "", digest: info?.digest ?? "partial",
                               removed: [sourceId], resent: [], chunks: [], hashes: [:])
    }

    // MARK: notebooks and sections

    /// The Mac's sections, with the ids the move action needs (the manifest carries names only).
    public func sections() async -> [Section] {
        guard let r = try? await mac("/phone/library/notebook/edit", ["action": "sections"]),
              let rows = r["sections"] as? [[String: Any]] else { return [] }
        return rows.compactMap { row in
            guard let id = row["id"] as? String, let name = row["name"] as? String else { return nil }
            return Section(id: id, name: name)
        }
    }

    /// One notebook-list action on the Mac (create, rename, color, delete, section, section_create, section_rename,
    /// section_delete), then a sync so the sidebar matches. Returns the Mac's reply through `made`.
    @discardableResult
    public func editNotebooks(_ action: String, _ fields: [String: Any] = [:], made: (([String: Any]) -> Void)? = nil) async -> String? {
        var body = fields
        body["action"] = action
        do {
            let r = try await mac("/phone/library/notebook/edit", body)
            made?(r)
        } catch { return "The Mac couldn't do that. (\(error.localizedDescription))" }
        await sync()
        return nil
    }
}
