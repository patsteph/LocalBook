import LocalBookCore
import LocalBookEngines
import SwiftUI

/// The Collector for the notebook on screen: what it is for, where it looks (feeds, pages, keywords), how it decides
/// (approval mode, schedule), what it found and is waiting on, and research jobs that feed the same queue.
struct CollectorPane: View {
    @Environment(AppModel.self) private var model
    @State private var notebookId: String?
    @State private var tab: Tab = .queue
    @State private var newAddress = ""
    @State private var newKeyword = ""
    @State private var newFocus = ""
    @State private var addProblem: String?
    @State private var researchTopic = ""
    @State private var researchMode: ResearchJob.Mode = .web
    @State private var researchSite: SiteSearch.Site = .wikipedia

    enum Tab: String, CaseIterable { case queue = "Queue", sources = "Sources", setup = "Setup", research = "Research", history = "History" }

    private var collector: CollectorEngine { Agents.collector }
    private var research: ResearchEngine { Agents.research }
    private var library: LibraryModel { model.library }
    private var notebook: LibraryNotebookInfo? { library.notebooks.first { $0.id == notebookId } }
    private var state: CollectorState { notebookId.map { collector.state($0) } ?? CollectorState() }
    private var busy: Bool { notebookId.map { collector.running.contains($0) } ?? false }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            Group {
                switch tab {
                case .queue: queue
                case .sources: sources
                case .setup: setup
                case .research: researchView
                case .history: history
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .onAppear { if notebookId == nil || notebook == nil { notebookId = library.scopeToNotebook ?? library.notebooks.first?.id } }
        .onChange(of: library.scopeToNotebook) { _, id in if let id { notebookId = id } }
        .onChange(of: model.collectorRequest) { _, r in
            guard let r else { return }
            model.collectorRequest = nil
            switch r {
            case .collect: Task { if let notebook { await collector.collect(notebook: notebook, library: library) } }
            case .research(let topic): tab = .research; researchTopic = topic; if !topic.isEmpty { startResearch() }
            case .show: break
            }
        }
    }

    private var header: some View {
        HStack(spacing: 12) {
            Image(systemName: "binoculars").foregroundStyle(.teal)
            VStack(alignment: .leading, spacing: 0) {
                Text("\(state.config.name) · \(notebook?.title ?? "Collector")").font(.headline)
                Text(state.config.isConfigured ? "\(state.config.rssFeeds.count) feeds · \(state.config.webPages.count) pages · \(state.config.newsKeywords.count) keywords · \(state.config.frequency.rawValue)" + (state.lastRun.map { " · last run \($0.formatted(date: .abbreviated, time: .shortened))" } ?? "")
                                              : "Nothing to watch yet. Add a feed, a page or a keyword under Sources.").font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer()
            if busy, let id = notebookId { ProgressView().controlSize(.small); Text(collector.status[id] ?? "Working…").font(.caption).foregroundStyle(.secondary).lineLimit(1) }
            else if let e = collector.error { Text(e).font(.caption).foregroundStyle(.red).lineLimit(1) }
            Picker("", selection: $tab) { ForEach(Tab.allCases, id: \.self) { t in Text(t == .queue && !state.pending.isEmpty ? "Queue · \(state.pending.count)" : t.rawValue).tag(t) } }.pickerStyle(.segmented).frame(width: 440)
            Button { Task { if let notebook { await collector.collect(notebook: notebook, library: library) } } } label: { Label("Collect now", systemImage: "arrow.down.circle") }
                .disabled(busy || notebook == nil || !state.config.isConfigured)
        }
        .controlSize(.small)
        .padding(.horizontal, 12).padding(.vertical, 8)
    }

    // MARK: queue

    private var queue: some View {
        let items = state.pending
        return ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                if items.isEmpty {
                    Text("Nothing waiting. Items the Collector is unsure about land here; in Trust me mode they go straight in.").foregroundStyle(.secondary).padding(.top, 20)
                } else {
                    HStack { Text("\(items.count) waiting for you").font(.headline); Spacer()
                        Button("Approve all") { Task { if let notebook { await collector.approveAll(notebook: notebook, library: library) } } }.disabled(busy) }
                    ForEach(items) { item in queueRow(item) }
                }
                let decided = state.items.filter { $0.status != "pending" }.suffix(8).reversed()
                if !decided.isEmpty {
                    Text("Decided lately").font(.subheadline.weight(.semibold)).padding(.top, 8)
                    ForEach(Array(decided)) { d in
                        HStack { Image(systemName: d.status == "approved" ? "checkmark.circle" : (d.status == "expired" ? "clock" : "xmark.circle")).foregroundStyle(d.status == "approved" ? .green : .secondary)
                            Text(d.title).lineLimit(1); Spacer(); Text(d.rejectionReason ?? d.status).font(.caption).foregroundStyle(.secondary) }
                    }
                }
            }
            .padding(16)
        }
    }

    private func queueRow(_ item: CollectedItem) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.title).font(.headline).lineLimit(2)
                    Text("\(item.sourceName) · \(item.sourceType)" + (item.published.map { " · \($0.formatted(date: .abbreviated, time: .omitted))" } ?? "")).font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                VStack(alignment: .trailing) {
                    Text("\(Int(item.overallConfidence * 100)) %").font(.title3.monospacedDigit().weight(.semibold)).foregroundStyle(item.overallConfidence >= 0.7 ? .green : (item.overallConfidence >= 0.45 ? .orange : .secondary))
                    if let e = item.expiresAt { Text("expires \(e.formatted(date: .abbreviated, time: .omitted))").font(.caption2).foregroundStyle(.tertiary) }
                }
            }
            if !item.preview.isEmpty { Text(item.preview).font(.callout).lineLimit(3) }
            Text(item.confidenceReasons.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary).lineLimit(2)
            HStack {
                Button("Approve") { Task { if let notebook { await collector.approve(item.id, notebook: notebook, library: library) } } }.disabled(busy)
                Menu("Dismiss") {
                    ForEach(["Not relevant", "Already have it", "Low quality", "Wrong topic", "Just not now"], id: \.self) { why in Button(why) { if let notebookId { collector.reject(item.id, notebook: notebookId, reason: why) } } }
                }.frame(width: 90)
                if let u = item.url.flatMap(URL.init(string:)) { Link("Open", destination: u) }
                Spacer()
            }
            .controlSize(.small)
        }
        .padding(12).background(RoundedRectangle(cornerRadius: 10).fill(Color(nsColor: .controlBackgroundColor)))
    }

    // MARK: sources

    private var sources: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    TextField("A feed or page address (a page that advertises a feed becomes the feed)", text: $newAddress).textFieldStyle(.roundedBorder).onSubmit { add() }
                    Button("Add") { add() }.disabled(newAddress.trimmingCharacters(in: .whitespaces).isEmpty || notebookId == nil)
                }
                if let addProblem { Text(addProblem).font(.caption).foregroundStyle(.red) }
                if !state.config.rssFeeds.isEmpty {
                    Text("Feeds").font(.subheadline.weight(.semibold))
                    ForEach(state.config.rssFeeds, id: \.self) { f in sourceRow(f, kind: "rss") }
                }
                if !state.config.webPages.isEmpty {
                    Text("Watched pages").font(.subheadline.weight(.semibold))
                    ForEach(state.config.webPages, id: \.self) { p in sourceRow(p, kind: "page") }
                }
                Text("Keywords (searched on Hacker News, Reddit and Wikipedia each run)").font(.subheadline.weight(.semibold))
                HStack {
                    TextField("A keyword or phrase", text: $newKeyword).textFieldStyle(.roundedBorder).onSubmit { addKeyword() }
                    Button("Add") { addKeyword() }.disabled(newKeyword.trimmingCharacters(in: .whitespaces).isEmpty)
                }
                RemovableChips(items: state.config.newsKeywords) { k in if let notebookId { collector.update(notebookId) { $0.config.newsKeywords.removeAll { $0 == k } } } }
            }
            .padding(16)
        }
    }

    private func sourceRow(_ address: String, kind: String) -> some View {
        let h = state.health(of: address)
        return HStack {
            Image(systemName: kind == "rss" ? "dot.radiowaves.left.and.right" : "doc.text.magnifyingglass").foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 1) {
                Text(address).lineLimit(1).font(.callout)
                Text(healthLine(h)).font(.caption).foregroundStyle(h.health == .healthy ? .secondary : (h.health == .degraded ? .orange : .red))
            }
            Spacer()
            Button { if let notebookId { collector.removeSource(address, notebook: notebookId) } } label: { Image(systemName: "minus.circle") }.buttonStyle(.borderless)
        }
    }
    private func healthLine(_ h: SourceHealthRecord) -> String {
        if h.lastSuccess == nil, h.lastFailure == nil { return "Not read yet" }
        var s = "\(h.health.rawValue) · \(h.itemsCollected) item\(h.itemsCollected == 1 ? "" : "s") so far"
        if let e = h.lastError, h.health != .healthy { s += " · \(e)" }
        return s
    }

    private func add() {
        guard let notebookId else { return }
        let a = newAddress; newAddress = ""; addProblem = nil
        Task { addProblem = await collector.addSource(a, notebook: notebookId) }
    }
    private func addKeyword() {
        guard let notebookId else { return }
        let k = newKeyword.trimmingCharacters(in: .whitespaces); newKeyword = ""
        guard !k.isEmpty else { return }
        collector.update(notebookId) { if !$0.config.newsKeywords.contains(k) { $0.config.newsKeywords.append(k) } }
    }

    // MARK: setup

    private var setup: some View {
        Form {
            Section("What this notebook is for") {
                TextField("Name", text: binding(\.name))
                TextField("Subject", text: binding(\.subject), prompt: Text("The company, person or topic at the centre"))
                TextField("Intent", text: binding(\.intent), prompt: Text("What you want to learn or keep track of"), axis: .vertical).lineLimit(2...4)
                HStack {
                    TextField("A focus area", text: $newFocus).onSubmit { addFocus() }
                    Button("Add") { addFocus() }.disabled(newFocus.trimmingCharacters(in: .whitespaces).isEmpty)
                }
                RemovableChips(items: state.config.focusAreas) { f in if let notebookId { collector.update(notebookId) { $0.config.focusAreas.removeAll { $0 == f } } } }
                TextField("Excluded topics (comma separated)", text: Binding(get: { state.config.excludedTopics.joined(separator: ", ") },
                                                                             set: { v in if let notebookId { collector.update(notebookId) { $0.config.excludedTopics = v.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty } } } }))
            }
            Section("How it decides") {
                Picker("Approval", selection: binding(\.approvalMode)) { ForEach(CollectorConfig.ApprovalMode.allCases, id: \.self) { m in Text(m.label).tag(m) } }
                Picker("Collect", selection: binding(\.frequency)) { ForEach(CollectorConfig.Frequency.allCases, id: \.self) { f in Text(f.rawValue.capitalized).tag(f) } }
                Stepper("At most \(state.config.maxItemsPerRun) items per run", value: binding(\.maxItemsPerRun), in: 1...50)
                Stepper("Ignore items older than \(state.config.maxAgeDays) days", value: binding(\.maxAgeDays), in: 1...365)
                Slider(value: binding(\.minRelevance), in: 0...1, step: 0.05) { Text("Relevance floor \(Int(state.config.minRelevance * 100)) %") }
                Text("Relevance is how close an item's meaning is to the subject, intent and focus areas above, measured with the search model. Nothing leaves this Mac to decide it.").font(.caption).foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
    }

    private func binding<T>(_ key: WritableKeyPath<CollectorConfig, T>) -> Binding<T> {
        Binding(get: { state.config[keyPath: key] }, set: { v in if let notebookId { collector.update(notebookId) { $0.config[keyPath: key] = v } } })
    }
    private func addFocus() {
        guard let notebookId else { return }
        let f = newFocus.trimmingCharacters(in: .whitespaces); newFocus = ""
        guard !f.isEmpty else { return }
        collector.update(notebookId) { if !$0.config.focusAreas.contains(f) { $0.config.focusAreas.append(f) } }
    }

    // MARK: research

    private var researchView: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Text("Research").font(.headline)
                Text("A web search looks across the sites LocalBook can search (the answer model picks which for the topic). A deep dive reads the top pages, scores them for length, links, recency and fit, and summarizes the keepers. Results wait in the Queue.").font(.caption).foregroundStyle(.secondary)
                HStack {
                    TextField("What to research", text: $researchTopic).textFieldStyle(.roundedBorder).onSubmit { startResearch() }
                    Picker("", selection: $researchMode) { ForEach(ResearchJob.Mode.allCases) { m in Text(m.label).tag(m) } }.frame(width: 130)
                    if researchMode == .site { Picker("", selection: $researchSite) { ForEach(SiteSearch.Site.allCases) { s in Text(s.label).tag(s) } }.frame(width: 150) }
                    Button("Start") { startResearch() }.disabled(researchTopic.trimmingCharacters(in: .whitespaces).isEmpty || notebookId == nil)
                }
                ForEach(notebookId.map { research.jobs(for: $0) } ?? []) { job in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Image(systemName: job.status == .running ? "hourglass" : (job.status == .done ? "checkmark.circle" : (job.status == .failed ? "exclamationmark.triangle" : "clock")))
                                .foregroundStyle(job.status == .done ? .green : .secondary)
                            Text(job.topic).font(.callout.weight(.medium)).lineLimit(1)
                            Text(job.mode.label).font(.caption).foregroundStyle(.secondary)
                            Spacer()
                            Text(job.created.formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundStyle(.secondary)
                        }
                        Text(job.progress).font(.caption).foregroundStyle(.secondary)
                        ForEach(job.results.prefix(6)) { r in
                            HStack(alignment: .top) {
                                if let u = URL(string: r.url) { Link(r.title.isEmpty ? r.url : r.title, destination: u).font(.caption).lineLimit(1) }
                                Spacer()
                                if r.quality > 0 { Text("\(Int(r.quality * 100)) %").font(.caption2).foregroundStyle(.secondary) }
                            }
                        }
                    }
                    .padding(10).background(RoundedRectangle(cornerRadius: 8).fill(Color(nsColor: .controlBackgroundColor)))
                }
            }
            .padding(16)
        }
    }

    private func startResearch() {
        guard let notebookId else { return }
        let t = researchTopic.trimmingCharacters(in: .whitespaces); guard !t.isEmpty else { return }
        researchTopic = ""
        research.start(topic: t, notebook: notebookId, mode: researchMode, site: researchMode == .site ? researchSite : nil, library: library)
    }

    // MARK: history

    private var history: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 8) {
                if state.runs.isEmpty { Text("No runs yet.").foregroundStyle(.secondary) }
                ForEach(state.runs.reversed()) { r in
                    VStack(alignment: .leading, spacing: 2) {
                        HStack { Text(r.startedAt.formatted(date: .abbreviated, time: .shortened)).font(.callout.weight(.medium)); Text(r.trigger).font(.caption).foregroundStyle(.secondary); Spacer()
                            Text("\(r.examined) looked at · \(r.stored) added · \(r.queued) queued · \(r.skipped) skipped").font(.caption).foregroundStyle(.secondary) }
                        ForEach(r.problems.prefix(3), id: \.self) { p in Text(p).font(.caption).foregroundStyle(.orange).lineLimit(1) }
                    }
                    .padding(8).background(RoundedRectangle(cornerRadius: 8).fill(Color(nsColor: .controlBackgroundColor)))
                }
            }
            .padding(16)
        }
    }
}

/// Chips with a remove button each.
struct RemovableChips: View {
    let items: [String]
    let remove: (String) -> Void
    var body: some View {
        if items.isEmpty { Text("None yet").font(.caption).foregroundStyle(.secondary) }
        else {
            HStack(spacing: 6) {
                ForEach(items, id: \.self) { w in
                    HStack(spacing: 3) {
                        Text(w).font(.caption)
                        Button { remove(w) } label: { Image(systemName: "xmark").font(.system(size: 8, weight: .bold)) }.buttonStyle(.borderless)
                    }
                    .padding(.horizontal, 8).padding(.vertical, 3).background(Color.accentColor.opacity(0.12), in: Capsule())
                }
            }
        }
    }
}
