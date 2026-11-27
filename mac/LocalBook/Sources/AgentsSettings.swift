import AppKit
import LocalBookCore
import LocalBookEngines
import SwiftUI

/// Settings › Agents: the local API's state, agent keys (issued once, scoped, revocable) with ready-to-paste setup,
/// what agents have proposed, what they did, and LocalBook's memory. The Curator, Collector and Correspondent
/// themselves arrive with the agents build; `@curator` in the Ask field already answers across notebooks.
struct AgentsSettings: View {
    @Environment(AppModel.self) private var model
    @State private var newName = ""
    @State private var scopes: Set<String> = ["mcp", "llm"]
    @State private var showKey = false
    @State private var newMemory = ""
    @State private var memoryCategory = "preference"

    private var api: LocalAPIController { model.localAPI }

    var body: some View {
        Form {
            statusSection
            keysSection
            proposalsSection
            callsSection
            memorySection
            Section("Agents in the Ask field") {
                Text("Type **@curator** before a question (or pick Curator from the / menu) to search every notebook at once; each citation names its notebook. Everything else you ask stays inside the notebook on screen.")
                    .font(.callout).foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .onAppear { api.refresh() }
        .onReceive(NotificationCenter.default.publisher(for: .proposalsChanged)) { _ in api.refresh() }
        .onReceive(NotificationCenter.default.publisher(for: .memoryChanged)) { _ in api.refresh() }
        .sheet(isPresented: $showKey) { if let fresh = api.freshKey { FreshKeySheet(companion: fresh.companion, token: fresh.token, api: api) } }
        .onChange(of: api.freshKey?.token) { _, token in showKey = token != nil }
    }

    private var statusSection: some View {
        Section {
            HStack {
                Circle().fill(statusColor).frame(width: 9, height: 9)
                Text(statusLine)
                Spacer()
                if api.isListening { Button("Stop") { api.stop() } } else { Button("Start") { Task { await api.start() } } }
            }
            Text("Agents on this Mac (Claude Code, Claude Desktop, Jocasta, the browser extension) reach LocalBook at \(api.address). It answers only to programs on this Mac; web pages are refused. Nothing here needs the internet.")
                .font(.caption).foregroundStyle(.secondary)
        } header: { Text("Local API") }
    }

    private var statusColor: Color { switch api.status { case .listening: .green; case .off: .gray; case .failed: .orange } }
    private var statusLine: String {
        switch api.status {
        case .listening: "Listening on \(api.address)"
        case .off: "Not listening"
        case .failed(let why): "Off: \(why)"
        }
    }

    private var keysSection: some View {
        Section {
            ForEach(api.keys) { key in
                HStack(alignment: .firstTextBaseline) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(key.companionId).font(.body.weight(.medium))
                        Text(key.scopes.joined(separator: " · ") + (key.lastUsedAt.map { " · last used \(short($0))" } ?? " · never used"))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Reissue") { api.issue(key.companionId, scopes: key.scopes) }.buttonStyle(.borderless)
                    Button("Revoke", role: .destructive) { api.revoke(key.companionId) }.buttonStyle(.borderless)
                }
            }
            if api.keys.isEmpty { Text("No agent has a key yet.").foregroundStyle(.secondary) }
            VStack(alignment: .leading, spacing: 8) {
                TextField("Agent name (claude-code, claude-desktop, jocasta…)", text: $newName)
                HStack(spacing: 14) {
                    ForEach(CompanionKeys.scopes, id: \.self) { scope in
                        Toggle(scope, isOn: Binding(get: { scopes.contains(scope) }, set: { on in if on { scopes.insert(scope) } else { scopes.remove(scope) } }))
                            .toggleStyle(.checkbox).font(.callout)
                    }
                }
                Text("mcp lets the agent search and read; all_notebooks lets it search every notebook in one call (only @curator does that otherwise); llm is the OpenAI-style /v1; memory lets it remember and recall.")
                    .font(.caption).foregroundStyle(.secondary)
                HStack {
                    Button("Issue Key") { api.issue(newName, scopes: Array(scopes)); newName = "" }
                        .disabled(newName.trimmingCharacters(in: .whitespaces).isEmpty || scopes.isEmpty)
                    if let problem = api.problem { Text(problem).font(.caption).foregroundStyle(.orange) }
                }
            }
        } header: { Text("Agent keys") } footer: {
            Text("A key is shown once; LocalBook keeps only a hash. Keys issued by the old app work here when its companion_keys.json is copied into this app's folder.")
        }
    }

    private var proposalsSection: some View {
        Section("Proposed by agents") {
            let queued = api.proposals.filter { $0.status == .queued }
            if queued.isEmpty { Text("Nothing waiting. An agent's propose_note lands here; nothing enters a notebook until you approve it.").foregroundStyle(.secondary) }
            ForEach(queued) { p in
                VStack(alignment: .leading, spacing: 4) {
                    HStack(alignment: .firstTextBaseline) {
                        Text(p.title).font(.body.weight(.medium))
                        Spacer()
                        Text(model.library.notebookTitle(p.notebookId)).font(.caption).foregroundStyle(.secondary)
                    }
                    Text(p.body).font(.callout).lineLimit(4)
                    HStack {
                        Text("\(p.companionId) · \(p.source)").font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        Button("Add as Note") { Task { await api.approve(p) } }.buttonStyle(.borderless)
                        Button("Dismiss", role: .destructive) { api.dismiss(p) }.buttonStyle(.borderless)
                    }
                }
            }
        }
    }

    private var callsSection: some View {
        Section {
            if api.calls.isEmpty { Text("No calls yet.").foregroundStyle(.secondary) }
            ForEach(api.calls.prefix(40)) { c in
                HStack {
                    Text(c.companionId).font(.callout.weight(.medium)).frame(width: 120, alignment: .leading).lineLimit(1)
                    Text(c.tool).font(.callout.monospaced())
                    Text(c.argsPreview).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    Spacer()
                    Text("\(c.milliseconds) ms").font(.caption).foregroundStyle(.secondary)
                    Text(c.outcome.rawValue).font(.caption.weight(.semibold)).foregroundStyle(c.outcome == .ok ? .green : .orange)
                }
                .help(c.detail ?? short(c.timestamp))
            }
            if !api.calls.isEmpty { Button("Clear the log", role: .destructive) { api.purgeCalls() } }
        } header: { Text("What they have done") } footer: {
            Text("Arguments are hashed, so your questions are never written down.")
        }
    }

    private var memorySection: some View {
        Section {
            ForEach(api.memories) { m in
                HStack(alignment: .firstTextBaseline) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(m.text).font(.callout).lineLimit(3)
                        Text([m.kind.rawValue, m.category, "by \(m.source)", short(m.updated)].filter { !$0.isEmpty }.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button(role: .destructive) { Task { await api.deleteMemory(m) } } label: { Image(systemName: "trash") }.buttonStyle(.borderless)
                }
            }
            if api.memories.isEmpty { Text("Nothing remembered yet.").foregroundStyle(.secondary) }
            HStack {
                TextField("Something LocalBook should remember about you", text: $newMemory)
                TextField("category", text: $memoryCategory).frame(width: 110)
                Button("Remember") { Task { await api.addMemory(newMemory, category: memoryCategory); newMemory = "" } }
                    .disabled(newMemory.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            let agents = Set(api.memories.map(\.source)).subtracting(["you", "localbook"]).sorted()
            ForEach(agents, id: \.self) { a in
                Button("Forget everything \(a) wrote", role: .destructive) { Task { await api.deleteMemories(from: a) } }
            }
        } header: { Text("Memory") } footer: {
            Text("Core facts and long-term memories, each tagged with who wrote them. Agents with the memory scope read and add here; the chat starts reading memory with the agents build.")
        }
    }

    private func short(_ iso: String) -> String {
        guard let d = (try? Date(iso, strategy: .iso8601)) ?? (try? Date(iso, strategy: Date.ISO8601FormatStyle(includingFractionalSeconds: true))) else { return iso }
        return d.formatted(date: .abbreviated, time: .shortened)
    }
}

/// The key, shown once, with the setup lines for the clients `docs/MCP.md` names.
struct FreshKeySheet: View {
    let companion: String
    let token: String
    let api: LocalAPIController
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Key for \(companion)").font(.title2.weight(.semibold))
            Text("Copy it now; it is shown once. LocalBook keeps only a hash.").foregroundStyle(.secondary)
            copyRow("The key", token)
            Divider()
            Text("Claude Code").font(.headline)
            copyRow("Terminal", api.claudeCodeCommand(token))
            Text("Claude Desktop").font(.headline)
            Text(LocalAPIController.claudeDesktopSteps).font(.callout).foregroundStyle(.secondary)
            Text("Anything else that speaks MCP over HTTP").font(.headline)
            copyRow("JSON", api.jsonConfig(token))
            HStack { Spacer(); Button("Done") { api.freshKey = nil; dismiss() }.keyboardShortcut(.defaultAction) }
        }
        .padding(24)
        .frame(width: 620)
    }

    private func copyRow(_ label: String, _ text: String) -> some View {
        HStack(alignment: .top) {
            Text(text).font(.callout.monospaced()).textSelection(.enabled).lineLimit(8).frame(maxWidth: .infinity, alignment: .leading)
                .padding(8).background(.quaternary, in: RoundedRectangle(cornerRadius: 6))
            Button("Copy") { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(text, forType: .string) }
        }
        .accessibilityLabel(label)
    }
}
