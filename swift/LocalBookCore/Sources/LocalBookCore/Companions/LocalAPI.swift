import Foundation

/// What the local API needs from the app: the library behind the MCP tools, the answer model behind `/v1`, and the
/// intake behind the browser extension. The app's `LocalAPIBridge` fills it on the main actor.
public protocol LocalAPIDelegate: Sendable {
    /// One MCP tool call. `caller` is the verified key; scope checks beyond `mcp` and the tool's own scope (the
    /// `all_notebooks` rule, which needs the notebook count) are the delegate's, with `LocalAPI.scopeDenied` wording.
    func tool(_ name: String, args: JSONBox, caller: CompanionIdentity) async -> JSONBox
    /// The model ids `/v1/models` lists; the first is the main model.
    func models() async -> [String]
    /// A completion for `/v1/chat/completions`; nil when the model gave nothing.
    func complete(system: String, prompt: String, maxTokens: Int, temperature: Double?) async throws -> String?
    /// `/browser/notebooks`.
    func notebooks() async -> [LocalAPI.NotebookRow]
    /// `/browser/exists`.
    func sourceExists(notebook: String, url: String) async -> LocalAPI.Existing?
    /// `/browser/capture`, `/browser/capture/selection`, `/browser/capture/youtube`: the text (or the address) becomes a source.
    func capture(_ request: LocalAPI.Capture) async -> LocalAPI.Captured
    /// `/browser/capture-status/...`.
    func captureStatus(notebook: String, sourceId: String) async -> LocalAPI.CaptureStatus?
}

/// The old app's loopback HTTP API, the parts other programs on this Mac call: `/mcp` (agents), `/v1` (OpenAI-style
/// companions), `/auth/bootstrap` + `/browser/*` (the shipped browser extension). Pure: a request in, the bytes of a
/// reply out; the router checks the caller (loopback, Host, Origin, key or token) before the delegate hears anything.
public final class LocalAPI: Sendable {
    public static let port: UInt16 = 8000
    public static let extensionId = "opnfhnhhcahpkglaepaplpafogdhemon"
    public static let mcpPath = "/mcp"

    public struct NotebookRow: Sendable { public let id: String, name: String, sourceCount: Int
        public init(id: String, name: String, sourceCount: Int) { self.id = id; self.name = name; self.sourceCount = sourceCount } }
    public struct Existing: Sendable { public let sourceId: String, title: String
        public init(sourceId: String, title: String) { self.sourceId = sourceId; self.title = title } }
    public struct Capture: Sendable {
        public enum Kind: String, Sendable { case page, selection, youtube }
        public let kind: Kind
        public let url: String
        public let title: String
        /// The page's text as the extension read it (page), the selected words (selection), or empty (youtube: the
        /// app fetches the transcript itself).
        public let text: String
        public let notebookId: String
        public init(kind: Kind, url: String, title: String, text: String, notebookId: String) {
            self.kind = kind; self.url = url; self.title = title; self.text = text; self.notebookId = notebookId
        }
    }
    public struct Captured: Sendable {
        public let success: Bool, sourceId: String?, title: String, wordCount: Int, error: String?
        public init(success: Bool, sourceId: String?, title: String, wordCount: Int, error: String? = nil) {
            self.success = success; self.sourceId = sourceId; self.title = title; self.wordCount = wordCount; self.error = error
        }
    }
    public struct CaptureStatus: Sendable { public let status: String, title: String, chunks: Int, wordCount: Int
        public init(status: String, title: String, chunks: Int, wordCount: Int) { self.status = status; self.title = title; self.chunks = chunks; self.wordCount = wordCount } }

    public let keys: CompanionKeys
    public let audit: CompanionAudit
    /// The per-launch token the browser extension fetches once from `/auth/bootstrap` (the old `.app_token`).
    public let appToken: String
    public let version: String
    private let delegate: any LocalAPIDelegate

    public init(keys: CompanionKeys, audit: CompanionAudit, appToken: String = LocalAPI.newToken(), version: String, delegate: any LocalAPIDelegate) {
        self.keys = keys; self.audit = audit; self.appToken = appToken; self.version = version; self.delegate = delegate
    }

    public static func newToken() -> String { CompanionKeys.randomToken() + CompanionKeys.randomToken() }

    // MARK: who may call

    static func isLoopback(_ host: String) -> Bool {
        let h = host.lowercased()
        return h == "127.0.0.1" || h == "::1" || h == "localhost" || h.hasPrefix("127.") || h == "::ffff:127.0.0.1"
    }

    static func hostAllowed(_ header: String?) -> Bool {
        guard let header, !header.isEmpty else { return true }
        var h = header.lowercased()
        if h.hasPrefix("[") { h = String(h.dropFirst()); if let close = h.firstIndex(of: "]") { h = String(h[..<close]) } }
        else if let colon = h.lastIndex(of: ":"), h.filter({ $0 == ":" }).count == 1 { h = String(h[..<colon]) }
        return isLoopback(h)
    }

    /// A web page may not call the API (its scripts could), the pinned extension and programs (no Origin) may.
    static func originAllowed(_ origin: String?) -> Bool {
        guard let origin, !origin.isEmpty, origin != "null" else { return true }
        return origin.lowercased() == "chrome-extension://\(extensionId)"
    }

    // MARK: replies

    static func json(_ status: Int, _ object: Any, headers: [String: String] = [:]) -> Data {
        let body = (try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])) ?? Data("{}".utf8)
        return HTTPWire.response(status: status, body: body, contentType: "application/json", headers: headers)
    }
    static func problem(_ status: Int, _ detail: String) -> Data { json(status, ["detail": detail]) }

    /// The old server's wording for a key without a scope, as a tool result (the call "succeeds" with an error body).
    public static func scopeDenied(_ scope: String, companion: String) -> [String: Any] {
        ["error": "This companion key does not carry the '\(scope)' scope. Reconnect \(companion) from Settings → Agents."]
    }

    // MARK: the router

    public func handle(_ req: HTTPRequest, remoteHost: String) async -> Data {
        guard Self.isLoopback(remoteHost), Self.hostAllowed(req.headers["host"]) else {
            return Self.problem(403, "LocalBook answers only to 127.0.0.1 or localhost")
        }
        guard Self.originAllowed(req.headers["origin"]) else { return Self.problem(403, "Requests from web pages are refused") }
        if req.method == "OPTIONS" { return Self.preflight() }
        switch req.path {
        case Self.mcpPath, Self.mcpPath + "/": return await mcp(req)
        case "/v1/models": return await models(req)
        case "/v1/chat/completions": return await completions(req)
        case "/auth/bootstrap": return bootstrap(req)
        case "/health", "/": return Self.json(200, ["status": "ok", "app": "LocalBook", "version": version])
        default:
            if req.path.hasPrefix("/browser/") { return await browser(req) }
            return Self.problem(404, "Not Found")
        }
    }

    static func preflight() -> Data {
        HTTPWire.response(status: 204, body: Data(), contentType: "text/plain",
                          headers: ["Access-Control-Allow-Origin": "chrome-extension://\(extensionId)",
                                    "Access-Control-Allow-Headers": "Content-Type, X-LocalBook-Token, Authorization",
                                    "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS"])
    }

    private func body(_ req: HTTPRequest) -> [String: Any] { (try? JSONSerialization.jsonObject(with: req.body) as? [String: Any]) ?? [:] }

    // MARK: /mcp

    private func mcp(_ req: HTTPRequest) async -> Data {
        guard let token = CompanionKeys.bearer(req.headers["authorization"]) else {
            return Self.problem(401, "Invalid companion key. LocalBook issues a key per agent in Settings → Agents.")
        }
        guard let who = keys.verify(token) else { return Self.problem(401, "Invalid companion key") }
        guard who.has("mcp") else { return Self.problem(403, "This companion key does not carry the 'mcp' scope. Issue it again with mcp ticked.") }
        let session = req.headers["mcp-session-id"] ?? MCPServer.sessionId()
        let headers = ["Mcp-Session-Id": session]
        switch req.method {
        case "DELETE": return Self.json(200, [String: Any](), headers: headers)
        case "GET": return Self.problem(405, "This server has no event stream; send JSON-RPC with POST")
        case "POST": break
        default: return Self.problem(405, "Method Not Allowed")
        }
        guard let parsed = try? JSONSerialization.jsonObject(with: req.body) else {
            return Self.json(400, ["jsonrpc": "2.0", "id": NSNull(), "error": ["code": -32700, "message": "Parse error"]], headers: headers)
        }
        let messages: [[String: Any]]
        if let one = parsed as? [String: Any] { messages = [one] }
        else if let many = parsed as? [[String: Any]] { messages = many }
        else { return Self.json(400, ["jsonrpc": "2.0", "id": NSNull(), "error": ["code": -32600, "message": "Invalid Request"]], headers: headers) }
        var replies: [[String: Any]] = []
        for message in messages {
            if let r = await MCPServer.reply(to: message, serverVersion: version, call: { name, args in await self.call(name, args, who) }) { replies.append(r) }
        }
        if replies.isEmpty { return HTTPWire.response(status: 202, body: Data(), contentType: "application/json", headers: headers) }
        let payload: Any = parsed is [Any] ? replies : replies[0]
        return Self.json(200, payload, headers: headers)
    }

    /// One tool call: the tool's own scope, the audit row, then the delegate.
    private func call(_ name: String, _ args: JSONBox, _ who: CompanionIdentity) async -> JSONBox {
        let started = Date()
        guard let tool = MCPCatalog.tool(named: name) else { return .error("unknown tool \(name)") }
        func done(_ out: JSONBox) -> JSONBox {
            let ms = Int(Date().timeIntervalSince(started) * 1000)
            let outcome: CompanionAudit.Outcome
            if out["error"] != nil { outcome = (out["error"] as? String)?.contains("scope") == true ? .denied : .error }
            else if out["status"] as? String == "busy" { outcome = .busy }
            else { outcome = .ok }
            audit.record(companion: who.companionId, tool: name, args: args.value, milliseconds: ms, outcome: outcome, detail: out["error"] as? String)
            return out
        }
        if let scope = tool.scope, !who.has(scope) { return done(JSONBox(Self.scopeDenied(scope, companion: who.companionId))) }
        if MCPCatalog.notYet.contains(name) { return done(.error("\(name) is not in this version of LocalBook yet; it arrives with the Curator")) }
        return done(await delegate.tool(name, args: args, caller: who))
    }

    // MARK: /v1

    private func companion(_ req: HTTPRequest, scope: String) -> (CompanionIdentity?, Data?) {
        guard let token = CompanionKeys.bearer(req.headers["authorization"]), let who = keys.verify(token) else {
            return (nil, Self.problem(401, "Invalid API key. LocalBook issues a companion key per tool; connect the tool from Settings → Agents."))
        }
        guard who.has(scope) else {
            return (nil, Self.problem(403, "This companion key does not carry the '\(scope)' scope. Reconnect \(who.companionId) from Settings → Agents."))
        }
        return (who, nil)
    }

    private func models(_ req: HTTPRequest) async -> Data {
        let (who, refusal) = companion(req, scope: "llm")
        if let refusal { return refusal }
        _ = who
        let now = Int(Date().timeIntervalSince1970)
        let list = await delegate.models().enumerated().map { i, m in
            ["id": m, "object": "model", "created": now, "owned_by": "localbook", "localbook_role": i == 0 ? "main" : "fast"] as [String: Any]
        }
        return Self.json(200, ["object": "list", "data": list])
    }

    /// The message list collapsed into (system, prompt) as `openai_compat._split_messages` did.
    public static func splitMessages(_ messages: [[String: Any]]) -> (system: String, prompt: String) {
        var system: [String] = [], convo: [String] = []
        for m in messages {
            let text = flatten(m["content"])
            guard !text.isEmpty else { continue }
            switch (m["role"] as? String ?? "user").lowercased() {
            case "system": system.append(text)
            case "assistant": convo.append("Assistant: \(text)")
            default: convo.append(messages.count <= 2 ? text : "User: \(text)")
            }
        }
        return (system.joined(separator: "\n\n"), convo.joined(separator: "\n\n"))
    }

    static func flatten(_ content: Any?) -> String {
        if let s = content as? String { return s }
        if let parts = content as? [Any] {
            return parts.compactMap { part -> String? in
                if let d = part as? [String: Any], d["type"] as? String == "text" { return d["text"] as? String ?? "" }
                return part as? String
            }.joined(separator: "\n")
        }
        return ""
    }

    private func completions(_ req: HTTPRequest) async -> Data {
        let (who, refusal) = companion(req, scope: "llm")
        if let refusal { return refusal }
        let b = body(req)
        if b["tools"] != nil || b["functions"] != nil { return Self.problem(400, "Tool/function calling is not supported by this endpoint.") }
        if let n = b["n"] as? Int, n > 1 { return Self.problem(400, "n > 1 is not supported.") }
        guard let messages = b["messages"] as? [[String: Any]], !messages.isEmpty else { return Self.problem(400, "messages is required") }
        let (system, prompt) = Self.splitMessages(messages)
        guard !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return Self.problem(400, "no user content in messages") }
        let served = await delegate.models()
        let model = served.contains(b["model"] as? String ?? "") ? (b["model"] as! String) : (served.first ?? "localbook")
        let maxTokens = (b["max_tokens"] as? Int) ?? 1024
        let temperature = b["temperature"] as? Double
        let started = Date()
        let text: String?
        do { text = try await delegate.complete(system: system, prompt: prompt, maxTokens: maxTokens, temperature: temperature) }
        catch { text = nil }
        let ms = Int(Date().timeIntervalSince(started) * 1000)
        audit.record(companion: who?.companionId ?? "?", tool: "chat/completions", args: ["chars": system.count + prompt.count, "model": model],
                     milliseconds: ms, outcome: text == nil ? .error : .ok)
        let created = Int(started.timeIntervalSince1970)
        let cid = "chatcmpl-" + String(UUID().uuidString.lowercased().replacingOccurrences(of: "-", with: "").prefix(24))
        let approx = (system.count + prompt.count) / 4
        if b["stream"] as? Bool == true {
            // One body holding every event: the first delta names the role, the words follow, then the stop and [DONE].
            var out = ""
            func event(_ o: [String: Any]) { if let d = try? JSONSerialization.data(withJSONObject: o) { out += "data: " + String(decoding: d, as: UTF8.self) + "\n\n" } }
            event(["id": cid, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [["index": 0, "delta": ["role": "assistant"], "finish_reason": NSNull()]]])
            if let text {
                var rest = Substring(text)
                while !rest.isEmpty {
                    let piece = String(rest.prefix(48)); rest = rest.dropFirst(48)
                    event(["id": cid, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [["index": 0, "delta": ["content": piece], "finish_reason": NSNull()]]])
                }
            }
            event(["id": cid, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [["index": 0, "delta": [String: Any](), "finish_reason": "stop"]]])
            out += "data: [DONE]\n\n"
            return HTTPWire.response(status: 200, body: Data(out.utf8), contentType: "text/event-stream")
        }
        guard let text, !text.isEmpty else {
            return Self.problem(503, "LocalBook's model produced no output. It may still be loading, or the request may be too long for its context window.")
        }
        return Self.json(200, ["id": cid, "object": "chat.completion", "created": created, "model": model,
                               "choices": [["index": 0, "finish_reason": "stop", "message": ["role": "assistant", "content": text]]],
                               "usage": ["prompt_tokens": approx, "completion_tokens": text.count / 4, "total_tokens": approx + text.count / 4]])
    }

    // MARK: the browser extension

    private func bootstrap(_ req: HTTPRequest) -> Data {
        guard req.method == "POST" else { return Self.problem(405, "Method Not Allowed") }
        guard req.headers["origin"]?.lowercased() == "chrome-extension://\(Self.extensionId)" else { return Self.problem(403, "Origin not permitted") }
        return Self.json(200, ["token": appToken], headers: ["Access-Control-Allow-Origin": "chrome-extension://\(Self.extensionId)"])
    }

    private func browser(_ req: HTTPRequest) async -> Data {
        guard req.headers["x-localbook-token"] == appToken else { return Self.problem(401, "Missing or invalid app token") }
        let cors = ["Access-Control-Allow-Origin": "chrome-extension://\(Self.extensionId)"]
        let b = body(req)
        func s(_ key: String) -> String { ((b[key] as? String) ?? req.query[key] ?? "").trimmingCharacters(in: .whitespacesAndNewlines) }
        switch (req.method, req.path) {
        case ("GET", "/browser/status"):
            return Self.json(200, ["status": "online", "version": version, "timestamp": ISO8601DateFormatter().string(from: Date())], headers: cors)
        case ("GET", "/browser/notebooks"):
            let rows = await delegate.notebooks().map { ["id": $0.id, "name": $0.name, "source_count": $0.sourceCount] as [String: Any] }
            return Self.json(200, rows, headers: cors)
        case ("GET", "/browser/exists"):
            guard !s("notebook_id").isEmpty, !s("url").isEmpty else { return Self.json(200, ["exists": false], headers: cors) }
            if let e = await delegate.sourceExists(notebook: s("notebook_id"), url: s("url")) {
                return Self.json(200, ["exists": true, "source_id": e.sourceId, "title": e.title, "status": "completed"], headers: cors)
            }
            return Self.json(200, ["exists": false], headers: cors)
        case ("POST", "/browser/capture"):
            guard !s("url").isEmpty, !s("notebook_id").isEmpty else { return Self.problem(400, "url and notebook_id are required") }
            let kind: Capture.Kind = Self.isYouTube(s("url")) ? .youtube : .page
            return await captured(Capture(kind: kind, url: s("url"), title: s("title"), text: s("content"), notebookId: s("notebook_id")), cors)
        case ("POST", "/browser/capture/selection"):
            guard !s("selected_text").isEmpty, !s("notebook_id").isEmpty else { return Self.problem(400, "selected_text and notebook_id are required") }
            return await captured(Capture(kind: .selection, url: s("url"), title: s("title"), text: s("selected_text"), notebookId: s("notebook_id")), cors)
        case ("POST", "/browser/capture/youtube"):
            guard !s("video_url").isEmpty, !s("notebook_id").isEmpty else { return Self.problem(400, "video_url and notebook_id are required") }
            return await captured(Capture(kind: .youtube, url: s("video_url"), title: "", text: "", notebookId: s("notebook_id")), cors)
        case ("GET", "/browser/pending-scrapes"):
            return Self.json(200, ["requests": [[String: Any]](), "count": 0], headers: cors)     // this Mac renders pages itself
        default:
            if req.method == "GET", req.path.hasPrefix("/browser/capture-status/") {
                let parts = req.path.dropFirst("/browser/capture-status/".count).split(separator: "/", maxSplits: 1).map(String.init)
                guard parts.count == 2 else { return Self.problem(404, "Source not found in this notebook") }
                let notebook = parts[0], sourceId = parts[1].removingPercentEncoding ?? parts[1]
                guard let st = await delegate.captureStatus(notebook: notebook, sourceId: sourceId) else { return Self.problem(404, "Source not found in this notebook") }
                return Self.json(200, ["source_id": sourceId, "status": st.status, "title": st.title, "chunks": st.chunks, "word_count": st.wordCount,
                                       "topics": [String](), "key_concepts": [String](), "summary_present": false, "error": NSNull()], headers: cors)
            }
            return Self.problem(404, "Not Found")
        }
    }

    static func isYouTube(_ url: String) -> Bool {
        url.range(of: #"(youtube\.com/(watch|shorts/|live/|embed/|v/)|youtu\.be/)"#, options: .regularExpression) != nil
    }

    private func captured(_ c: Capture, _ cors: [String: String]) async -> Data {
        let r = await delegate.capture(c)
        return Self.json(200, ["success": r.success, "source_id": orNull(r.sourceId), "title": r.title, "word_count": r.wordCount,
                               "reading_time_minutes": max(1, r.wordCount / 200), "summary": NSNull(), "key_concepts": [String](),
                               "error": orNull(r.error), "status": "completed"], headers: cors)
    }
}
