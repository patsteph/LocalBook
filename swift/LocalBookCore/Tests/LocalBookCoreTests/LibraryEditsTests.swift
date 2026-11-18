import Foundation
import Testing
@testable import LocalBookCore

/// Offline-first edits in the store: notebooks made here, the queue for the paired Mac, moving a notebook over to the
/// Mac's id, and what a sync may and may not remove.
@Suite("Library edits, offline first")
struct LibraryEditsTests {
    private func freshStore() async throws -> LibraryStore {
        let url = FileManager.default.temporaryDirectory.appending(path: "edits-\(UUID().uuidString).sqlite")
        let store = try LibraryStore(path: url)
        try await store.prepare()
        return store
    }

    private func vector() -> Data { Data(repeating: 0, count: LibrarySync.expectedDims * 2) }

    private func chunk(_ notebook: String, _ source: String, _ text: String) -> LibraryChunk {
        .init(notebookId: notebook, sourceId: source, chunkIndex: 0, text: text, summary: false, filename: source, sourceType: "note", vector: vector())
    }

    @Test("a notebook made here is listed at once, marked as not on the Mac, and its create is queued")
    func createLocally() async throws {
        let store = try await freshStore()
        try await store.createNotebook(id: "nb-local", title: "Harbor Lights", color: "#3B82F6")
        let listed = await store.notebooks()
        #expect(listed.map(\.id) == ["nb-local"])
        #expect(listed.first?.onMac == false)
        #expect(listed.first?.digest == "local")
        #expect(await store.isLocalNotebook("nb-local"))
        try await store.enqueueEdit("notebook_create", ["notebook_id": "nb-local", "title": "Harbor Lights"])
        let edits = await store.pendingEdits()
        #expect(edits.count == 1)
        #expect(edits.first?.kind == "notebook_create")
        #expect(edits.first?.body["title"] == "Harbor Lights")
    }

    @Test("rename, color and section apply here; the queue keeps the order they happened in")
    func renameColorSection() async throws {
        let store = try await freshStore()
        try await store.createNotebook(id: "nb", title: "Old", color: nil)
        try await store.renameNotebook("nb", title: "New")
        try await store.setNotebookColor("nb", color: "#EF4444")
        try await store.setNotebookSection("nb", section: "Work")
        try await store.enqueueEdit("notebook_rename", ["notebook_id": "nb", "title": "New"])
        try await store.enqueueEdit("notebook_color", ["notebook_id": "nb", "color": "#EF4444"])
        try await store.enqueueEdit("notebook_section", ["notebook_id": "nb", "section": "Work"])
        let nb = await store.notebooks().first
        #expect(nb?.title == "New")
        #expect(nb?.color == "#EF4444")
        #expect(nb?.section == "Work")
        #expect(await store.sectionNames() == ["Work"])
        #expect(await store.pendingEdits().map(\.kind) == ["notebook_rename", "notebook_color", "notebook_section"])
        try await store.removeEdit(await store.pendingEdits()[0].seq)
        #expect(await store.pendingEdits().map(\.kind) == ["notebook_color", "notebook_section"])
    }

    @Test("sections made here with nothing in them are kept by name; rename and delete follow the notebooks")
    func sections() async throws {
        let store = try await freshStore()
        try await store.createNotebook(id: "a", title: "A", color: nil)
        try await store.setNotebookSection("a", section: "Reading")
        try await store.setLocalSections(["Later", "Later"])
        #expect(await store.sectionNames() == ["Reading", "Later"])
        try await store.renameSection("Reading", to: "Books")
        #expect(await store.notebooks().first?.section == "Books")
        try await store.deleteSection("Books")
        #expect(await store.notebooks().first?.section == nil)
        try await store.deleteSection("Later")
        #expect(await store.sectionNames().isEmpty)
    }

    @Test("the Mac's id replaces the local one everywhere: rows, meta, file paths and the queued edits that named it")
    func renumber() async throws {
        let store = try await freshStore()
        try await store.createNotebook(id: "local-1", title: "Trip", color: nil)
        try await store.addLocalSource(notebook: "local-1", sourceId: "local:file-1", title: "a.pdf", format: "pdf", text: "hello",
                                       url: nil, chunks: [chunk("local-1", "local:file-1", "hello")], file: "Sources/local-1/1 a.pdf")
        try await store.addLocalNote(notebook: "local-1", sourceId: "local:n1", title: "n", kind: "note", chunks: [chunk("local-1", "local:n1", "note")], hash: "local", text: "note")
        try await store.setMeta("personal:local-1", "{}")
        try await store.enqueueEdit("notebook_create", ["notebook_id": "local-1", "title": "Trip"])
        try await store.enqueueEdit("notebook_rename", ["notebook_id": "local-1", "title": "Trip 2"])
        try await store.renumberNotebook(from: "local-1", to: "mac-9")
        #expect(await store.notebooks().map(\.id) == ["mac-9"])
        #expect(await store.notebooks().first?.onMac == true)
        #expect(await store.localNotebookIds().isEmpty)
        let sources = await store.sources(notebook: "mac-9")
        #expect(Set(sources.map(\.id)) == ["local:file-1", "local:n1"])
        #expect(sources.first { $0.id == "local:file-1" }?.file == "Sources/mac-9/1 a.pdf")
        #expect(await store.sourceText(notebook: "mac-9", sourceId: "local:file-1") == "hello")
        #expect(await store.meta("personal:mac-9") == "{}")
        #expect(await store.meta("personal:local-1") == nil)
        #expect(await store.pendingEdits().allSatisfy { $0.body["notebook_id"] == "mac-9" })
        #expect(await store.localNotes().first?.notebookId == "mac-9")
    }

    @Test("a source moves between notebooks with its text, note text and kept file path")
    func moveSource() async throws {
        let store = try await freshStore()
        try await store.createNotebook(id: "a", title: "A", color: nil)
        try await store.createNotebook(id: "b", title: "B", color: nil)
        try await store.addLocalSource(notebook: "a", sourceId: "local:file-7", title: "x.docx", format: "docx", text: "body",
                                       url: nil, chunks: [chunk("a", "local:file-7", "body")], file: "Sources/a/7 x.docx")
        try await store.moveSource(notebook: "a", sourceId: "local:file-7", to: "b")
        #expect(await store.sources(notebook: "a").isEmpty)
        let moved = await store.sources(notebook: "b")
        #expect(moved.map(\.id) == ["local:file-7"])
        #expect(moved.first?.file == "Sources/b/7 x.docx")
        #expect(await store.sourceText(notebook: "b", sourceId: "local:file-7") == "body")
        #expect(await store.chunkCount() == 1)
    }

    @Test("deleting a notebook here removes its rows and marks, and the queued edits about it")
    func deleteNotebook() async throws {
        let store = try await freshStore()
        try await store.createNotebook(id: "gone", title: "Gone", color: nil)
        try await store.addLocalSource(notebook: "gone", sourceId: "local:file-2", title: "s", format: "txt", text: "t",
                                       url: nil, chunks: [chunk("gone", "local:file-2", "t")])
        try await store.enqueueEdit("notebook_rename", ["notebook_id": "gone", "title": "Gone 2"])
        try await store.enqueueEdit("source_rename", ["notebook_id": "other", "source_id": "s9", "title": "keep me"])
        try await store.dropEdits(aboutNotebook: "gone")
        let ids = try await store.deleteNotebook("gone")
        #expect(ids == ["local:file-2"])
        #expect(await store.notebooks().isEmpty)
        #expect(await store.chunkCount() == 0)
        #expect(await store.localNotebookIds().isEmpty)
        #expect(await store.pendingEdits().map(\.kind) == ["source_rename"])
        try await store.dropEdits(aboutSource: "s9")
        #expect(await store.pendingEditCount() == 0)
    }

    @Test("a sync's removal of notebooks the Mac no longer lists spares the ones made here and the imported ones")
    func syncKeepsLocalNotebooks() async throws {
        let store = try await freshStore()
        try await store.apply(notebook: "mac-a", title: "From the Mac", digest: "d1", removed: [], resent: [], chunks: [], hashes: [:])
        try await store.apply(notebook: "mac-b", title: "Gone on the Mac", digest: "d2", removed: [], resent: [], chunks: [], hashes: [:])
        try await store.createNotebook(id: "here", title: "Made here", color: nil)
        let listed: Set<String> = ["mac-a"]
        let madeHere = await store.localNotebookIds()
        try await store.removeNotebooks(except: listed.union(madeHere))
        #expect(Set(await store.notebooks().map(\.id)) == ["mac-a", "here"])
    }

    @Test("a connection failure is told apart from a refusal the Mac answered")
    func unreachable() {
        #expect(MacLink.isUnreachable(MacLinkError.detail("The request timed out. [NSURLErrorDomain -1001] — no TLS challenge reached the app")))
        #expect(MacLink.isUnreachable(MacLinkError.detail("Could not connect to the server. [NSURLErrorDomain -1004] — x")))
        #expect(!MacLink.isUnreachable(MacLinkError.http(404, "no such notebook")))
        #expect(!MacLink.isUnreachable(MacLinkError.detail("The data couldn’t be read. [NSCocoaErrorDomain 3840] — x")))
    }
}
