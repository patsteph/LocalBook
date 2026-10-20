import Foundation

/// Pulls the Mac's library onto the phone over the signed phone link: a manifest, then — for each
/// notebook whose digest differs — the source hashes, then only the changed sources' chunks.
enum LibrarySync {
    /// What this phone's embedder is. Vectors from any other embedder are refused.
    static let expectedModel = "mlx-community/embeddinggemma-2-bf16"
    static let expectedDims = 768
    static let expectedMaxTokens = 512

    struct Manifest: Decodable {
        struct Embedder: Decodable {
            let modelId: String; let dims: Int; let maxTokens: Int
            let queryTemplate: String; let documentTemplate: String
        }
        struct Notebook: Decodable { let id: String; let title: String; let sources: Int; let chunks: Int; let digest: String }
        let embedder: Embedder
        let notebooks: [Notebook]
    }
    struct Sources: Decodable { struct Source: Decodable { let sourceId: String; let hash: String }; let sources: [Source] }
    struct ChunkPage: Decodable {
        struct Row: Decodable {
            let sourceId: String; let chunkIndex: Int; let text: String; let summary: Bool
            let filename: String; let sourceType: String; let vector: String
        }
        let chunks: [Row]; let more: Bool; let after: After?
        struct After: Codable { let sourceId: String; let chunkIndex: Int }
    }

    struct Summary: Sendable {
        var notebooks = 0, notebooksChanged = 0, sourcesFetched = 0, chunksFetched = 0, sourcesRemoved = 0
        var seconds = 0.0, bytes = 0
        var recipeVersion: String?
        var recipeProblems: [String] = []
    }

    private static var decoder: JSONDecoder {
        let d = JSONDecoder(); d.keyDecodingStrategy = .convertFromSnakeCase; return d
    }

    private static func call<T: Decodable>(_ mac: PairedMac, _ path: String, _ json: [String: Any] = [:],
                                           as: T.Type, bytes: inout Int) async throws -> T {
        let data = try await MacLink.signedCall(mac, path: path, json: json)
        bytes += data.count
        do { return try decoder.decode(T.self, from: data) }
        catch { throw LibraryError.badResponse("\(path): \(error.localizedDescription)") }
    }

    /// Bring the phone's copy in line with the Mac. `progress` gets short human-readable lines.
    static func run(store: LibraryStore, progress: @escaping @Sendable (String) -> Void) async throws -> Summary {
        guard let mac = MacLink.paired else { throw LibraryError.notPaired }
        let started = Date()
        var sum = Summary()
        progress("Asking the Mac what it has…")
        let manifest = try await call(mac, "/phone/library/manifest", as: Manifest.self, bytes: &sum.bytes)
        let key = manifest.embedder
        guard key.modelId == expectedModel, key.dims == expectedDims, key.maxTokens == expectedMaxTokens else {
            throw LibraryError.embedderMismatch("The Mac searches with \(key.modelId) (\(key.dims) numbers, \(key.maxTokens)-token limit); "
                + "this phone uses \(expectedModel) (\(expectedDims), \(expectedMaxTokens)). Its vectors would not match, so nothing was copied.")
        }
        // A different embedder than the last sync means every stored vector is stale.
        let keyString = "\(key.modelId)|\(key.dims)|\(key.maxTokens)|\(key.documentTemplate)"
        if let old = await store.meta("embedder"), old != keyString { try await store.wipe() }
        try await store.setMeta("embedder", keyString)
        try await store.setMeta("query_template", key.queryTemplate)

        // The Mac's answer recipe (prompts, classifier, decode settings, cleaning): kept as published, then
        // checked against the Mac's own sample outputs so a mismatch is reported rather than trusted.
        let recipeData = try await MacLink.signedCall(mac, path: "/phone/library/recipe")
        sum.bytes += recipeData.count
        if let recipe = try? decoder.decode(AnswerRecipe.self, from: recipeData) {
            try await store.setMeta("recipe", String(decoding: recipeData, as: UTF8.self))
            sum.recipeVersion = recipe.recipeVersion
            sum.recipeProblems = recipe.check()
        } else {
            sum.recipeProblems = ["The Mac sent an answer recipe this phone cannot read (version mismatch?)"]
        }

        try await store.removeNotebooks(except: Set(manifest.notebooks.map(\.id)))
        sum.notebooks = manifest.notebooks.count
        let local = Dictionary(uniqueKeysWithValues: await store.notebooks().map { ($0.id, $0.digest) })
        for (i, nb) in manifest.notebooks.enumerated() {
            if local[nb.id] == nb.digest { progress("\(nb.title): up to date"); continue }
            sum.notebooksChanged += 1
            progress("\(nb.title): comparing (\(i + 1) of \(manifest.notebooks.count))…")
            let remote = try await call(mac, "/phone/library/sources", ["notebook_id": nb.id], as: Sources.self, bytes: &sum.bytes)
            let have = await store.sourceHashes(notebook: nb.id)
            let remoteHashes = Dictionary(uniqueKeysWithValues: remote.sources.map { ($0.sourceId, $0.hash) })
            let removed = have.keys.filter { remoteHashes[$0] == nil }
            let resend = remote.sources.filter { have[$0.sourceId] != $0.hash }.map(\.sourceId)
            sum.sourcesRemoved += removed.count
            var rows: [LibraryChunk] = []
            for batch in stride(from: 0, to: resend.count, by: 40).map({ Array(resend[$0..<min($0 + 40, resend.count)]) }) {
                var after: ChunkPage.After?
                repeat {
                    var body: [String: Any] = ["notebook_id": nb.id, "source_ids": batch, "limit": 200]
                    if let after { body["after"] = ["source_id": after.sourceId, "chunk_index": after.chunkIndex] }
                    let page = try await call(mac, "/phone/library/chunks", body, as: ChunkPage.self, bytes: &sum.bytes)
                    for r in page.chunks {
                        guard let vec = Data(base64Encoded: r.vector), vec.count == expectedDims * 2 else { continue }
                        rows.append(.init(notebookId: nb.id, sourceId: r.sourceId, chunkIndex: r.chunkIndex, text: r.text,
                                          summary: r.summary, filename: r.filename, sourceType: r.sourceType, vector: vec))
                    }
                    progress("\(nb.title): \(rows.count) chunks received")
                    after = page.more ? page.after : nil
                } while after != nil
            }
            sum.sourcesFetched += resend.count
            sum.chunksFetched += rows.count
            try await store.apply(notebook: nb.id, title: nb.title, digest: nb.digest, removed: removed, resent: resend,
                                  chunks: rows, hashes: remoteHashes.filter { resend.contains($0.key) })
        }
        try await store.setMeta("last_sync", ISO8601DateFormatter().string(from: Date()))
        sum.seconds = Date().timeIntervalSince(started)
        return sum
    }
}
