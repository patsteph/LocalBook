import Foundation
import Testing
@testable import LocalBookCore

/// The loopback API's pure parts: keys (the old file layout), the router's refusals, MCP over JSON-RPC, `/v1` shapes,
/// the browser extension's token dance, memory and the cross-notebook rules.
@Suite("Local API")
struct LocalAPITests {
    private func temp(_ name: String) -> URL {
        let dir = FileManager.default.temporaryDirectory.appending(path: "lb-tests-\(UUID().uuidString)")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appending(path: name)
    }

    /// Answers like the app would, with fixed data.
    final class StubDelegate: LocalAPIDelegate {
        func tool(_ name: String, args: JSONBox, caller: CompanionIdentity) async -> JSONBox {
            switch name {
            case "list_notebooks": return JSONBox(["notebooks": [["id": "nb1", "title": "Coffee", "source_count": 2]]])
            case "search_notebook": return JSONBox(["notebook_id": args.string("notebook_id") ?? "", "results": [["source_id": "s1", "text": "beans", "score": 0.9]]])
            default: return .error("stub has no \(name)")
            }
        }
        func models() async -> [String] { ["mlx-community/gemma-4-e4b-it-4bit"] }
        func complete(system: String, prompt: String, maxTokens: Int, temperature: Double?) async throws -> String? { "Hello from \(system.isEmpty ? "nobody" : "system"): \(prompt)" }
        func notebooks() async -> [LocalAPI.NotebookRow] { [.init(id: "nb1", name: "Coffee", sourceCount: 2)] }
        func sourceExists(notebook: String, url: String) async -> LocalAPI.Existing? { url.contains("known") ? .init(sourceId: "s1", title: "Known") : nil }
        func capture(_ request: LocalAPI.Capture) async -> LocalAPI.Captured { .init(success: true, sourceId: "local:file-1", title: request.title, wordCount: request.text.split(separator: " ").count) }
        func captureStatus(notebook: String, sourceId: String) async -> LocalAPI.CaptureStatus? { sourceId == "local:file-1" ? .init(status: "completed", title: "T", chunks: 3, wordCount: 40) : nil }
    }

    private func api(_ keys: CompanionKeys) -> LocalAPI {
        LocalAPI(keys: keys, audit: CompanionAudit(url: temp("calls.json")), appToken: "tok-123", version: "3.0-test", delegate: StubDelegate())
    }

    private func request(_ method: String, _ path: String, headers: [String: String] = [:], json: Any? = nil, host: String = "127.0.0.1:8000") -> HTTPRequest {
        var h = headers; h["host"] = host
        var r = HTTPRequest(method: method, path: path, headers: h, body: json.map { try! JSONSerialization.data(withJSONObject: $0) } ?? Data())
        if let q = path.firstIndex(of: "?") { r.path = String(path[..<q]); r.query = HTTPWire.queryPairs(String(path[path.index(after: q)...])) }
        return r
    }

    private func parse(_ reply: Data) -> (status: Int, headers: [String: String], body: Any?) {
        let text = String(decoding: reply, as: UTF8.self)
        let parts = text.components(separatedBy: "\r\n\r\n")
        let lines = parts[0].components(separatedBy: "\r\n")
        let status = Int(lines[0].split(separator: " ")[1])!
        var headers: [String: String] = [:]
        for l in lines.dropFirst() { if let c = l.firstIndex(of: ":") { headers[l[..<c].lowercased()] = l[l.index(after: c)...].trimmingCharacters(in: .whitespaces) } }
        let bodyData = reply.suffix(from: reply.range(of: Data("\r\n\r\n".utf8))!.upperBound)
        return (status, headers, try? JSONSerialization.jsonObject(with: Data(bodyData)))
    }

    // MARK: keys

    @Test("a key is lb- + 43 url-safe characters, stored as sha256(salt + token) in the old file layout, verified in constant time, revocable")
    func keys() throws {
        let store = CompanionKeys(url: temp("companion_keys.json"))
        let token = try store.issue("claude-code", scopes: ["mcp", "all_notebooks"])
        #expect(token.hasPrefix("lb-"))
        #expect(token.count == 46)
        #expect(token.allSatisfy { $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" })
        let who = try #require(store.verify(token))
        #expect(who.companionId == "claude-code")
        #expect(who.has("mcp") && who.has("all_notebooks") && !who.has("llm"))
        #expect(store.verify("lb-nope") == nil)
        #expect(store.verify("") == nil)
        let rec = try #require(store.list().first)
        #expect(rec.scopes == ["mcp", "all_notebooks"])
        #expect(rec.lastUsedAt != nil)
        #expect(store.revoke("claude-code"))
        #expect(store.verify(token) == nil)
        #expect(throws: CompanionKeys.KeyError.self) { try store.issue("x", scopes: ["admin"]) }
        #expect(throws: CompanionKeys.KeyError.self) { try store.issue("  ", scopes: ["mcp"]) }
    }

    @Test("a v1 file written by the old app is read, and its mcp keys gain all_notebooks as the old upgrade did")
    func oldFile() throws {
        let url = temp("companion_keys.json")
        let salt = "0123456789abcdef0123456789abcdef"
        let token = "lb-oldtoken"
        let json: [String: Any] = ["version": 1, "companions": ["jocasta": ["salt": salt, "key_hash": CompanionKeys.hash(salt: salt, token: token), "scopes": ["mcp"], "created_at": "2026-11-06T00:00:00Z", "last_used_at": NSNull()]]]
        try JSONSerialization.data(withJSONObject: json).write(to: url)
        let store = CompanionKeys(url: url)
        let who = try #require(store.verify(token))
        #expect(who.companionId == "jocasta")
        #expect(who.has("all_notebooks"))
    }

    @Test("the bearer header is read case-insensitively")
    func bearer() {
        #expect(CompanionKeys.bearer("Bearer lb-abc") == "lb-abc")
        #expect(CompanionKeys.bearer("bearer  lb-abc ") == "lb-abc")
        #expect(CompanionKeys.bearer("Basic xyz") == nil)
        #expect(CompanionKeys.bearer(nil) == nil)
    }

    // MARK: who may call

    @Test("only this Mac may call, Host must be loopback, and a web page's Origin is refused")
    func callers() async throws {
        let api = api(CompanionKeys(url: temp("k.json")))
        var r = parse(await api.handle(request("GET", "/health"), remoteHost: "192.168.1.9"))
        #expect(r.status == 403)
        r = parse(await api.handle(request("GET", "/health", host: "mymac.local:8000"), remoteHost: "127.0.0.1"))
        #expect(r.status == 403)
        r = parse(await api.handle(request("GET", "/health", headers: ["origin": "https://evil.example"]), remoteHost: "127.0.0.1"))
        #expect(r.status == 403)
        r = parse(await api.handle(request("GET", "/health"), remoteHost: "::1"))
        #expect(r.status == 200)
        r = parse(await api.handle(request("GET", "/health", host: "localhost:8000"), remoteHost: "127.0.0.1"))
        #expect(r.status == 200)
        r = parse(await api.handle(request("GET", "/nowhere"), remoteHost: "127.0.0.1"))
        #expect(r.status == 404)
    }

    // MARK: MCP

    @Test("MCP: no key is 401, a key without mcp is 403, then initialize, tools/list, tools/call and a notification work over JSON-RPC")
    func mcp() async throws {
        let keys = CompanionKeys(url: temp("k.json"))
        let api = api(keys)
        var r = parse(await api.handle(request("POST", "/mcp", json: ["jsonrpc": "2.0", "id": 1, "method": "initialize"]), remoteHost: "127.0.0.1"))
        #expect(r.status == 401)
        let llmOnly = try keys.issue("notes", scopes: ["llm"])
        r = parse(await api.handle(request("POST", "/mcp", headers: ["authorization": "Bearer \(llmOnly)"], json: ["jsonrpc": "2.0", "id": 1, "method": "initialize"]), remoteHost: "127.0.0.1"))
        #expect(r.status == 403)
        #expect(((r.body as? [String: Any])?["detail"] as? String)?.contains("'mcp' scope") == true)

        let token = try keys.issue("claude-code", scopes: ["mcp"])
        let auth = ["authorization": "Bearer \(token)", "accept": "application/json, text/event-stream"]
        r = parse(await api.handle(request("POST", "/mcp", headers: auth, json: ["jsonrpc": "2.0", "id": 1, "method": "initialize", "params": ["protocolVersion": "2025-06-18", "clientInfo": ["name": "test"]]]), remoteHost: "127.0.0.1"))
        #expect(r.status == 200)
        #expect(r.headers["mcp-session-id"]?.isEmpty == false)
        let opened = try #require((r.body as? [String: Any])?["result"] as? [String: Any])
        #expect(opened["protocolVersion"] as? String == "2025-06-18")
        #expect((opened["serverInfo"] as? [String: Any])?["name"] as? String == "LocalBook")
        #expect((opened["instructions"] as? String)?.contains("one notebook at a time") == true)

        r = parse(await api.handle(request("POST", "/mcp", headers: auth, json: ["jsonrpc": "2.0", "method": "notifications/initialized"]), remoteHost: "127.0.0.1"))
        #expect(r.status == 202)

        r = parse(await api.handle(request("POST", "/mcp", headers: auth, json: ["jsonrpc": "2.0", "id": 2, "method": "tools/list"]), remoteHost: "127.0.0.1"))
        let tools = try #require(((r.body as? [String: Any])?["result"] as? [String: Any])?["tools"] as? [[String: Any]])
        let names = Set(tools.compactMap { $0["name"] as? String })
        for wanted in ["list_notebooks", "search_notebook", "search_notebooks", "ask_notebook", "list_sources", "get_source", "list_notes", "get_note",
                       "list_recent_notes", "fetch_page", "web_search", "youtube_search", "scholarly_search", "propose_note", "memory_search", "memory_add", "events_since", "start_research"] {
            #expect(names.contains(wanted), "missing \(wanted)")
        }
        let search = try #require(tools.first { $0["name"] as? String == "search_notebook" })
        let schema = try #require(search["inputSchema"] as? [String: Any])
        #expect((schema["required"] as? [String]) == ["notebook_id", "query"])
        #expect((search["annotations"] as? [String: Any])?["readOnlyHint"] as? Bool == true)
        let propose = try #require(tools.first { $0["name"] as? String == "propose_note" })
        #expect((propose["annotations"] as? [String: Any])?["readOnlyHint"] as? Bool == false)

        r = parse(await api.handle(request("POST", "/mcp", headers: auth, json: ["jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": ["name": "search_notebook", "arguments": ["notebook_id": "nb1", "query": "beans"]]]), remoteHost: "127.0.0.1"))
        let result = try #require((r.body as? [String: Any])?["result"] as? [String: Any])
        #expect(result["isError"] == nil)
        let structured = try #require(result["structuredContent"] as? [String: Any])
        #expect(structured["notebook_id"] as? String == "nb1")
        let content = try #require(result["content"] as? [[String: Any]])
        #expect((content.first?["text"] as? String)?.contains("beans") == true)

        // A tool the key's scope does not cover is a result with an error, not an HTTP refusal; a Curator tool says not yet.
        r = parse(await api.handle(request("POST", "/mcp", headers: auth, json: ["jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": ["name": "memory_search", "arguments": ["query": "x"]]]), remoteHost: "127.0.0.1"))
        var res = try #require((r.body as? [String: Any])?["result"] as? [String: Any])
        #expect(res["isError"] as? Bool == true)
        #expect(((res["structuredContent"] as? [String: Any])?["error"] as? String)?.contains("'memory' scope") == true)
        r = parse(await api.handle(request("POST", "/mcp", headers: auth, json: ["jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": ["name": "todays_digest", "arguments": [:]]]), remoteHost: "127.0.0.1"))
        res = try #require((r.body as? [String: Any])?["result"] as? [String: Any])
        #expect(((res["structuredContent"] as? [String: Any])?["error"] as? String)?.contains("not in this version") == true)

        r = parse(await api.handle(request("POST", "/mcp", headers: auth, json: ["jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": ["name": "no_such_tool"]]), remoteHost: "127.0.0.1"))
        #expect(((r.body as? [String: Any])?["error"] as? [String: Any])?["code"] as? Int == -32602)
        r = parse(await api.handle(request("POST", "/mcp", headers: auth, json: ["jsonrpc": "2.0", "id": 7, "method": "ping"]), remoteHost: "127.0.0.1"))
        #expect(r.status == 200)
        r = parse(await api.handle(request("GET", "/mcp", headers: auth), remoteHost: "127.0.0.1"))
        #expect(r.status == 405)
        r = parse(await api.handle(request("DELETE", "/mcp", headers: auth), remoteHost: "127.0.0.1"))
        #expect(r.status == 200)

        // Every call left an audit row with the arguments hashed, never the words.
        let calls = api.audit.recent()
        #expect(calls.count == 3)
        #expect(calls.contains { $0.tool == "search_notebook" && $0.outcome == .ok && $0.argsPreview == "notebook_id, query" })
        #expect(!calls.contains { $0.argsPreview.contains("beans") })
        #expect(calls.contains { $0.tool == "memory_search" && $0.outcome == .denied })
    }

    // MARK: /v1

    @Test("/v1: models and a completion in the OpenAI shape, streamed as one event body when asked")
    func v1() async throws {
        let keys = CompanionKeys(url: temp("k.json"))
        let api = api(keys)
        let token = try keys.issue("meeting-notes", scopes: ["llm"])
        let auth = ["authorization": "Bearer \(token)"]
        var r = parse(await api.handle(request("GET", "/v1/models"), remoteHost: "127.0.0.1"))
        #expect(r.status == 401)
        r = parse(await api.handle(request("GET", "/v1/models", headers: auth), remoteHost: "127.0.0.1"))
        let data = try #require((r.body as? [String: Any])?["data"] as? [[String: Any]])
        #expect(data.first?["id"] as? String == "mlx-community/gemma-4-e4b-it-4bit")
        #expect(data.first?["localbook_role"] as? String == "main")

        let messages: [[String: Any]] = [["role": "system", "content": "Be brief."], ["role": "user", "content": [["type": "text", "text": "Hi there"]]]]
        r = parse(await api.handle(request("POST", "/v1/chat/completions", headers: auth, json: ["model": "gpt-4o", "messages": messages]), remoteHost: "127.0.0.1"))
        #expect(r.status == 200)
        let body = try #require(r.body as? [String: Any])
        #expect(body["object"] as? String == "chat.completion")
        #expect(body["model"] as? String == "mlx-community/gemma-4-e4b-it-4bit")    // an unknown name serves the main model
        let choice = try #require((body["choices"] as? [[String: Any]])?.first)
        #expect(((choice["message"] as? [String: Any])?["content"] as? String) == "Hello from system: Hi there")
        #expect(choice["finish_reason"] as? String == "stop")
        #expect((body["usage"] as? [String: Any])?["total_tokens"] != nil)

        let streamed = await api.handle(request("POST", "/v1/chat/completions", headers: auth, json: ["messages": messages, "stream": true]), remoteHost: "127.0.0.1")
        let text = String(decoding: streamed, as: UTF8.self)
        #expect(text.contains("Content-Type: text/event-stream"))
        #expect(text.contains("\"delta\":{\"role\":\"assistant\"}") || text.contains("\"delta\": {\"role\": \"assistant\"}") || text.contains("\"role\":\"assistant\""))
        #expect(text.contains("Hello from system"))
        #expect(text.hasSuffix("data: [DONE]\n\n"))

        r = parse(await api.handle(request("POST", "/v1/chat/completions", headers: auth, json: ["messages": messages, "tools": [["type": "function"]]]), remoteHost: "127.0.0.1"))
        #expect(r.status == 400)
        r = parse(await api.handle(request("POST", "/v1/chat/completions", headers: auth, json: ["messages": [[String: Any]]()]), remoteHost: "127.0.0.1"))
        #expect(r.status == 400)
    }

    @Test("the message list collapses as the old app's _split_messages did")
    func split() {
        let (system, prompt) = LocalAPI.splitMessages([["role": "system", "content": "S"], ["role": "user", "content": "a"], ["role": "assistant", "content": "b"], ["role": "user", "content": "c"]])
        #expect(system == "S")
        #expect(prompt == "User: a\n\nAssistant: b\n\nUser: c")
        #expect(LocalAPI.splitMessages([["role": "user", "content": "only"]]).prompt == "only")
    }

    // MARK: the browser extension

    @Test("the extension bootstraps its token by Origin, then captures with X-LocalBook-Token; every route refuses without it")
    func browser() async throws {
        let api = api(CompanionKeys(url: temp("k.json")))
        var r = parse(await api.handle(request("POST", "/auth/bootstrap", headers: ["origin": "chrome-extension://someoneelse"], json: [:]), remoteHost: "127.0.0.1"))
        #expect(r.status == 403)
        r = parse(await api.handle(request("POST", "/auth/bootstrap", headers: ["origin": "chrome-extension://\(LocalAPI.extensionId)"], json: [:]), remoteHost: "127.0.0.1"))
        #expect(r.status == 200)
        #expect((r.body as? [String: Any])?["token"] as? String == "tok-123")

        r = parse(await api.handle(request("GET", "/browser/status"), remoteHost: "127.0.0.1"))
        #expect(r.status == 401)
        let tok = ["x-localbook-token": "tok-123", "origin": "chrome-extension://\(LocalAPI.extensionId)"]
        r = parse(await api.handle(request("GET", "/browser/status", headers: tok), remoteHost: "127.0.0.1"))
        #expect((r.body as? [String: Any])?["status"] as? String == "online")
        r = parse(await api.handle(request("GET", "/browser/notebooks", headers: tok), remoteHost: "127.0.0.1"))
        let rows = try #require(r.body as? [[String: Any]])
        #expect(rows.first?["name"] as? String == "Coffee" && rows.first?["source_count"] as? Int == 2)
        r = parse(await api.handle(request("GET", "/browser/exists?notebook_id=nb1&url=https%3A%2F%2Fx.example%2Fknown", headers: tok), remoteHost: "127.0.0.1"))
        #expect((r.body as? [String: Any])?["exists"] as? Bool == true)
        r = parse(await api.handle(request("GET", "/browser/exists?notebook_id=nb1&url=https://x.example/new", headers: tok), remoteHost: "127.0.0.1"))
        #expect((r.body as? [String: Any])?["exists"] as? Bool == false)
        r = parse(await api.handle(request("POST", "/browser/capture", headers: tok, json: ["url": "https://x.example/a", "title": "A page", "content": "one two three four", "notebook_id": "nb1", "capture_type": "page"]), remoteHost: "127.0.0.1"))
        let cap = try #require(r.body as? [String: Any])
        #expect(cap["success"] as? Bool == true && cap["word_count"] as? Int == 4 && cap["status"] as? String == "completed")
        r = parse(await api.handle(request("POST", "/browser/capture/selection", headers: tok, json: ["url": "https://x.example/a", "title": "A", "selected_text": "just this", "notebook_id": "nb1"]), remoteHost: "127.0.0.1"))
        #expect((r.body as? [String: Any])?["success"] as? Bool == true)
        r = parse(await api.handle(request("GET", "/browser/capture-status/nb1/local:file-1", headers: tok), remoteHost: "127.0.0.1"))
        #expect((r.body as? [String: Any])?["chunks"] as? Int == 3)
        r = parse(await api.handle(request("GET", "/browser/capture-status/nb1/nope", headers: tok), remoteHost: "127.0.0.1"))
        #expect(r.status == 404)
        r = parse(await api.handle(request("GET", "/browser/pending-scrapes", headers: tok), remoteHost: "127.0.0.1"))
        #expect((r.body as? [String: Any])?["count"] as? Int == 0)
        #expect(LocalAPI.isYouTube("https://www.youtube.com/watch?v=abc") && LocalAPI.isYouTube("https://youtu.be/abc") && !LocalAPI.isYouTube("https://x.example"))
    }

    @Test("the query string is read off the request line, decoded")
    func query() throws {
        let head = "GET /browser/exists?notebook_id=nb%201&url=https%3A%2F%2Fa.b%2Fc%3Fd%3D1&flag HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
        let (req, _) = try #require(try HTTPWire.parse(Data(head.utf8)))
        #expect(req.path == "/browser/exists")
        #expect(req.query["notebook_id"] == "nb 1")
        #expect(req.query["url"] == "https://a.b/c?d=1")
        #expect(req.query["flag"] == "")
        let reply = String(decoding: HTTPWire.response(status: 200, body: Data("x".utf8), contentType: "text/event-stream", headers: ["Mcp-Session-Id": "s1"]), as: UTF8.self)
        #expect(reply.contains("Content-Type: text/event-stream\r\n") && reply.contains("Mcp-Session-Id: s1\r\n") && reply.hasSuffix("\r\n\r\nx"))
    }

    // MARK: proposals and audit

    @Test("a proposal is queued once; the same one again is skipped; approving or dismissing changes its status")
    func proposals() {
        let p = Proposals(url: temp("proposals.json"))
        let a = p.add(.init(notebookId: "nb1", title: "T", body: "B", source: "x", companionId: "claude"))
        #expect(a.outcome == "queued")
        let b = p.add(.init(notebookId: "nb1", title: "T", body: "B", source: "x", companionId: "claude"))
        #expect(b.outcome == "skipped" && b.id == a.id)
        #expect(p.all(status: .queued).count == 1)
        p.setStatus(a.id, .stored)
        #expect(p.all(status: .queued).isEmpty && p.all(status: .stored).count == 1)
    }

    @Test("the audit keeps newest first, hashes the arguments, and purges per companion")
    func audit() {
        let a = CompanionAudit(url: temp("calls.json"))
        a.record(companion: "x", tool: "search_notebook", args: ["query": "secret words", "notebook_id": "nb"], milliseconds: 12, outcome: .ok)
        a.record(companion: "y", tool: "ask_notebook", args: ["question": "q"], milliseconds: 900, outcome: .busy)
        let rows = a.recent()
        #expect(rows.count == 2 && rows[0].companionId == "y")
        #expect(rows[1].argsPreview == "notebook_id, query" && !rows[1].argsHash.isEmpty && rows[1].argsHash.count == 16)
        #expect(a.recent(companion: "x").count == 1)
        #expect(a.purge(companion: "x") == 1)
        #expect(a.recent().count == 1)
    }

    // MARK: memory

    @Test("memory stores core and archival rows, finds them by word overlap within a budget, and forgets one source at once")
    func memory() async throws {
        let m = try MemoryStore(path: temp("memory.sqlite"))
        _ = try await m.add(.core, text: "Patrick prefers short answers in British English", category: "preference", source: "you")
        _ = try await m.add(.archival, text: "The coffee notebook tracks roast schedules for the Harbor Lights cafe", category: "", source: "claude-code")
        _ = try await m.add(.archival, text: "The coffee notebook tracks roast schedules for the Harbor Lights cafe", category: "project", source: "claude-code")   // a twin refreshes
        #expect(await m.count() == 2)
        let hits = try await m.search("what does Patrick prefer")
        #expect(hits.first?.memory.category == "preference")
        let out = try await m.prefetch("coffee roast", k: 5, charBudget: 2_000).value
        let sections = out["sections"] as! [[String: Any]]
        #expect(sections.first?["kind"] as? String == "archival")
        #expect(out["chars"] as? Int ?? 0 > 0)
        let tight = try await m.prefetch("coffee roast", k: 5, charBudget: 10).value
        #expect((tight["sections"] as! [[String: Any]]).isEmpty)
        #expect(try await m.deleteAll(source: "claude-code") == 1)
        #expect(await m.count() == 1)
    }

    // MARK: cross-notebook

    @Test("@curator is recognised with or without punctuation, the merge keeps 3 per notebook by score, and the context is numbered with notebooks named")
    func crossNotebook() {
        #expect(CrossNotebook.ask(in: "@curator what links these?") == "what links these?")
        #expect(CrossNotebook.ask(in: "@Curator: ?") == "?")
        #expect(CrossNotebook.ask(in: "@curator") == "")
        #expect(CrossNotebook.ask(in: "@curatorial studies") == nil)
        #expect(CrossNotebook.ask(in: "ask @curator") == nil)
        func hit(_ nb: String, _ i: Int, _ score: Float) -> LibraryHit {
            LibraryHit(notebookId: nb, notebookTitle: nb.uppercased(), sourceId: "s\(i)", chunkIndex: i, filename: "f\(i)", text: "text \(nb) \(i)", score: score)
        }
        let a = (0..<5).map { hit("a", $0, 0.9 - Float($0) * 0.1) }
        let b = (0..<2).map { hit("b", $0, 0.85 - Float($0) * 0.3) }
        let merged = CrossNotebook.merge([a, b], topK: 4)
        #expect(merged.map(\.id) == ["a/s0/0", "b/s0/0", "a/s1/1", "a/s2/2"])
        #expect(CrossNotebook.merge([a], topK: 8).count == 3)
        let ctx = CrossNotebook.context(merged)
        #expect(ctx.hasPrefix("[1] A / f0: text a 0"))
        #expect(ctx.contains("[2] B / f0: text b 0"))
        #expect(CrossNotebook.context(merged, maxChars: 30).components(separatedBy: "\n\n").count == 1)
        #expect(CrossNotebook.status(notebooksSearched: 3, hits: merged, seconds: 1.26) == "Curator read 3 notebooks in 1.3 s · cited A, B")
    }
}
