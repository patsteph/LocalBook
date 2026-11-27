import Foundation
import LocalBookCore
import MLXLMCommon

/// The local API's routes answered with the same code the Mac's own window runs: retrieval for `search_notebook`,
/// `localAnswer` for `ask_notebook`, `ingestText`/`ingestLink` for a browser capture, the loaded answer model for
/// `/v1/chat/completions`. An agent can do nothing the owner could not do here, and it is done the same way.
@MainActor
public final class LocalAPIBridge: LocalAPIDelegate {
    private let library: LibraryModel
    public let memory: MemoryStore?
    public let proposals = Proposals()

    public init(library: LibraryModel) {
        self.library = library
        memory = try? MemoryStore()
    }

    private static let passageChars = 2_000
    private static let defaultChars = 8_000
    private static let noteChars = 20_000

    private func k(_ args: JSONBox, _ key: String, _ fallback: Int, max cap: Int = 50) -> Int { Swift.max(1, Swift.min(args.int(key) ?? fallback, cap)) }

    // MARK: MCP tools

    public func tool(_ name: String, args: JSONBox, caller: CompanionIdentity) async -> JSONBox {
        do {
            switch name {
            case "list_notebooks": return JSONBox(await listNotebooks())
            case "list_sources": return JSONBox(await listSources(args))
            case "search_notebook": return JSONBox(try await searchNotebook(args))
            case "search_notebooks": return JSONBox(try await searchNotebooks(args, caller))
            case "ask_notebook": return JSONBox(try await askNotebook(args, caller))
            case "get_source": return JSONBox(await getSource(args))
            case "list_notes": return JSONBox(await listNotes(args))
            case "get_note": return JSONBox(await getNote(args))
            case "list_recent_notes": return JSONBox(await recentNotes(args))
            case "fetch_page": return JSONBox(try await fetchPage(args))
            case "web_search": return JSONBox(["results": [[String: Any]](), "note": "Web search needs a Brave key in Settings › Agents, which this version does not take yet; search the notebooks, or use fetch_page on an address you know."])
            case "youtube_search": return JSONBox(["results": [[String: Any]](), "note": "YouTube search waits for Settings › Keys; add a video by its address with fetch_page or propose_note."])
            case "scholarly_search": return JSONBox(try await scholarly(args))
            case "propose_note": return JSONBox(propose(args, caller))
            case "approval_queue": return JSONBox(queue(args))
            case "memory_search": return JSONBox(try await memorySearch(args))
            case "memory_add": return JSONBox(try await memoryAdd(args, caller))
            default: return .error("unknown tool \(name)")
            }
        } catch {
            return .error(error.localizedDescription)
        }
    }

    private func listNotebooks() async -> [String: Any] {
        var rows: [[String: Any]] = []
        for nb in library.notebooks {
            let summary = await library.notebookSummary(nb.id)
            rows.append(["id": nb.id, "title": nb.title, "description": NSNull(), "source_count": summary.sources])
        }
        return ["notebooks": rows]
    }

    private func listSources(_ args: JSONBox) async -> [String: Any] {
        guard let notebook = args.string("notebook_id"), !notebook.isEmpty else { return ["error": "notebook_id is required"] }
        guard library.notebooks.contains(where: { $0.id == notebook }) else { return ["error": "no notebook with id \(notebook) (see list_notebooks)"] }
        let rows = await library.sources(in: notebook)
        return ["notebook_id": notebook, "total": rows.count,
                "sources": rows.prefix(k(args, "k", 20, max: 100)).map { ["id": $0.id, "title": $0.name, "type": $0.type, "added_at": $0.created] as [String: Any] }]
    }

    private func passage(_ h: LibraryHit, score: Double?) -> [String: Any] {
        ["source_id": h.sourceId, "title": h.filename, "source_type": h.sourceType, "chunk_index": h.chunkIndex,
         "text": String(h.text.prefix(Self.passageChars)), "truncated": h.text.count > Self.passageChars, "score": orNull(score)]
    }

    private func searchNotebook(_ args: JSONBox) async throws -> [String: Any] {
        guard let notebook = args.string("notebook_id"), !notebook.isEmpty else { return ["error": "notebook_id is required"] }
        guard let query = args.string("query"), !query.isEmpty else { return ["error": "a query is required"] }
        guard let nb = library.notebooks.first(where: { $0.id == notebook }) else { return ["error": "no notebook with id \(notebook) (see list_notebooks)"] }
        await library.waitForSearch()
        let found = try await library.retrieve(question: query, notebook: notebook)
        let top = found.pool.isEmpty ? found.hits.map { ($0, Double($0.rrf)) } : found.pool.map { ($0.hit, $0.rerank) }
        return ["notebook_id": notebook, "notebook_title": nb.title, "results": top.prefix(k(args, "k", 8)).map { passage($0.0, score: $0.1) }]
    }

    private func searchNotebooks(_ args: JSONBox, _ caller: CompanionIdentity) async throws -> [String: Any] {
        guard let query = args.string("query"), !query.isEmpty else { return ["error": "a query is required"] }
        let ids = args.strings("notebook_ids")
        if ids?.count != 1, !caller.has("all_notebooks") {
            var denied = LocalAPI.scopeDenied("all_notebooks", companion: caller.companionId)
            denied["hint"] = "pass one notebook id, or use search_notebook"
            return denied
        }
        await library.waitForSearch()
        let found = try await library.searchAcrossNotebooks(query, notebookIds: ids, topK: k(args, "k", 8))
        return ["results": found.hits.map(CrossNotebook.row), "notebooks_searched": found.notebooksSearched]
    }

    private func askNotebook(_ args: JSONBox, _ caller: CompanionIdentity) async throws -> [String: Any] {
        guard let question = args.string("question"), !question.isEmpty else { return ["error": "a question is required"] }
        var target = args.string("notebook_id") ?? ""
        var across = false
        if target.isEmpty {
            guard !library.notebooks.isEmpty else { return ["error": "there are no notebooks yet"] }
            if library.notebooks.count > 1 {
                guard caller.has("all_notebooks") else {
                    var denied = LocalAPI.scopeDenied("all_notebooks", companion: caller.companionId)
                    denied["hint"] = "pass notebook_id (see list_notebooks)"
                    return denied
                }
                across = true
            } else { target = library.notebooks[0].id }
        }
        if library.answering { return ["status": "busy", "retry_after": 15, "detail": "the person at this Mac is using the model"] }
        await library.waitForSearch()
        let topK = k(args, "top_k", 4, max: 12)
        if across {
            let out = try await library.answerAcrossNotebooks(question, topK: Swift.max(topK * 2, 8))
            let sources = out.sources.enumerated().map { i, h in
                ["number": i + 1, "notebook_id": h.notebookId, "notebook_title": h.notebookTitle, "source_id": h.sourceId, "filename": h.filename, "chunk_index": h.chunkIndex] as [String: Any]
            }
            return ["answer": orNull(out.sources.isEmpty ? nil : out.answer), "sources": sources, "notebooks_searched": out.notebooksSearched, "notebook_id": NSNull()]
        }
        guard library.notebooks.contains(where: { $0.id == target }) else { return ["error": "no notebook with id \(target) (see list_notebooks)"] }
        let out = try await library.localAnswer(question: question, notebook: target, topK: topK)
        let sources = out.sources.enumerated().map { i, h in
            ["number": i + 1, "source_id": h.sourceId, "filename": h.filename, "chunk_index": h.chunkIndex, "text": String(h.text.prefix(600)), "confidence": Double(h.score)] as [String: Any]
        }
        return ["answer": out.answer, "sources": sources, "notebook_id": target]
    }

    /// Which notebook holds a source, and its row.
    private func locate(source id: String) async -> (notebook: String, info: LibraryStore.SourceInfo)? {
        for nb in library.notebooks {
            if let s = await library.sources(in: nb.id).first(where: { $0.id == id }) { return (nb.id, s) }
        }
        return nil
    }

    private func getSource(_ args: JSONBox) async -> [String: Any] {
        guard let id = args.string("source_id"), !id.isEmpty else { return ["error": "source_id is required"] }
        guard let found = await locate(source: id) else { return ["error": "no source with id \(id)"] }
        let notebook = found.notebook, info = found.info
        let content = await library.sourceText(notebook: notebook, sourceId: id)
        let offset = Swift.max(0, args.int("offset") ?? 0)
        let maxChars = Swift.max(200, Swift.min(args.int("max_chars") ?? Self.defaultChars, 50_000))
        let chars = Array(content)
        let start = Swift.min(offset, chars.count)
        let end = Swift.min(start + maxChars, chars.count)
        let page = String(chars[start..<end])
        let url = await library.sourceURL(notebook: notebook, sourceId: id)
        return ["source_id": id, "title": info.name, "type": info.type, "url": orNull(url),
                "total_chars": chars.count, "offset": start, "text": page, "next_offset": orNull(end < chars.count ? end : nil)]
    }

    private struct NoteRow { let id: String, notebookId: String, title: String, text: String, updated: String }

    private func notes(in notebook: String?) async -> [NoteRow] {
        var rows: [NoteRow] = []
        for n in await library.localNotes() where notebook == nil || n.notebookId == notebook {
            rows.append(NoteRow(id: n.id, notebookId: n.notebookId, title: n.title, text: n.text, updated: ""))
        }
        if let store = library.store {
            for n in await store.importedNotes(notebook: notebook) {
                rows.append(NoteRow(id: n.id, notebookId: n.notebookId, title: n.title, text: n.text, updated: n.created))
            }
        }
        return rows
    }

    private func noteRow(_ n: NoteRow, withNotebook: Bool) -> [String: Any] {
        var r: [String: Any] = ["id": n.id, "title": n.title, "tags": [String](), "updated_at": orNull(n.updated.isEmpty ? nil : n.updated), "preview": String(n.text.prefix(200))]
        if withNotebook { r["notebook_id"] = n.notebookId }
        return r
    }

    private func listNotes(_ args: JSONBox) async -> [String: Any] {
        guard let notebook = args.string("notebook_id"), !notebook.isEmpty else { return ["error": "notebook_id is required"] }
        let rows = await notes(in: notebook)
        return ["notebook_id": notebook, "total": rows.count, "notes": rows.prefix(k(args, "n", 20, max: 100)).map { noteRow($0, withNotebook: false) }]
    }

    private func getNote(_ args: JSONBox) async -> [String: Any] {
        guard let id = args.string("note_id"), !id.isEmpty else { return ["error": "note_id is required"] }
        guard let n = await notes(in: nil).first(where: { $0.id == id }) else { return ["error": "no note with id \(id)"] }
        let maxChars = Swift.max(200, Swift.min(args.int("max_chars") ?? Self.noteChars, Self.noteChars))
        let when = orNull(n.updated.isEmpty ? nil : n.updated)
        return ["id": n.id, "title": n.title, "notebook_id": n.notebookId, "tags": [String](), "created_at": when,
                "updated_at": when, "text": String(n.text.prefix(maxChars)), "total_chars": n.text.count, "truncated": n.text.count > maxChars]
    }

    private func recentNotes(_ args: JSONBox) async -> [String: Any] {
        ["notes": await notes(in: nil).prefix(k(args, "n", 10, max: 100)).map { noteRow($0, withNotebook: true) }]
    }

    /// Only public web addresses: nothing on this Mac or its network is reachable through an agent.
    static func publicWebAddress(_ text: String) -> URL? {
        guard let url = URL(string: text), let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https", let host = url.host()?.lowercased() else { return nil }
        if host == "localhost" || host.hasSuffix(".local") || host.hasSuffix(".internal") || host == "0.0.0.0" { return nil }
        let parts = host.split(separator: ".").compactMap { Int($0) }
        if parts.count == 4 {
            if parts[0] == 10 || parts[0] == 127 || parts[0] == 0 || (parts[0] == 169 && parts[1] == 254) || (parts[0] == 172 && (16...31).contains(parts[1])) || (parts[0] == 192 && parts[1] == 168) { return nil }
        }
        if host.contains(":") { return nil }      // IPv6 literals: refused rather than classified
        return url
    }

    private func fetchPage(_ args: JSONBox) async throws -> [String: Any] {
        guard let raw = args.string("url"), let url = Self.publicWebAddress(raw) else { return ["error": "refused: only public http(s) addresses can be fetched"] }
        let maxChars = Swift.max(200, Swift.min(args.int("max_chars") ?? Self.defaultChars, 50_000))
        let fetched = try await WebExtract.fetch(url)
        let title: String, text: String
        switch fetched {
        case .text(let t, let body, _, _, _): title = t; text = body
        case .file(let file, let name):
            defer { try? FileManager.default.removeItem(at: file.deletingLastPathComponent()) }
            title = name; text = try await TextExtract.text(from: file)
        }
        return ["url": raw, "title": title, "text": String(text.prefix(maxChars)), "truncated": text.count > maxChars]
    }

    private func scholarly(_ args: JSONBox) async throws -> [String: Any] {
        guard let query = args.string("query"), !query.isEmpty else { return ["error": "a query is required"] }
        let n = k(args, "n", 5, max: 20)
        let source = args.string("source") ?? "all"
        let sites: [SiteSearch.Site]
        switch source {
        case "all": sites = [.semanticScholar, .arxiv]
        case "semanticscholar": sites = [.semanticScholar]
        case "arxiv": sites = [.arxiv]
        case "openalex": sites = [.openAlex]
        case "pubmed": sites = [.pubmed]
        default: return ["error": "source must be 'all' or one of arxiv, openalex, pubmed, semanticscholar"]
        }
        let (batches, _) = await SiteSearchClient.searchAll(sites, query: query, max: n)
        // Interleaved so neither index crowds the other out of the top n.
        var results: [SiteSearch.Result] = []
        var seen: Set<String> = []
        for i in 0..<(batches.map(\.count).max() ?? 0) {
            for b in batches where i < b.count && !seen.contains(b[i].url) { seen.insert(b[i].url); results.append(b[i]) }
        }
        return ["results": results.prefix(n).map { ["title": $0.title, "url": $0.url, "snippet": $0.snippet, "site": $0.site.domain, "published": orNull($0.published)] as [String: Any] }]
    }

    private func propose(_ args: JSONBox, _ caller: CompanionIdentity) -> [String: Any] {
        guard let notebook = args.string("notebook_id"), !notebook.isEmpty, library.notebooks.contains(where: { $0.id == notebook }) else {
            return ["error": "notebook_id must name one of the notebooks (see list_notebooks)"]
        }
        guard let title = args.string("title"), !title.isEmpty, let body = args.string("body"), !body.isEmpty else { return ["error": "a proposal needs both a title and a body"] }
        let source = args.string("source") ?? ""
        let (outcome, id) = proposals.add(.init(notebookId: notebook, title: title, body: body, source: source.isEmpty ? "proposed by \(caller.companionId)" : source, companionId: caller.companionId))
        NotificationCenter.default.post(name: .proposalsChanged, object: nil)
        return ["outcome": outcome, "item_id": id,
                "detail": outcome == "queued" ? "waiting for the user to approve it in LocalBook (Settings › Agents)" : "already known or a duplicate"]
    }

    private func queue(_ args: JSONBox) -> [String: Any] {
        let status = Proposals.Status(rawValue: args.string("status") ?? "queued") ?? .queued
        let rows = proposals.all(status: status).prefix(k(args, "k", 20, max: 100)).map {
            ["id": $0.id, "notebook_id": $0.notebookId, "title": $0.title, "preview": String($0.body.prefix(300)), "source": $0.source, "proposed_by": $0.companionId, "created_at": $0.created, "status": $0.status.rawValue] as [String: Any]
        }
        return ["status": status.rawValue, "items": rows]
    }

    private func memorySearch(_ args: JSONBox) async throws -> [String: Any] {
        guard let memory else { return ["error": "memory is not available"] }
        guard let query = args.string("query") else { return ["error": "a query is required"] }
        return try await memory.prefetch(query, k: k(args, "k", 8), charBudget: Swift.max(100, Swift.min(args.int("char_budget") ?? 2_000, 20_000)))
    }

    private func memoryAdd(_ args: JSONBox, _ caller: CompanionIdentity) async throws -> [String: Any] {
        guard let memory else { return ["error": "memory is not available"] }
        guard let text = args.string("text"), !text.isEmpty else { return ["error": "text is required"] }
        let m = try await memory.add(.archival, text: text, category: args.string("category") ?? "", source: caller.companionId)
        NotificationCenter.default.post(name: .memoryChanged, object: nil)
        return ["id": m.id, "stored": true, "source": m.source, "category": m.category]
    }

    // MARK: /v1

    public func models() async -> [String] { [AnswerModels.chosenId] }

    /// The loaded answer model writes the reply; while the person at this Mac is mid-answer the call waits a little,
    /// then gives up (the caller sees a 503 and tries again).
    public func complete(system: String, prompt: String, maxTokens: Int, temperature: Double?) async throws -> String? {
        var waited = 0
        while library.answering, waited < 20 { try await Task.sleep(for: .seconds(1)); waited += 1 }
        guard !library.answering else { return nil }
        let container: ModelContainer
        if let loaded = await library.plainAnswerModel() { container = loaded } else { container = try await AnswerModels.loadContainer() }
        var params = GenerateParameters(maxTokens: Swift.max(16, Swift.min(maxTokens, 4_096)))
        if let temperature { params.temperature = Float(temperature) }
        let session = ChatSession(container, instructions: system.isEmpty ? nil : system, generateParameters: params)
        var text = ""
        for try await item in session.streamDetails(to: prompt) { if case .chunk(let c) = item { text += c } }
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    // MARK: the browser extension

    public func notebooks() async -> [LocalAPI.NotebookRow] {
        var rows: [LocalAPI.NotebookRow] = []
        for nb in library.notebooks { rows.append(.init(id: nb.id, name: nb.title, sourceCount: await library.notebookSummary(nb.id).sources)) }
        return rows
    }

    static func normalize(_ u: String) -> String { u.split(separator: "#", maxSplits: 1).first.map(String.init)?.trimmingCharacters(in: CharacterSet(charactersIn: "/")).lowercased() ?? "" }

    public func sourceExists(notebook: String, url: String) async -> LocalAPI.Existing? {
        let target = Self.normalize(url)
        guard !target.isEmpty else { return nil }
        for s in await library.sources(in: notebook) {
            guard let stored = await library.sourceURL(notebook: notebook, sourceId: s.id) else { continue }
            if stored == url || Self.normalize(stored) == target { return .init(sourceId: s.id, title: s.name) }
        }
        return nil
    }

    public func capture(_ c: LocalAPI.Capture) async -> LocalAPI.Captured {
        guard library.notebooks.contains(where: { $0.id == c.notebookId }) else { return .init(success: false, sourceId: nil, title: c.title, wordCount: 0, error: "That notebook is not in LocalBook") }
        if let existing = await sourceExists(notebook: c.notebookId, url: c.url), c.kind != .selection {
            return .init(success: true, sourceId: existing.sourceId, title: existing.title, wordCount: 0, error: nil)
        }
        do {
            let report: IngestReport
            switch c.kind {
            case .youtube:
                guard let url = URL(string: c.url) else { return .init(success: false, sourceId: nil, title: c.title, wordCount: 0, error: "Not a web address") }
                report = try await library.ingestLink(url, notebook: c.notebookId)
            case .page:
                // The extension read the page in the browser (logged in, scripts run); a thin read means a page that
                // only renders for a real visit, and the app's own loader has a better chance.
                if c.text.count < 300, let url = URL(string: c.url) {
                    report = try await library.ingestLink(url, notebook: c.notebookId)
                } else {
                    report = try await library.ingestText(c.text, title: c.title.isEmpty ? c.url : c.title, format: "web", notebook: c.notebookId, url: c.url)
                }
            case .selection:
                let title = "Selection: " + (c.title.isEmpty ? c.url : c.title)
                report = try await library.ingestText(c.text, title: title, format: "web", notebook: c.notebookId, url: c.url)
            }
            NotificationCenter.default.post(name: Notification.Name("LocalBook.librarySynced"), object: nil)
            let words = c.text.isEmpty ? report.characters / 6 : c.text.split { $0.isWhitespace }.count
            return .init(success: true, sourceId: report.sourceId, title: report.title, wordCount: words, error: nil)
        } catch {
            return .init(success: false, sourceId: nil, title: c.title, wordCount: 0, error: error.localizedDescription)
        }
    }

    public func captureStatus(notebook: String, sourceId: String) async -> LocalAPI.CaptureStatus? {
        guard let s = await library.sources(in: notebook).first(where: { $0.id == sourceId }) else { return nil }
        return .init(status: "completed", title: s.name, chunks: s.pieces, wordCount: s.characters / 6)
    }
}

public extension Notification.Name {
    static let proposalsChanged = Notification.Name("LocalBook.proposalsChanged")
    static let memoryChanged = Notification.Name("LocalBook.memoryChanged")
}
