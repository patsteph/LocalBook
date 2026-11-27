import Foundation

/// A JSON object carried across isolation boundaries. `[String: Any]` is not `Sendable`; this box says the dictionary
/// holds only what `JSONSerialization` makes (strings, numbers, booleans, arrays, dictionaries, null), which is.
public struct JSONBox: @unchecked Sendable {
    public let value: [String: Any]
    public init(_ value: [String: Any]) { self.value = value }
    public subscript(_ key: String) -> Any? { value[key] }
    public func string(_ key: String) -> String? { (value[key] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) }
    public func int(_ key: String) -> Int? {
        if let i = value[key] as? Int { return i }
        if let d = value[key] as? Double { return Int(d) }
        if let s = value[key] as? String { return Int(s) }
        return nil
    }
    public func strings(_ key: String) -> [String]? { value[key] as? [String] }
    public static func error(_ message: String) -> JSONBox { JSONBox(["error": message]) }
}

/// A JSON value for an optional: the value, or JSON null. (`String? ?? NSNull()` does not type-check; this does.)
public func orNull(_ value: Any?) -> Any { value ?? NSNull() }

/// The tools an agent may call, with the descriptions and input schemas the old app's FastMCP server published. Names
/// and reply shapes match `services/mcp_server.py` and `mcp_tools_more.py`, so Jocasta, the `.mcpb` bridge and the
/// Claude Code configuration in `docs/MCP.md` need no change.
public enum MCPCatalog {
    public static let protocolVersion = "2025-06-18"
    public static let serverName = "LocalBook"
    public static let instructions = "LocalBook is the user's private, offline research library. Search and read their notebooks here rather than guessing, "
        + "and cite the sources you are given. Everything is local to this machine. Work one notebook at a time: call list_notebooks, pick the notebook "
        + "that fits, then search_notebook / list_sources / list_notes / get_source within it. ask_notebook writes a cited answer with LocalBook's own "
        + "model and is slower; it may answer busy when the user is using the model."

    public struct Tool: Sendable {
        public let name: String
        public let description: String
        /// Property name → (JSON type, description, required).
        public let properties: [(name: String, type: String, description: String, required: Bool)]
        public let readOnly: Bool
        /// The scope the key needs besides `mcp`, checked before the call reaches the app.
        public let scope: String?

        var schema: [String: Any] {
            var props: [String: Any] = [:]
            for p in properties { props[p.name] = ["type": p.type, "description": p.description] }
            var s: [String: Any] = ["type": "object", "properties": props]
            let required = properties.filter(\.required).map(\.name)
            if !required.isEmpty { s["required"] = required }
            return s
        }

        var annotations: [String: Any] {
            readOnly ? ["readOnlyHint": true] : ["readOnlyHint": false, "destructiveHint": false, "idempotentHint": false]
        }

        public var listing: [String: Any] { ["name": name, "description": description, "inputSchema": schema, "annotations": annotations] }
    }

    static func t(_ name: String, _ description: String, _ props: [(String, String, String, Bool)] = [], readOnly: Bool = true, scope: String? = nil) -> Tool {
        Tool(name: name, description: description, properties: props.map { ($0.0, $0.1, $0.2, $0.3) }, readOnly: readOnly, scope: scope)
    }

    public static let tools: [Tool] = [
        t("list_notebooks", "List the user's notebooks, with how many sources each holds."),
        t("list_sources", "List a notebook's sources, newest first (id, title, type, added). Use the ids with get_source.",
          [("notebook_id", "string", "The notebook", true), ("k", "integer", "How many (default 20, at most 100)", false)]),
        t("search_notebook", "Search ONE notebook for passages about `query`, best first. The same retrieval LocalBook's chat uses before it writes an answer "
          + "(keyword + meaning search, then the reranker), without the model, so it is fast and never busy. Each passage carries its `source_id`; read more of "
          + "that source with get_source. Get notebook ids from list_notebooks.",
          [("notebook_id", "string", "The notebook", true), ("query", "string", "What to look for", true), ("k", "integer", "How many passages (default 8)", false)]),
        t("search_notebooks", "Search several notebooks at once and return passages with attribution. Prefer search_notebook (one notebook, the same retrieval "
          + "the app uses). Searching more than one notebook needs a key with the `all_notebooks` scope; with one id in `notebook_ids` any MCP key may call it.",
          [("query", "string", "What to look for", true), ("notebook_ids", "array", "Notebook ids; omit for every notebook", false), ("k", "integer", "How many passages (default 8)", false)]),
        t("ask_notebook", "Ask a question and get a written, cited answer from the user's own material. With no notebook_id, the answer draws on every notebook "
          + "and each citation names the notebook it came from (needs the `all_notebooks` scope). Returns {\"status\": \"busy\", \"retry_after\": N} when the "
          + "model is in use: the person sitting at the machine comes first.",
          [("question", "string", "The question", true), ("notebook_id", "string", "The notebook; omit to ask every notebook", false), ("top_k", "integer", "Passages to read (default 4)", false)]),
        t("get_source", "Read one source's full text, a page at a time. Paginated on purpose: sources are whole documents.",
          [("source_id", "string", "The source", true), ("offset", "integer", "Character offset (default 0)", false), ("max_chars", "integer", "Characters per page (default 8000)", false)]),
        t("list_notes", "The notes saved in one notebook, most recently edited first (title + a preview). Read one in full with get_note.",
          [("notebook_id", "string", "The notebook", true), ("n", "integer", "How many (default 20)", false)]),
        t("get_note", "Read one of the user's notes, by id.",
          [("note_id", "string", "The note", true), ("max_chars", "integer", "Characters to return (default 20000)", false)]),
        t("list_recent_notes", "The user's most recently edited notes, newest first (titles + a preview).", [("n", "integer", "How many (default 10)", false)]),
        t("fetch_page", "Fetch a web page (or PDF/document) and return its readable text. Refuses anything that is not a public http/https address.",
          [("url", "string", "The address", true), ("max_chars", "integer", "Characters to return (default 8000)", false)]),
        t("web_search", "Search the web. Use this only when the user's own notebooks do not have it.",
          [("query", "string", "What to search for", true), ("n", "integer", "How many results (default 5)", false)]),
        t("youtube_search", "Search YouTube for videos.", [("query", "string", "What to search for", true), ("n", "integer", "How many (default 5)", false)]),
        t("scholarly_search", "Search academic papers. `source`: \"all\" (Semantic Scholar + arXiv), \"semanticscholar\", \"arxiv\", \"openalex\" or \"pubmed\".",
          [("query", "string", "What to search for", true), ("n", "integer", "How many (default 5)", false), ("source", "string", "Which index (default all)", false)]),
        t("propose_note", "Propose something for a notebook. It is QUEUED, never added directly: the user approves it in LocalBook (Settings › Agents). "
          + "Returns the outcome: `queued`, `skipped` (a duplicate).",
          [("notebook_id", "string", "The notebook", true), ("title", "string", "A title", true), ("body", "string", "The text", true), ("source", "string", "Where it came from", false)],
          readOnly: false),
        t("approval_queue", "What agents have proposed and the user has not decided on yet.",
          [("status", "string", "queued (default), stored or dismissed", false), ("k", "integer", "How many (default 20)", false)]),
        t("memory_search", "Search LocalBook's memory (core facts about the user, long-term memories) for what bears on `query`. Needs the `memory` scope.",
          [("query", "string", "What to look for", true), ("k", "integer", "How many (default 8)", false), ("char_budget", "integer", "Characters at most (default 2000)", false)],
          scope: "memory"),
        t("memory_add", "Remember something long-term, in LocalBook's shared memory, under your companion's tag. Needs the `memory` scope.",
          [("text", "string", "What to remember", true), ("category", "string", "A free label (preference, person, …)", false)], readOnly: false, scope: "memory"),
        // The Curator's tools keep the old app's names, so a client written against it finds them.
        t("events_since", "What has happened in LocalBook since you last asked: sources added, questions asked, Studio items and insights made, what the Collector found, briefs written. Pass back the cursor you were given. Needs the `events` scope.",
          [("cursor", "string", "The cursor from the last call (omit for everything)", false), ("k", "integer", "How many at most (default 100)", false)], scope: "events"),
        t("event_kinds", "The kinds of events events_since reports, with what each means.", scope: "events"),
        t("curator_insights", "The Curator's cross-notebook connections (where two notebooks say close to the same thing) and what it wrote lately (briefs, the week in review, patterns, devil's advocate).",
          [("since", "string", "ISO date; only items after it", false), ("k", "integer", "How many of each (default 10)", false)]),
        t("todays_digest", "The Curator's digest of a notebook (3-4 sentences on its state), or of every notebook when notebook_id is omitted. Digests are written in the Curator pane; this reads the latest.",
          [("notebook_id", "string", "A notebook", false)]),
        t("start_research", "Start a research job on a topic for a notebook: a web search (default), a site search, or a deep dive that reads the pages and keeps the best. Results wait in the notebook's Collector queue for the user to approve. Returns a job id for get_job.",
          [("topic", "string", "The topic", true), ("notebook_id", "string", "The notebook the results are for (default: the one on screen)", false), ("mode", "string", "web_search (default), site_search or deep_dive", false), ("site", "string", "For site_search: arxiv, wikipedia, hackerNews, github, reddit, stackOverflow, semanticScholar, openAlex or pubmed", false)], readOnly: false),
        t("get_job", "Status and results of a research job.", [("job_id", "string", "The job", true)]),
    ]

    public static func tool(named name: String) -> Tool? { tools.first { $0.name == name } }
    /// Tools the catalogue lists but this version does not answer (none since flight 5).
    public static let notYet: [String] = []
}

/// Streamable HTTP MCP, the server half: one JSON-RPC request in, one JSON-RPC reply out. Sessions are stateless (the
/// id is handed out and accepted, never required) so a client that lost one keeps working, as with the old server.
public enum MCPServer {
    public static func sessionId() -> String { UUID().uuidString.lowercased().replacingOccurrences(of: "-", with: "") }

    /// nil means a notification: nothing to send back (HTTP 202).
    public static func reply(to message: [String: Any], serverVersion: String,
                             call: (_ name: String, _ args: JSONBox) async -> JSONBox) async -> [String: Any]? {
        let id = message["id"]
        let method = message["method"] as? String ?? ""
        func result(_ r: [String: Any]) -> [String: Any] { ["jsonrpc": "2.0", "id": id ?? NSNull(), "result": r] }
        func failure(_ code: Int, _ text: String) -> [String: Any] { ["jsonrpc": "2.0", "id": id ?? NSNull(), "error": ["code": code, "message": text]] }
        let isNotification = id == nil || id is NSNull
        if method.hasPrefix("notifications/") { return nil }
        let params = message["params"] as? [String: Any] ?? [:]
        switch method {
        case "initialize":
            let asked = params["protocolVersion"] as? String ?? MCPCatalog.protocolVersion
            let known = ["2024-11-05", "2025-03-26", "2025-06-18"]
            return result(["protocolVersion": known.contains(asked) ? asked : MCPCatalog.protocolVersion,
                           "capabilities": ["tools": ["listChanged": false]],
                           "serverInfo": ["name": MCPCatalog.serverName, "version": serverVersion],
                           "instructions": MCPCatalog.instructions])
        case "ping":
            return result([:])
        case "tools/list":
            return result(["tools": MCPCatalog.tools.map(\.listing)])
        case "tools/call":
            guard let name = params["name"] as? String, MCPCatalog.tool(named: name) != nil else {
                return failure(-32602, "Unknown tool: \(params["name"] as? String ?? "")")
            }
            let args = JSONBox(params["arguments"] as? [String: Any] ?? [:])
            let out = await call(name, args).value
            let text = (try? JSONSerialization.data(withJSONObject: out, options: [.sortedKeys])).map { String(decoding: $0, as: UTF8.self) } ?? "{}"
            var r: [String: Any] = ["content": [["type": "text", "text": text]], "structuredContent": out]
            if out["error"] != nil { r["isError"] = true }
            return result(r)
        case "resources/list", "prompts/list":
            return result([method.hasPrefix("resources") ? "resources" : "prompts": [[String: Any]]()])
        case "":
            return isNotification ? nil : failure(-32600, "Invalid Request")
        default:
            return isNotification ? nil : failure(-32601, "Method not found: \(method)")
        }
    }
}
