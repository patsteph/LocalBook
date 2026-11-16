import Foundation

/// Copies each notebook's topics (the Mac's topic model, what its Themes panel shows) into the store, after a sync
/// or an import. Topics are a convenience for the sidebar, so a failure is kept, never thrown: the notebook is still
/// usable without them, and the next sync tries again.
public enum TopicsPull {
    /// Root "/phone/library" over the paired link, "/migrate" over loopback to the old app.
    /// Returns the problems, one per notebook whose topics could not be fetched.
    @discardableResult
    public static func pull(call: LibraryCall, root: String, store: LibraryStore, notebooks: [String]) async -> [String] {
        var problems: [String] = []
        for id in notebooks {
            do {
                let data = try await call("\(root)/topics", try JSONSerialization.data(withJSONObject: ["notebook_id": id]))
                let rows = ((try JSONSerialization.jsonObject(with: data) as? [String: Any])?["topics"] as? [[String: Any]]) ?? []
                let list = rows.compactMap { r -> LibraryStore.TopicInfo? in
                    guard let tid = r["id"] as? String, let name = r["name"] as? String, !name.isEmpty else { return nil }
                    return .init(id: tid, notebookId: id, name: name, keywords: r["keywords"] as? [String] ?? [],
                                 chunks: r["chunks"] as? Int ?? 0, sources: r["sources"] as? Int ?? 0)
                }
                try await store.setTopics(notebook: id, list)
            } catch {
                problems.append("\(id): \(error.localizedDescription)")
            }
        }
        return problems
    }
}
