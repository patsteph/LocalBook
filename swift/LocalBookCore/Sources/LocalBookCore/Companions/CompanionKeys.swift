import CryptoKit
import Foundation

/// Who holds a companion key and what it may do. The old app's `CompanionIdentity`.
public struct CompanionIdentity: Sendable, Equatable {
    public let companionId: String
    public let scopes: Set<String>
    public init(companionId: String, scopes: Set<String>) { self.companionId = companionId; self.scopes = scopes }
    public func has(_ scope: String) -> Bool { scopes.contains(scope) }
}

/// Per-companion API keys: one key each, hashed at rest, scoped, revocable. The file is the old app's
/// `companion_keys.json` byte for byte in shape (`{"version": 2, "companions": {id: {salt, key_hash, scopes, …}}}`,
/// `key_hash = sha256(salt + token)`, tokens `lb-` + 43 url-safe characters), so a key issued by either app verifies
/// in the other once the file is copied over. Verification compares against every record with no early exit.
public final class CompanionKeys: @unchecked Sendable {
    public static let scopes = ["llm", "mcp", "audio", "memory", "events", "all_notebooks"]
    public static let prefix = "lb-"
    public static let storeVersion = 2
    public static let fileName = "companion_keys.json"

    public struct Record: Sendable, Identifiable, Equatable {
        public let companionId: String
        public let scopes: [String]
        public let createdAt: String
        public let lastUsedAt: String?
        public let firstIssuedAt: String?
        public var id: String { companionId }
    }

    public enum KeyError: LocalizedError {
        case emptyName, unknownScopes([String]), unreadable(String)
        public var errorDescription: String? {
            switch self {
            case .emptyName: "The agent needs a name."
            case .unknownScopes(let s): "Unknown scope(s) \(s.joined(separator: ", ")); expected some of \(CompanionKeys.scopes.joined(separator: ", "))."
            case .unreadable(let why): "The agent key store could not be read: \(why)"
            }
        }
    }

    private let lock = NSLock()
    private let fileURL: URL?
    public init(url: URL? = nil) { fileURL = url }
    private var url: URL { fileURL ?? AppFolders.base.appending(path: Self.fileName) }

    // MARK: the file

    private func load() throws -> [String: Any] {
        guard FileManager.default.fileExists(atPath: url.path) else { return ["version": Self.storeVersion, "companions": [String: Any]()] }
        let data = try Data(contentsOf: url)
        guard var root = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              var companions = root["companions"] as? [String: Any] else { throw KeyError.unreadable("no companions map") }
        if (root["version"] as? Int ?? 1) < Self.storeVersion {
            // v1 → v2: a key that held `mcp` before `all_notebooks` existed could already search across notebooks.
            for (id, any) in companions {
                guard var rec = any as? [String: Any] else { continue }
                var scopes = rec["scopes"] as? [String] ?? []
                if scopes.contains("mcp"), !scopes.contains("all_notebooks") { scopes.append("all_notebooks"); rec["scopes"] = scopes; companions[id] = rec }
            }
            root["companions"] = companions
            root["version"] = Self.storeVersion
            try? save(root)
        }
        return root
    }

    private func save(_ root: [String: Any]) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let data = try JSONSerialization.data(withJSONObject: root, options: [.prettyPrinted, .sortedKeys])
        let tmp = url.appendingPathExtension("tmp")
        try data.write(to: tmp, options: .atomic)
        _ = try? FileManager.default.replaceItemAt(url, withItemAt: tmp)
        if FileManager.default.fileExists(atPath: tmp.path) { try? FileManager.default.removeItem(at: tmp) }
        try? FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }

    static func hash(salt: String, token: String) -> String {
        SHA256.hash(data: Data((salt + token).utf8)).map { String(format: "%02x", $0) }.joined()
    }

    static func now() -> String { ISO8601DateFormatter().string(from: Date()) }

    /// 32 random bytes as url-safe base64 without padding, what Python's `secrets.token_urlsafe(32)` gives.
    static func randomToken() -> String {
        var bytes = [UInt8](repeating: 0, count: 32)
        for i in bytes.indices { bytes[i] = UInt8.random(in: 0...255) }
        return Data(bytes).base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    }

    // MARK: issuing, verifying, revoking

    /// Mints a key for `companionId` and returns it ONCE in plain text. Reissuing rotates: the old key stops working.
    @discardableResult
    public func issue(_ companionId: String, scopes: [String]) throws -> String {
        let id = companionId.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !id.isEmpty else { throw KeyError.emptyName }
        let wanted = scopes.isEmpty ? ["llm"] : scopes
        let unknown = wanted.filter { !Self.scopes.contains($0) }
        guard unknown.isEmpty else { throw KeyError.unknownScopes(unknown) }
        let token = Self.prefix + Self.randomToken()
        var saltBytes = [UInt8](repeating: 0, count: 16)
        for i in saltBytes.indices { saltBytes[i] = UInt8.random(in: 0...255) }
        let salt = saltBytes.map { String(format: "%02x", $0) }.joined()
        lock.lock(); defer { lock.unlock() }
        var root = try load()
        var companions = root["companions"] as? [String: Any] ?? [:]
        let existing = companions[id] as? [String: Any] ?? [:]
        companions[id] = [
            "salt": salt,
            "key_hash": Self.hash(salt: salt, token: token),
            "scopes": wanted,
            "created_at": Self.now(),
            "last_used_at": NSNull(),
            "first_issued_at": existing["first_issued_at"] ?? Self.now(),
        ] as [String: Any]
        root["companions"] = companions
        try save(root)
        return token
    }

    /// Who holds this key, or nil. Every record is compared; a miss takes as long as a hit.
    public func verify(_ token: String) -> CompanionIdentity? {
        guard !token.isEmpty else { return nil }
        lock.lock(); defer { lock.unlock() }
        guard var root = try? load(), var companions = root["companions"] as? [String: Any] else { return nil }
        var matched: String?
        for (id, any) in companions.sorted(by: { $0.key < $1.key }) {
            guard let rec = any as? [String: Any], let stored = rec["key_hash"] as? String, !stored.isEmpty else { continue }
            let computed = Self.hash(salt: rec["salt"] as? String ?? "", token: token)
            if Self.constantTimeEqual(stored, computed), matched == nil { matched = id }
        }
        guard let matched, var rec = companions[matched] as? [String: Any] else { return nil }
        rec["last_used_at"] = Self.now()
        companions[matched] = rec
        root["companions"] = companions
        try? save(root)
        return CompanionIdentity(companionId: matched, scopes: Set(rec["scopes"] as? [String] ?? []))
    }

    static func constantTimeEqual(_ a: String, _ b: String) -> Bool {
        let x = Array(a.utf8), y = Array(b.utf8)
        guard x.count == y.count else { return false }
        var diff: UInt8 = 0
        for i in x.indices { diff |= x[i] ^ y[i] }
        return diff == 0
    }

    /// The `lb-…` token of an `Authorization: Bearer …` header, or nil.
    public static func bearer(_ authorization: String?) -> String? {
        guard let authorization, authorization.lowercased().hasPrefix("bearer ") else { return nil }
        let token = authorization.dropFirst(7).trimmingCharacters(in: .whitespaces)
        return token.isEmpty ? nil : token
    }

    @discardableResult
    public func revoke(_ companionId: String) -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard var root = try? load(), var companions = root["companions"] as? [String: Any], companions[companionId] != nil else { return false }
        companions[companionId] = nil
        root["companions"] = companions
        try? save(root)
        return true
    }

    public func list() -> [Record] {
        lock.lock(); defer { lock.unlock() }
        guard let root = try? load(), let companions = root["companions"] as? [String: Any] else { return [] }
        return companions.compactMap { id, any -> Record? in
            guard let rec = any as? [String: Any] else { return nil }
            return Record(companionId: id, scopes: rec["scopes"] as? [String] ?? [], createdAt: rec["created_at"] as? String ?? "",
                          lastUsedAt: rec["last_used_at"] as? String, firstIssuedAt: rec["first_issued_at"] as? String)
        }.sorted { $0.companionId < $1.companionId }
    }

    public func has(_ companionId: String) -> Bool { list().contains { $0.companionId == companionId } }
}

/// What companions did and when: one row per call, arguments hashed (never the question itself), kept as a small
/// JSON list beside the keys (the old app's `companion_calls` table). Settings shows it and can purge it.
public final class CompanionAudit: @unchecked Sendable {
    public static let fileName = "companion_calls.json"
    public static let keep = 2_000
    public enum Outcome: String, Sendable { case ok, error, denied, busy }

    public struct Call: Sendable, Identifiable, Equatable {
        public let id: String
        public let companionId: String
        public let tool: String
        public let argsHash: String
        public let argsPreview: String
        public let timestamp: String
        public let milliseconds: Int
        public let outcome: Outcome
        public let detail: String?
    }

    private let lock = NSLock()
    private let fileURL: URL?
    private var cache: [Call]?
    public init(url: URL? = nil) { fileURL = url }
    private var url: URL { fileURL ?? AppFolders.base.appending(path: Self.fileName) }

    private func load() -> [Call] {
        if let cache { return cache }
        guard let data = try? Data(contentsOf: url), let rows = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]] else { cache = []; return [] }
        let calls = rows.compactMap { r -> Call? in
            guard let id = r["id"] as? String, let c = r["companion_id"] as? String, let t = r["tool"] as? String else { return nil }
            return Call(id: id, companionId: c, tool: t, argsHash: r["args_hash"] as? String ?? "", argsPreview: r["args_preview"] as? String ?? "",
                        timestamp: r["ts"] as? String ?? "", milliseconds: r["ms"] as? Int ?? 0,
                        outcome: Outcome(rawValue: r["outcome"] as? String ?? "ok") ?? .ok, detail: r["detail"] as? String)
        }
        cache = calls
        return calls
    }

    private func save(_ calls: [Call]) {
        cache = calls
        let rows: [[String: Any]] = calls.map {
            ["id": $0.id, "companion_id": $0.companionId, "tool": $0.tool, "args_hash": $0.argsHash, "args_preview": $0.argsPreview,
             "ts": $0.timestamp, "ms": $0.milliseconds, "outcome": $0.outcome.rawValue, "detail": orNull($0.detail)]
        }
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        if let data = try? JSONSerialization.data(withJSONObject: rows) { try? data.write(to: url, options: .atomic) }
    }

    /// The argument names and a hash of the values: enough to see what was asked for, never the words.
    public static func summarize(_ args: [String: Any]) -> (hash: String, preview: String) {
        let keys = args.keys.sorted()
        let canonical = (try? JSONSerialization.data(withJSONObject: args, options: [.sortedKeys])) ?? Data()
        let hash = SHA256.hash(data: canonical).prefix(8).map { String(format: "%02x", $0) }.joined()
        return (hash, keys.joined(separator: ", "))
    }

    public func record(companion: String, tool: String, args: [String: Any], milliseconds: Int, outcome: Outcome, detail: String? = nil) {
        let (hash, preview) = Self.summarize(args)
        let call = Call(id: UUID().uuidString.lowercased(), companionId: companion, tool: tool, argsHash: hash, argsPreview: preview,
                        timestamp: ISO8601DateFormatter().string(from: Date()), milliseconds: milliseconds, outcome: outcome, detail: detail)
        lock.lock(); defer { lock.unlock() }
        var calls = load()
        calls.insert(call, at: 0)
        if calls.count > Self.keep { calls.removeLast(calls.count - Self.keep) }
        save(calls)
    }

    /// Newest first; `companion` nil means every companion.
    public func recent(companion: String? = nil, limit: Int = 100) -> [Call] {
        lock.lock(); defer { lock.unlock() }
        let all = load()
        return Array((companion.map { c in all.filter { $0.companionId == c } } ?? all).prefix(limit))
    }

    @discardableResult
    public func purge(companion: String? = nil) -> Int {
        lock.lock(); defer { lock.unlock() }
        let all = load()
        let kept = companion.map { c in all.filter { $0.companionId != c } } ?? []
        save(kept)
        return all.count - kept.count
    }
}

/// What agents propose for a notebook: queued, never added on their own. The person approves one in Settings ›
/// Agents (it becomes a note) or dismisses it. The old app's Collector approval queue, reduced to what an agent can
/// put there. A small JSON file beside the keys.
public final class Proposals: @unchecked Sendable {
    public static let fileName = "proposals.json"
    public enum Status: String, Sendable, Codable { case queued, stored, dismissed }

    public struct Proposal: Sendable, Identifiable, Codable, Equatable {
        public let id: String
        public let notebookId: String
        public let title: String
        public let body: String
        public let source: String
        public let companionId: String
        public let created: String
        public var status: Status
        public init(id: String = UUID().uuidString.lowercased(), notebookId: String, title: String, body: String, source: String, companionId: String,
                    created: String = ISO8601DateFormatter().string(from: Date()), status: Status = .queued) {
            self.id = id; self.notebookId = notebookId; self.title = title; self.body = body; self.source = source; self.companionId = companionId
            self.created = created; self.status = status
        }
    }

    private let lock = NSLock()
    private let fileURL: URL?
    public init(url: URL? = nil) { fileURL = url }
    private var url: URL { fileURL ?? AppFolders.base.appending(path: Self.fileName) }

    private func load() -> [Proposal] {
        guard let data = try? Data(contentsOf: url) else { return [] }
        return (try? JSONDecoder().decode([Proposal].self, from: data)) ?? []
    }
    private func save(_ list: [Proposal]) {
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        if let data = try? JSONEncoder().encode(list) { try? data.write(to: url, options: .atomic) }
    }

    /// Queues a proposal; the same title and body for the same notebook twice is a duplicate (`skipped`).
    public func add(_ p: Proposal) -> (outcome: String, id: String) {
        lock.lock(); defer { lock.unlock() }
        var list = load()
        if let same = list.first(where: { $0.notebookId == p.notebookId && $0.title == p.title && $0.body == p.body && $0.status != .dismissed }) {
            return ("skipped", same.id)
        }
        list.insert(p, at: 0)
        if list.count > 500 { list.removeLast(list.count - 500) }
        save(list)
        return ("queued", p.id)
    }

    public func all(status: Status? = nil) -> [Proposal] {
        lock.lock(); defer { lock.unlock() }
        let list = load()
        return status.map { s in list.filter { $0.status == s } } ?? list
    }

    public func setStatus(_ id: String, _ status: Status) {
        lock.lock(); defer { lock.unlock() }
        var list = load()
        guard let i = list.firstIndex(where: { $0.id == id }) else { return }
        list[i].status = status
        save(list)
    }

    public func remove(_ id: String) {
        lock.lock(); defer { lock.unlock() }
        save(load().filter { $0.id != id })
    }
}
