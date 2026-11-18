import Foundation
import LocalBookCore

extension Notification.Name {
    /// A notebook made here was created on the paired Mac under another id: userInfo "from" and "to".
    public static let notebookRenumbered = Notification.Name("LocalBook.notebookRenumbered")
}

/// Changes to the library, offline-first: every one is made on this device at once, and when a Mac is paired the
/// change is queued and sent the next time that Mac can be reached (before a sync reads its list, so the two copies
/// agree without anything being undone). Nothing here waits on the Mac, and nothing is refused for lack of one.
/// Every method returns a problem to show, or nil when done.
extension LibraryModel {
    public struct Section: Identifiable, Sendable, Hashable { public let id: String; public let name: String }

    /// What the app says when the paired Mac cannot be reached (another network, or that Mac asleep).
    public static let problemMacAway = "Your paired Mac can't be reached from here. Changes are kept on this Mac and sent to it the next time they're on the same network."

    /// A timeout or a connection failure, as against a refusal the Mac answered.
    public static func isUnreachable(_ error: Error) -> Bool { MacLink.isUnreachable(error) }

    /// The id a new notebook gets here: the Mac's shape (a UUID), so nothing downstream can tell them apart.
    public nonisolated static func newNotebookId() -> String { UUID().uuidString.lowercased() }

    // MARK: sources

    /// Deletes a source here at once; if the paired Mac has it, the delete is queued for it.
    public func deleteSource(_ source: LibraryStore.SourceInfo) async -> String? {
        guard let store else { return "The library is not open." }
        let macId: String? = source.isLocal ? await noteSentSourceId(source.id) : source.id
        if let file = source.file, file.hasPrefix("Sources/") { try? FileManager.default.removeItem(at: Self.originalURL(file)) }
        if source.isLocal { try? FileManager.default.removeItem(at: Self.photoURL(source.id)) }
        do { try await store.removeSource(notebook: source.notebookId, sourceId: source.id) }
        catch { return "Couldn't delete it here: \(error.localizedDescription)" }
        try? await tabular?.delete(sourceId: source.id)
        try? await store.dropEdits(aboutSource: source.id)
        if let macId {
            try? await store.setMeta("note_sent:" + source.id, "")
            try? await store.enqueueEdit("source_delete", ["notebook_id": source.notebookId, "source_id": macId])
        }
        await refresh()
        kickFlush()
        return nil
    }

    /// Moves a source to another notebook here at once (its kept original goes with it); queued for the Mac when it has it.
    public func moveSource(_ source: LibraryStore.SourceInfo, to notebook: String) async -> String? {
        guard notebook != source.notebookId, let store else { return nil }
        do { try await store.moveSource(notebook: source.notebookId, sourceId: source.id, to: notebook) }
        catch { return "Couldn't move it here: \(error.localizedDescription)" }
        try? await tabular?.moveSource(sourceId: source.id, to: notebook)
        if let file = source.file, file.hasPrefix("Sources/\(source.notebookId)/") {
            let from = Self.originalURL(file)
            let to = Self.originalURL(file.replacingOccurrences(of: "Sources/\(source.notebookId)/", with: "Sources/\(notebook)/"))
            try? FileManager.default.createDirectory(at: to.deletingLastPathComponent(), withIntermediateDirectories: true)
            try? FileManager.default.moveItem(at: from, to: to)
        }
        let macId: String? = source.isLocal ? await noteSentSourceId(source.id) : source.id
        if let macId {
            try? await store.enqueueEdit("source_move", ["notebook_id": source.notebookId, "source_id": macId, "target_notebook_id": notebook])
        }
        await refresh()
        kickFlush()
        return nil
    }

    // MARK: notebooks and sections

    /// Section names in use here (and any made here that are still empty). Sections have no ids on this device.
    public func sections() async -> [Section] {
        (await store?.sectionNames() ?? []).map { Section(id: $0, name: $0) }
    }

    /// One notebook-list action (create, rename, color, delete, section, section_create, section_rename,
    /// section_delete), made here at once and queued for the paired Mac. `made` gets {"id": …} for a create.
    @discardableResult
    public func editNotebooks(_ action: String, _ fields: [String: Any] = [:], made: (([String: Any]) -> Void)? = nil) async -> String? {
        guard let store else { return "The library is not open." }
        func text(_ key: String) -> String { (fields[key] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? "" }
        let id = text("notebook_id")
        do {
            switch action {
            case "create":
                let title = text("title")
                guard !title.isEmpty else { return "Give the notebook a title first." }
                let newId = Self.newNotebookId()
                let color = fields["color"] as? String
                try await store.createNotebook(id: newId, title: title, color: color)
                var body = ["notebook_id": newId, "title": title]
                if let color { body["color"] = color }
                try await store.enqueueEdit("notebook_create", body)
                made?(["id": newId, "title": title])
            case "rename":
                let title = text("title")
                guard !title.isEmpty else { return "Give the notebook a title first." }
                try await store.renameNotebook(id, title: title)
                try await store.enqueueEdit("notebook_rename", ["notebook_id": id, "title": title])
            case "color":
                let color = text("color")
                try await store.setNotebookColor(id, color: color.isEmpty ? nil : color)
                try await store.enqueueEdit("notebook_color", ["notebook_id": id, "color": color])
            case "section":
                let name = (fields["section"] as? String) ?? (fields["section_id"] as? String) ?? ""
                try await store.setNotebookSection(id, section: name.isEmpty ? nil : name)
                try await store.enqueueEdit("notebook_section", ["notebook_id": id, "section": name])
            case "delete":
                let wasOnMac = !(await store.isLocalNotebook(id))
                try await store.dropEdits(aboutNotebook: id)
                let sourceIds = try await store.deleteNotebook(id)
                for sid in sourceIds {
                    try? await tabular?.delete(sourceId: sid)
                    try? await store.setMeta("note_sent:" + sid, "")
                }
                try? FileManager.default.removeItem(at: AppFolders.userFiles.appending(path: "Sources", directoryHint: .isDirectory).appending(path: id, directoryHint: .isDirectory))
                for item in StudioStore.all(notebook: id) { StudioSync.delete(item) }
                if wasOnMac { try await store.enqueueEdit("notebook_delete", ["notebook_id": id]) }
            case "section_create":
                let name = text("name")
                guard !name.isEmpty else { return "Give the section a name first." }
                try await store.setLocalSections(await store.localSections() + [name])
                try await store.enqueueEdit("section_create", ["name": name])
            case "section_rename":
                let old = text("section_id"), name = text("name")
                guard !name.isEmpty else { return "Give the section a name first." }
                try await store.renameSection(old, to: name)
                try await store.enqueueEdit("section_rename", ["old": old, "name": name])
            case "section_delete":
                let name = text("section_id")
                try await store.deleteSection(name)
                try await store.enqueueEdit("section_delete", ["name": name])
            default:
                return "Unknown action \(action)."
            }
        } catch { return "Couldn't \(action) here: \(error.localizedDescription)" }
        await refresh()
        kickFlush()
        return nil
    }

    // MARK: the queue to the paired Mac

    /// How many changes made here the paired Mac has not heard about yet.
    public func pendingEditCount() async -> Int { await store?.pendingEditCount() ?? 0 }

    /// Sends the queue in the background when a Mac is paired; quiet when it is out of reach.
    func kickFlush() {
        guard MacLink.paired != nil else { return }
        Task { await flushEdits(probe: true) }
    }

    /// Sends every queued change to the paired Mac, oldest first. Stops at the first connection failure (the rest
    /// wait); a change the Mac refuses (it does not share that notebook, say) is dropped with a trace line, since
    /// sending it again would be refused again. Returns how many are still waiting.
    @discardableResult
    public func flushEdits(probe: Bool = false) async -> Int {
        guard let store, let mac = MacLink.paired else { return 0 }
        guard !flushingEdits else { return await store.pendingEditCount() }
        flushingEdits = true
        defer { flushingEdits = false }
        var edits = await store.pendingEdits()
        guard !edits.isEmpty else { return 0 }
        if probe, !(await MacLink.reachable(mac)) { return edits.count }
        var sectionIds: [String: String]?             // the Mac's sections by name, fetched once per flush when needed
        func macSection(named name: String, create: Bool) async throws -> String? {
            if sectionIds == nil {
                let r = try await call(mac, "/phone/library/notebook/edit", ["action": "sections"])
                var ids: [String: String] = [:]
                for row in r["sections"] as? [[String: Any]] ?? [] {
                    if let id = row["id"] as? String, let n = row["name"] as? String { ids[n] = id }
                }
                sectionIds = ids
            }
            if let id = sectionIds?[name] { return id }
            guard create else { return nil }
            let r = try await call(mac, "/phone/library/notebook/edit", ["action": "section_create", "name": name])
            guard let id = r["id"] as? String else { return nil }
            sectionIds?[name] = id
            return id
        }
        while let edit = edits.first {
            let b = edit.body
            do {
                switch edit.kind {
                case "notebook_create":
                    var body: [String: Any] = ["action": "create", "title": b["title"] ?? ""]
                    if let c = b["color"], !c.isEmpty { body["color"] = c }
                    let r = try await call(mac, "/phone/library/notebook/edit", body)
                    if let macId = r["id"] as? String, let localId = b["notebook_id"], !macId.isEmpty, macId != localId {
                        try await store.renumberNotebook(from: localId, to: macId)
                        try? await tabular?.renumberNotebook(from: localId, to: macId)
                        let sources = AppFolders.userFiles.appending(path: "Sources", directoryHint: .isDirectory)
                        try? FileManager.default.moveItem(at: sources.appending(path: localId), to: sources.appending(path: macId))
                        for var item in StudioStore.all(notebook: localId) { item.notebookId = macId; StudioStore.save(item) }
                        if UserDefaults.standard.string(forKey: "lastNotebook") == localId { UserDefaults.standard.set(macId, forKey: "lastNotebook") }
                        if scopeToNotebook == localId { scopeToNotebook = macId }
                        SoakTrace.log("edits: notebook \(localId.prefix(8)) is \(macId.prefix(8)) on the Mac")
                        NotificationCenter.default.post(name: .notebookRenumbered, object: nil, userInfo: ["from": localId, "to": macId])
                    }
                case "notebook_rename":
                    _ = try await call(mac, "/phone/library/notebook/edit", ["action": "rename", "notebook_id": b["notebook_id"] ?? "", "title": b["title"] ?? ""])
                case "notebook_color":
                    _ = try await call(mac, "/phone/library/notebook/edit", ["action": "color", "notebook_id": b["notebook_id"] ?? "", "color": b["color"] ?? ""])
                case "notebook_delete":
                    _ = try await call(mac, "/phone/library/notebook/edit", ["action": "delete", "notebook_id": b["notebook_id"] ?? ""])
                case "notebook_section":
                    let name = b["section"] ?? ""
                    let sid: Any = name.isEmpty ? NSNull() : (try await macSection(named: name, create: true) ?? NSNull())
                    _ = try await call(mac, "/phone/library/notebook/edit", ["action": "section", "notebook_id": b["notebook_id"] ?? "", "section_id": sid])
                case "section_create":
                    _ = try await macSection(named: b["name"] ?? "", create: true)
                case "section_rename":
                    if let sid = try await macSection(named: b["old"] ?? "", create: false) {
                        _ = try await call(mac, "/phone/library/notebook/edit", ["action": "section_rename", "section_id": sid, "name": b["name"] ?? ""])
                        sectionIds?[b["name"] ?? ""] = sid; sectionIds?[b["old"] ?? ""] = nil
                    } else { _ = try await macSection(named: b["name"] ?? "", create: true) }
                case "section_delete":
                    if let sid = try await macSection(named: b["name"] ?? "", create: false) {
                        _ = try await call(mac, "/phone/library/notebook/edit", ["action": "section_delete", "section_id": sid])
                        sectionIds?[b["name"] ?? ""] = nil
                    }
                case "source_delete":
                    _ = try await call(mac, "/phone/library/source/delete", ["notebook_id": b["notebook_id"] ?? "", "source_id": b["source_id"] ?? ""])
                case "source_move":
                    _ = try await call(mac, "/phone/library/source/move", ["notebook_id": b["notebook_id"] ?? "", "source_id": b["source_id"] ?? "", "target_notebook_id": b["target_notebook_id"] ?? ""])
                case "source_rename":
                    _ = try await call(mac, "/phone/library/source/rename", ["notebook_id": b["notebook_id"] ?? "", "source_id": b["source_id"] ?? "", "title": b["title"] ?? ""])
                default:
                    SoakTrace.log("edits: unknown kind \(edit.kind) dropped")
                }
            } catch MacLinkError.http(let code, let message) {
                // The Mac answered and said no (a notebook it does not share, a name it will not take): sending the
                // same change again would get the same answer, so it is dropped and noted.
                SoakTrace.log("edits: \(edit.kind) refused by the Mac (\(code) \(message.prefix(80))); dropped")
                editProblem = "The Mac did not take one change (\(edit.kind.replacingOccurrences(of: "_", with: " "))): \(message.prefix(120))"
            } catch {
                SoakTrace.log("edits: \(edit.kind) waits, the Mac could not be reached: \(error.localizedDescription.prefix(120))")
                return edits.count                                   // out of reach: everything from here waits
            }
            try? await store.removeEdit(edit.seq)
            edits = await store.pendingEdits()
        }
        await refresh()
        return 0
    }

    private func call(_ mac: PairedMac, _ path: String, _ json: [String: Any]) async throws -> [String: Any] {
        let body = try JSONSerialization.data(withJSONObject: json, options: [.sortedKeys])  // Data is Sendable; the dictionary is not
        let data = try await MacLink.signedCall(mac, path: path, body: body, timeout: 120)
        return (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
    }
}
