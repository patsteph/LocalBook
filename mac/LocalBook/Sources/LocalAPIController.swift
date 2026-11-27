import Foundation
import LocalBookCore
import LocalBookEngines
import Observation

/// The old app's loopback API (`http://127.0.0.1:8000`: `/mcp` for agents, `/v1` for OpenAI-style companions,
/// `/browser/*` for the shipped browser extension), hosted by this app from the first second the library is open.
/// Mirrors the package's `LocalAPI` + `HTTPServer` for Settings › Agents: the listener's state, the keys, the call log.
@Observable @MainActor
final class LocalAPIController {
    enum Status: Equatable { case off, listening, failed(String) }

    private(set) var status: Status = .off
    private(set) var keys: [CompanionKeys.Record] = []
    private(set) var calls: [CompanionAudit.Call] = []
    private(set) var proposals: [Proposals.Proposal] = []
    private(set) var memories: [MemoryStore.Memory] = []
    /// A key just issued, shown once; nil after the sheet is dismissed.
    var freshKey: (companion: String, token: String)?
    var problem: String?

    let store = CompanionKeys()
    let audit = CompanionAudit()
    private(set) var bridge: LocalAPIBridge?
    private var api: LocalAPI?
    private var server: HTTPServer?
    private let library: LibraryModel

    init(library: LibraryModel) { self.library = library }

    var isListening: Bool { status == .listening }
    static var version: String { Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "3.0" }
    var address: String { "http://127.0.0.1:\(LocalAPI.port)" }

    /// Opens the port once the library is open. The old app on the same Mac holds 8000 while it runs: that is
    /// reported in Settings, not fatal; quit the old app and press Start.
    func start() async {
        guard server == nil else { return }
        let bridge = LocalAPIBridge(library: library)
        self.bridge = bridge
        let api = LocalAPI(keys: store, audit: audit, version: Self.version, delegate: bridge)
        self.api = api
        let server = HTTPServer(loopbackPort: LocalAPI.port) { req, host in await api.handle(req, remoteHost: host) }
        self.server = server
        do { try await server.start(); status = .listening; problem = nil }
        catch {
            status = .failed(error.localizedDescription + (error.localizedDescription.contains("in use") || error.localizedDescription.contains("48") ? " (the old LocalBook may be holding port 8000; quit it and press Start)" : ""))
            self.server = nil
        }
        refresh()
    }

    func stop() {
        server?.stop(); server = nil; status = .off
    }

    func refresh() {
        keys = store.list()
        calls = audit.recent(limit: 200)
        proposals = bridge?.proposals.all() ?? []
        Task { memories = (try? await bridge?.memory?.all()) ?? [] }
    }

    func issue(_ name: String, scopes: [String]) {
        do { let token = try store.issue(name, scopes: scopes); freshKey = (name.trimmingCharacters(in: .whitespaces), token); problem = nil }
        catch { problem = error.localizedDescription }
        refresh()
    }

    func revoke(_ companion: String) { store.revoke(companion); refresh() }
    func purgeCalls(_ companion: String? = nil) { audit.purge(companion: companion); refresh() }

    /// A proposal becomes a note of its notebook, searched and cited like one typed here.
    func approve(_ p: Proposals.Proposal) async {
        do {
            try await library.addNote(notebook: p.notebookId, title: p.title, text: p.body + "\n\n— " + p.source, kind: "note")
            bridge?.proposals.setStatus(p.id, .stored)
        } catch { problem = error.localizedDescription }
        refresh()
    }
    func dismiss(_ p: Proposals.Proposal) { bridge?.proposals.setStatus(p.id, .dismissed); refresh() }
    func removeProposal(_ p: Proposals.Proposal) { bridge?.proposals.remove(p.id); refresh() }

    func deleteMemory(_ m: MemoryStore.Memory) async { try? await bridge?.memory?.delete(m.id); refresh() }
    func deleteMemories(from source: String) async { _ = try? await bridge?.memory?.deleteAll(source: source); refresh() }
    func addMemory(_ text: String, category: String) async {
        do { _ = try await bridge?.memory?.add(.core, text: text, category: category, source: "you") } catch { problem = error.localizedDescription }
        refresh()
    }

    // MARK: ready-to-paste setup, as Settings › Companions showed it

    func claudeCodeCommand(_ token: String) -> String {
        "claude mcp add --transport http --scope user localbook \(address)/mcp --header \"Authorization: Bearer \(token)\""
    }
    func jsonConfig(_ token: String) -> String {
        """
        {
          "mcpServers": {
            "localbook": {
              "type": "http",
              "url": "\(address)/mcp",
              "headers": { "Authorization": "Bearer \(token)" }
            }
          }
        }
        """
    }
    static let claudeDesktopSteps = "Claude Desktop starts local servers as programs, so LocalBook ships a small extension that passes messages through: run ./mcp-bridge/build.sh in the repository, open mcp-bridge/dist/LocalBook.mcpb, click Install and paste the key when it asks."
}
