import Foundation
import Observation
import Tokenizers

/// Phase 1, item 2: the phone's copy of the Mac's library — sync it, then search it with the phone's
/// own embedder. Searching the Mac's vectors with the phone's embedder is exactly what test 1 proved.
@MainActor
@Observable
final class LibraryModel {
    enum SyncState: Equatable { case idle, syncing(String), failed(String) }
    enum SearchState: Equatable { case notReady, preparing(String), ready, failed(String) }

    private(set) var syncState: SyncState = .idle
    private(set) var searchState: SearchState = .notReady
    private(set) var notebooks: [LibraryNotebookInfo] = []
    private(set) var chunkCount = 0
    private(set) var lastSyncLine = "Never synced"
    private(set) var lastSummary: LibrarySync.Summary?
    private(set) var hits: [LibraryHit] = []
    private(set) var timing = ""
    var query = ""
    var scopeToNotebook: String?

    private var store: LibraryStore?
    private var engine: EmbeddingGemma2Engine?
    private var tokenizer: (any Tokenizer)?
    private var queryTemplate = "task: search result | query: {text}"

    func open() async {
        guard store == nil else { return }
        do {
            let s = try LibraryStore()
            try await s.prepare()
            store = s
            await refresh()
        } catch { syncState = .failed(error.localizedDescription) }
    }

    func refresh() async {
        guard let store else { return }
        notebooks = await store.notebooks()
        chunkCount = await store.chunkCount()
        if let t = await store.meta("last_sync") { lastSyncLine = "Last synced \(t)" }
        if let q = await store.meta("query_template") { queryTemplate = q }
    }

    func sync() async {
        guard let store else { return }
        syncState = .syncing("Starting…")
        do {
            let summary = try await LibrarySync.run(store: store) { line in
                Task { @MainActor in if case .syncing = self.syncState { self.syncState = .syncing(line) } }
            }
            lastSummary = summary
            await refresh()
            syncState = .idle
        } catch {
            syncState = .failed(error.localizedDescription)
        }
    }

    /// Download/compile the search model if needed and load the tokenizer.
    func prepareSearch() async {
        guard engine == nil else { return }
        do {
            searchState = .preparing("Search model…")
            let location = try await EmbeddingModelStore.prepare { _ in }
            let e = try EmbeddingGemma2Engine(location: location)
            searchState = .preparing("Warming up (first time only)…")
            _ = try await e.warmUp()
            searchState = .preparing("Tokenizer…")
            tokenizer = try await AutoTokenizer.from(pretrained: LibrarySync.expectedModel)
            engine = e
            searchState = .ready
        } catch { searchState = .failed(error.localizedDescription) }
    }

    func search() async {
        guard let store, let engine, let tokenizer, !query.trimmingCharacters(in: .whitespaces).isEmpty else { return }
        do {
            let t0 = Date()
            let text = queryTemplate.replacingOccurrences(of: "{text}", with: query)
            let ids = TokenRepair.spaceFallback(tokenizer.encode(text: text))
            let vector = try await engine.embed(tokenIds: ids)
            let embedSeconds = Date().timeIntervalSince(t0)
            let result = try await store.search(query: vector, topK: 5, notebook: scopeToNotebook)
            hits = result.hits
            timing = "Question embedded in \(Int(embedSeconds * 1000)) ms · scanned \(result.scanned) chunks in \(Int(result.seconds * 1000)) ms"
        } catch { searchState = .failed(error.localizedDescription) }
    }
}
