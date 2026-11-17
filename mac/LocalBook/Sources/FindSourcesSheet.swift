import LocalBookCore
import LocalBookEngines
import SwiftUI

/// File › Find Sources on the Web…: the old app's site search and source discovery in one sheet. Search mode asks
/// one site (or all of them) for a phrase within a time range; Discover mode takes one sentence of intent and lets
/// the answer model choose the terms and the sites. Either way the hits are a checklist, and the ticked ones go
/// into the chosen notebook through the same intake as a pasted link.
struct FindSourcesSheet: View {
    @Environment(AppModel.self) private var model
    @State private var mode = Mode.search
    @State private var query = ""
    @State private var site: SiteSearch.Site? = nil           // nil: every site
    @State private var range = SiteSearch.TimeRange.all
    @State private var notebookId = ""
    @State private var results: [SiteSearch.Result] = []
    @State private var chosen = Set<String>()
    @State private var status = ""
    @State private var problems: [String] = []
    @State private var planLine = ""
    @State private var working = false
    @FocusState private var focused: Bool

    enum Mode: String, CaseIterable, Identifiable { case search = "Search", discover = "Discover"; var id: String { rawValue } }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Find sources on the web").font(.headline)
                Spacer()
                Picker("", selection: $mode) { ForEach(Mode.allCases) { Text($0.rawValue).tag($0) } }
                    .pickerStyle(.segmented).frame(width: 180).labelsHidden()
            }
            HStack {
                TextField(mode == .search ? "What to search for" : "What this notebook is for, in a sentence", text: $query)
                    .textFieldStyle(.roundedBorder).focused($focused).onSubmit { run() }
                Button(working ? "Searching…" : (mode == .search ? "Search" : "Discover")) { run() }
                    .keyboardShortcut(.defaultAction).disabled(working || query.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            if mode == .search {
                HStack(spacing: 12) {
                    Picker("Site", selection: $site) {
                        Text("All sites").tag(SiteSearch.Site?.none)
                        Divider()
                        ForEach(SiteSearch.Site.allCases) { Text($0.label).tag(Optional($0)) }
                    }.frame(width: 190)
                    Picker("When", selection: $range) { ForEach(SiteSearch.TimeRange.allCases) { Text($0.label).tag($0) } }.frame(width: 190)
                    Spacer()
                }
            } else {
                Text("The answer model reads the sentence into a topic, search terms and the sites that fit, then searches them all.")
                    .font(.caption).foregroundStyle(.secondary)
            }
            resultsList
            if !planLine.isEmpty { Text(planLine).font(.caption).foregroundStyle(.secondary) }
            if !problems.isEmpty {
                Text(problems.joined(separator: " · ")).font(.caption).foregroundStyle(.orange).lineLimit(2)
            }
            HStack {
                Picker("Add to", selection: $notebookId) {
                    ForEach(model.sections) { section in
                        Section(section.name) { ForEach(section.notebooks) { Text($0.title).tag($0.id) } }
                    }
                }.frame(maxWidth: 320)
                Spacer()
                Button("Cancel") { model.showFindSources = false }.keyboardShortcut(.cancelAction)
                Button(chosen.count == 1 ? "Add 1 Source" : "Add \(chosen.count) Sources") { add() }
                    .disabled(chosen.isEmpty || notebookId.isEmpty)
            }
        }
        .padding(20).frame(width: 640, height: 560)
        .onAppear { notebookId = model.currentNotebookId ?? model.library.notebooks.first?.id ?? ""; focused = true }
    }

    private var resultsList: some View {
        Group {
            if results.isEmpty {
                ContentUnavailableView {
                    Label(status.isEmpty ? "No results yet" : status, systemImage: working ? "hourglass" : "globe")
                } description: {
                    Text(mode == .search ? "arXiv, Wikipedia, Hacker News, GitHub, Reddit, Stack Overflow, Semantic Scholar, OpenAlex and PubMed."
                                         : "Try “Follow research on sparse attention” or “Track Costco”.")
                }
            } else {
                List(results) { r in
                    HStack(alignment: .top, spacing: 8) {
                        Toggle("", isOn: Binding(get: { chosen.contains(r.id) }, set: { if $0 { chosen.insert(r.id) } else { chosen.remove(r.id) } }))
                            .labelsHidden().toggleStyle(.checkbox)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(r.title).font(.body).lineLimit(2)
                            if !r.snippet.isEmpty { Text(r.snippet).font(.caption).foregroundStyle(.secondary).lineLimit(2) }
                            Text([r.site.label, r.published, r.author].compactMap { $0 }.joined(separator: " · "))
                                .font(.caption2).foregroundStyle(.tertiary).lineLimit(1)
                        }
                        Spacer()
                        Link(destination: URL(string: r.url) ?? URL(string: "https://\(r.site.domain)")!) { Image(systemName: "arrow.up.right.square") }
                            .help(r.url)
                    }
                    .padding(.vertical, 2)
                }
                .listStyle(.inset).frame(maxHeight: .infinity)
                .overlay(alignment: .bottomTrailing) {
                    HStack {
                        Button("Select All") { chosen = Set(results.map(\.id)) }
                        Button("Select None") { chosen = [] }
                    }.controlSize(.small).padding(6)
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func run() {
        let q = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !q.isEmpty, !working else { return }
        working = true; results = []; chosen = []; problems = []; planLine = ""
        status = mode == .search ? "Searching…" : "Reading the intent…"
        let sites = site.map { [$0] } ?? SiteSearch.Site.allCases
        let when = range, how = mode, notebook = notebookId.isEmpty ? nil : notebookId, perSite = site == nil ? 6 : 20
        Task {
            if how == .search {
                let found = await SiteSearchClient.searchAll(sites, query: q, range: when, max: perSite)
                results = SourceDiscovery.merge(found.batches, max: 60)
                problems = found.problems
            } else {
                let d = await model.library.discoverSources(intent: q, notebook: notebook)
                results = d.results; problems = d.problems
                planLine = "\(d.plan.topic) · \(d.plan.purpose.rawValue.replacingOccurrences(of: "_", with: " ")) · " +
                    ([d.plan.topic] + d.plan.keywords).prefix(3).joined(separator: ", ") + " on " + d.plan.sites.map(\.label).joined(separator: ", ")
            }
            status = results.isEmpty ? "Nothing found" : ""
            working = false
        }
    }

    private func add() {
        let urls = results.filter { chosen.contains($0.id) }.compactMap { URL(string: $0.url) }
        let nb = notebookId
        model.showFindSources = false
        model.intake.take(links: urls, notebook: nb, library: model.library)
    }
}
