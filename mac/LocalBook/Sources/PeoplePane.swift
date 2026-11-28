import LocalBookCore
import LocalBookEngines
import SwiftUI
import WebKit

/// The People profiler for the notebook on screen: the team, one person at a time (what their pages say, what they
/// have been up to, the coaching notes and goals, what changed), and the setup (team, schedule, sign-ins).
struct PeoplePane: View {
    @Environment(AppModel.self) private var model
    @State private var notebookId: String?
    @State private var selected: String?
    @State private var tab: Tab = .team
    @State private var adding = false
    @State private var signingIn: SocialPlatform?

    enum Tab: String, CaseIterable { case team = "Team", setup = "Setup" }

    private var people: PeopleEngine { Agents.people }
    private var library: LibraryModel { model.library }
    private var notebook: LibraryNotebookInfo? { library.notebooks.first { $0.id == notebookId } }
    private var config: PeopleConfig { notebookId.map { people.config($0) } ?? PeopleConfig() }

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            Group {
                switch tab {
                case .team: team
                case .setup: setup
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .onAppear { if notebookId == nil || notebook == nil { notebookId = library.scopeToNotebook ?? library.notebooks.first?.id } }
        .onChange(of: library.scopeToNotebook) { _, id in if let id { notebookId = id; selected = nil } }
        .onChange(of: model.peopleRequest, initial: true) { _, r in
            guard let r else { return }
            model.peopleRequest = nil
            switch r {
            case .collect: Task { if let notebook { await people.collectAll(notebook: notebook, library: library) } }
            case .add(let name): adding = true; _ = name
            case .show: break
            }
        }
        .sheet(isPresented: $adding) { if let notebookId { MemberSheet(notebook: notebookId, person: nil) } }
        .sheet(item: $signingIn) { p in SignInSheet(platform: p) }
    }

    private var header: some View {
        HStack(spacing: 12) {
            Image(systemName: "person.2").foregroundStyle(.pink)
            VStack(alignment: .leading, spacing: 0) {
                Text((config.teamName.isEmpty ? "People" : config.teamName) + " · " + (notebook?.title ?? "")).font(.headline)
                if let s = notebookId.flatMap({ people.status[$0] }) { Text(s).font(.caption).foregroundStyle(.secondary) }
                else if let e = people.error { Text(e).font(.caption).foregroundStyle(.red).lineLimit(2) }
                else { Text("\(config.members.count) \(config.members.count == 1 ? "person" : "people")" + (config.lastCollectionRun.map { " · last read \($0.formatted(date: .abbreviated, time: .shortened))" } ?? "")).font(.caption).foregroundStyle(.secondary) }
            }
            Spacer()
            Picker("", selection: $tab) { ForEach(Tab.allCases, id: \.self) { Text($0.rawValue).tag($0) } }.pickerStyle(.segmented).frame(width: 160)
            Picker("Notebook", selection: Binding(get: { notebookId ?? "" }, set: { notebookId = $0.isEmpty ? nil : $0; selected = nil })) {
                ForEach(library.notebooks) { nb in Text(nb.title).tag(nb.id) }
            }.labelsHidden().frame(width: 180)
            Button { Task { if let notebook { await people.collectAll(notebook: notebook, library: library) } } } label: { Label("Read profiles now", systemImage: "arrow.down.circle") }
                .disabled(config.members.isEmpty || !people.running.isEmpty)
        }
        .padding(.horizontal, 16).padding(.vertical, 10)
    }

    // MARK: team

    private var team: some View {
        HSplitView {
            List(selection: $selected) {
                ForEach(config.members) { p in
                    HStack(spacing: 10) {
                        Image(systemName: "person.crop.circle").font(.title2).foregroundStyle(.secondary)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(p.name).font(.body.weight(.medium))
                            Text(p.roleLine.isEmpty ? (p.headline.isEmpty ? "No role yet" : p.headline) : p.roleLine).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                        }
                        Spacer()
                        if people.running.contains(p.id) { ProgressView().controlSize(.small) }
                        else if !p.recentChanges.isEmpty, let latest = p.recentChanges.first, Date().timeIntervalSince(latest.detectedAt) < 14 * 86_400 {
                            Image(systemName: "circle.fill").font(.system(size: 8)).foregroundStyle(latest.severity == "important" ? Color.orange : Color.blue).help(latest.description)
                        }
                    }
                    .tag(p.id)
                    .contextMenu {
                        Button("Read profiles now") { Task { if let notebook { await people.collect(p.id, notebook: notebook, library: library) } } }
                        Button("Remove from team", role: .destructive) { Task { if let notebookId { await people.remove(p.id, in: notebookId, library: library); if selected == p.id { selected = nil } } } }
                    }
                }
                Button { adding = true } label: { Label("Add a person", systemImage: "plus") }.buttonStyle(.plain).foregroundStyle(.tint).padding(.vertical, 4)
            }
            .frame(minWidth: 220, idealWidth: 260, maxWidth: 340)
            if let id = selected, let p = config.member(id), let notebookId {
                PersonView(person: p, notebook: notebookId, notebookInfo: notebook, coaching: config.coachingEnabled)
            } else {
                VStack(spacing: 10) {
                    Image(systemName: "person.2").font(.system(size: 40)).foregroundStyle(.secondary)
                    if config.members.isEmpty {
                        Text("Nobody here yet.").font(.title3)
                        Text("Add the people this notebook is about, with the addresses of their public pages. The profiler reads those pages (signed in where a site needs it), works out what each person has been up to, writes coaching insights against what is in this notebook, and makes each profile a source so you can ask about them. Your notes about a person stay on this Mac.")
                            .multilineTextAlignment(.center).foregroundStyle(.secondary).frame(maxWidth: 460)
                        Button("Add a person") { adding = true }.buttonStyle(.borderedProminent)
                    } else { Text("Pick someone on the left.").foregroundStyle(.secondary) }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
    }

    // MARK: setup

    private var setup: some View {
        Form {
            Section("This team") {
                TextField("Team name", text: Binding(get: { config.teamName }, set: { v in if let notebookId { people.update(notebookId) { $0.teamName = v } } }), prompt: Text("Platform engineering"))
                Toggle("Coaching notebook (notes, goals and coaching insights)", isOn: Binding(get: { config.coachingEnabled }, set: { v in if let notebookId { people.update(notebookId) { $0.coachingEnabled = v } } }))
                Picker("Read everyone's pages", selection: Binding(get: { config.collectionSchedule }, set: { v in if let notebookId { people.update(notebookId) { $0.collectionSchedule = v } } })) {
                    ForEach(PeopleConfig.Schedule.allCases, id: \.self) { Text($0.label).tag($0) }
                }
                Text("Reads happen while the app is open. Each read updates the person's profile source in this notebook, the activity picture and the coaching insights, and notes what changed.").font(.caption).foregroundStyle(.secondary)
            }
            Section("Sign-ins (for pages only a signed-in visitor can see)") {
                ForEach(SocialPlatform.allCases.filter(\.needsSignIn)) { p in SignInRow(platform: p) { signingIn = p } }
                Text("You sign in yourself, in a window inside LocalBook. The sign-in is kept in LocalBook's own cookie store on this Mac, used only to load these pages, and never read by anything else.").font(.caption).foregroundStyle(.secondary)
            }
            Section("How it works") {
                Text("Each page is loaded the way a browser would and read as text; the answer model turns the text into fields (role, company, headline, bio, skills, experience, recent posts, repositories). Posting frequency and content types come from the dates and shapes of what was found. Coaching insights follow the old rules: your notes and goals are primary, the person's stated grade anchors everything, and growth areas appear only where this notebook's documents show a gap. The profile and your notes are indexed as sources of this notebook (never anywhere else), so Ask can answer about people.")
                    .font(.callout).foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
    }
}

/// One platform's sign-in state and buttons.
private struct SignInRow: View {
    let platform: SocialPlatform
    let signIn: () -> Void
    @State private var signedIn = false
    private var people: PeopleEngine { Agents.people }
    var body: some View {
        HStack {
            Text(platform.label)
            Spacer()
            Text(signedIn ? "Signed in" : "Not signed in").font(.caption).foregroundStyle(signedIn ? Color.green : Color.secondary)
            Button(signedIn ? "Sign in again" : "Sign in…", action: signIn)
            if signedIn { Button("Sign out") { Task { await people.signOut(platform); signedIn = await people.signedIn(platform) } } }
        }
        .task { signedIn = await people.signedIn(platform) }
    }
}

/// A web view on the site's login page, in the profiler's cookie store; the user signs in and closes it.
struct SignInSheet: View {
    let platform: SocialPlatform
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Sign in to \(platform.label)").font(.headline)
                Spacer()
                Text("Sign in as you normally would, then press Done. Your password goes to \(platform.label) only.").font(.caption).foregroundStyle(.secondary)
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            .padding(12)
            Divider()
            if let url = platform.loginURL { SignInWebView(url: url, store: Agents.people.webStore) }
        }
        .frame(width: 900, height: 700)
    }
}

struct SignInWebView: NSViewRepresentable {
    let url: URL
    let store: WKWebsiteDataStore
    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = store
        let v = WKWebView(frame: .zero, configuration: config)
        v.customUserAgent = WebExtract.userAgent
        v.load(URLRequest(url: url))
        return v
    }
    func updateNSView(_ nsView: WKWebView, context: Context) {}
}

/// Adding or editing one person.
struct MemberSheet: View {
    let notebook: String
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var person: PersonProfile
    @State private var tags = ""
    @State private var firstNote = ""
    private let isNew: Bool
    private var people: PeopleEngine { Agents.people }

    init(notebook: String, person: PersonProfile?) {
        self.notebook = notebook
        isNew = person == nil
        _person = State(initialValue: person ?? PersonProfile(notebookId: notebook, name: ""))
        _tags = State(initialValue: person?.tags.joined(separator: ", ") ?? "")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(isNew ? "Add a person" : "Edit \(person.name)").font(.title3.weight(.semibold))
            Form {
                TextField("Full name", text: $person.name)
                TextField("Role", text: $person.currentRole, prompt: Text("Senior Engineer"))
                TextField("Company", text: $person.currentCompany)
                TextField("Email (yours to keep; never sent anywhere)", text: $person.email)
                TextField("Tags, comma-separated", text: $tags, prompt: Text("mountain, new manager"))
                Section("Public pages") {
                    ForEach(SocialPlatform.allCases) { p in
                        TextField(p.label, text: Binding(get: { person.socialLinks[p.rawValue] ?? "" }, set: { person.socialLinks[p.rawValue] = $0 }), prompt: Text(p.placeholder))
                    }
                }
                if isNew { Section("First note (private)") { TextField("Anything you already know", text: $firstNote, axis: .vertical).lineLimit(2...4) } }
            }
            .formStyle(.grouped)
            HStack {
                Spacer()
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                Button(isNew ? "Add" : "Save") {
                    var p = person
                    p.name = p.name.trimmingCharacters(in: .whitespaces)
                    p.tags = tags.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
                    p.socialLinks = p.socialLinks.filter { !$0.value.trimmingCharacters(in: .whitespaces).isEmpty }.mapValues { $0.trimmingCharacters(in: .whitespaces) }
                    if isNew, !firstNote.trimmingCharacters(in: .whitespaces).isEmpty { p.coachingNotes.append(CoachingNote(text: firstNote.trimmingCharacters(in: .whitespaces))) }
                    people.save(p, in: notebook)
                    if isNew, !p.coachingNotes.isEmpty { people.update(notebook) { $0.coachingEnabled = true } }
                    dismiss()
                }.keyboardShortcut(.defaultAction).disabled(person.name.trimmingCharacters(in: .whitespaces).isEmpty)
            }
        }
        .padding(20).frame(width: 540, height: 620)
    }
}

/// One person: overview, activity, coaching, changes.
struct PersonView: View {
    let person: PersonProfile
    let notebook: String
    let notebookInfo: LibraryNotebookInfo?
    let coaching: Bool
    @Environment(AppModel.self) private var model
    @State private var tab: Tab = .overview
    @State private var editing = false
    @State private var noteText = ""
    @State private var noteCategory: CoachingNote.Category = .general
    @State private var goalText = ""

    enum Tab: String, CaseIterable { case overview = "Overview", activity = "Activity", coaching = "Coaching", changes = "Changes", sources = "Pages" }
    private var people: PeopleEngine { Agents.people }
    private var tabs: [Tab] { Tab.allCases.filter { $0 != .coaching || coaching } }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(person.name).font(.title2.weight(.semibold))
                    if !person.roleLine.isEmpty { Text(person.roleLine).foregroundStyle(.secondary) }
                    if !person.headline.isEmpty, person.headline != person.roleLine { Text(person.headline).font(.callout).foregroundStyle(.secondary) }
                    HStack(spacing: 8) {
                        if !person.location.isEmpty { Label(person.location, systemImage: "mappin").font(.caption).foregroundStyle(.secondary) }
                        ForEach(person.tags, id: \.self) { t in Text(t).font(.caption2).padding(.horizontal, 6).padding(.vertical, 2).background(.quaternary, in: Capsule()) }
                    }
                }
                Spacer()
                Button("Edit…") { editing = true }
                Button { Task { if let notebookInfo { await people.collect(person.id, notebook: notebookInfo, library: model.library) } } } label: { Label("Read pages", systemImage: "arrow.down.circle") }
                    .disabled(people.running.contains(person.id) || person.links.isEmpty)
            }
            .padding(16)
            Picker("", selection: $tab) { ForEach(tabs, id: \.self) { Text($0.rawValue).tag($0) } }.pickerStyle(.segmented).padding(.horizontal, 16)
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    switch tab {
                    case .overview: overview
                    case .activity: activity
                    case .coaching: coachingView
                    case .changes: changes
                    case .sources: pages
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading).padding(16)
            }
        }
        .sheet(isPresented: $editing) { MemberSheet(notebook: notebook, person: person) }
    }

    private func heading(_ s: String) -> some View { Text(s.uppercased()).font(.caption.weight(.semibold)).foregroundStyle(.secondary) }

    @ViewBuilder private var overview: some View {
        if person.bio.isEmpty && person.experience.isEmpty && person.skills.isEmpty && person.coachingInsights == nil {
            Text(person.links.isEmpty ? "No pages yet. Edit… to add the addresses of this person's public pages, then Read pages." : "Nothing read yet. Press Read pages.").foregroundStyle(.secondary)
        }
        if !person.bio.isEmpty { heading("About"); Text(person.bio).textSelection(.enabled) }
        if !person.experience.isEmpty {
            heading("Experience")
            ForEach(Array(person.experience.prefix(8).enumerated()), id: \.offset) { _, e in
                VStack(alignment: .leading, spacing: 1) {
                    Text(e.title + (e.company.isEmpty ? "" : " · \(e.company)")).font(.callout.weight(.medium))
                    if !e.dates.isEmpty { Text(e.dates).font(.caption).foregroundStyle(.secondary) }
                }
            }
        }
        if !person.education.isEmpty { heading("Education"); ForEach(Array(person.education.enumerated()), id: \.offset) { _, e in Text([e.degree, e.field, e.school, e.dates].filter { !$0.isEmpty }.joined(separator: " · ")).font(.callout) } }
        if !person.skills.isEmpty { heading("Skills"); Text(person.skills.joined(separator: " · ")).font(.callout) }
        if let ci = person.coachingInsights {
            heading("Coaching insights" + (ci.generatedAt.map { " · \($0.formatted(date: .abbreviated, time: .omitted))" } ?? ""))
            if !ci.overallSummary.isEmpty { Text(ci.overallSummary).textSelection(.enabled) }
            insightList("Strengths", ci.strengths, color: .green)
            insightList("Growth areas", ci.growthAreas, color: .orange)
            insightList("Conversation starters", ci.conversationStarters, color: .blue)
            insightList("Topic trends", ci.topicTrends, color: .purple)
            insightList("Goal observations", ci.goalObservations, color: .teal)
        }
    }

    @ViewBuilder private func insightList(_ title: String, _ items: [String], color: Color) -> some View {
        if !items.isEmpty {
            Text(title).font(.callout.weight(.semibold)).foregroundStyle(color)
            ForEach(items, id: \.self) { Text("• " + $0).font(.callout).textSelection(.enabled) }
        }
    }

    @ViewBuilder private var activity: some View {
        let a = person.activityProfile
        if a.platformFrequency.isEmpty { Text("No activity read yet.").foregroundStyle(.secondary) }
        else {
            heading("Overall · " + ActivityAnalysis.label(a.overallFrequency) + (a.overallLastActive.isEmpty ? "" : " · last seen \(a.overallLastActive.prefix(10))"))
            if !a.focusSummary.isEmpty { Text(a.focusSummary).textSelection(.enabled) }
            ForEach(a.platformFrequency.sorted { $0.key < $1.key }, id: \.key) { pair in
                let k = pair.key, v = pair.value
                HStack {
                    Text(SocialPlatform(rawValue: k)?.label ?? k).font(.callout.weight(.medium)).frame(width: 110, alignment: .leading)
                    Text(ActivityAnalysis.label(v)).font(.callout)
                    if let types = a.contentTypes[k] { Text(types.joined(separator: ", ")).font(.caption).foregroundStyle(.secondary) }
                    if let last = a.platformLastActive[k] { Spacer(); Text(last.prefix(10)).font(.caption).foregroundStyle(.secondary) }
                }
            }
            if !a.topics.isEmpty { heading("Topics"); Text(a.topics.joined(separator: " · ")).font(.callout) }
            if !a.recentItems.isEmpty {
                heading("Lately")
                ForEach(a.recentItems) { item in
                    VStack(alignment: .leading, spacing: 1) {
                        Text(item.title).font(.callout)
                        Text([SocialPlatform(rawValue: item.platform)?.label ?? item.platform, item.contentType, String(item.date.prefix(10))].filter { !$0.isEmpty }.joined(separator: " · ")).font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            if !person.githubRepos.isEmpty { heading("Repositories"); ForEach(person.githubRepos, id: \.self) { Text($0).font(.callout) } }
        }
    }

    @ViewBuilder private var coachingView: some View {
        heading("Notes (private)")
        HStack(alignment: .top) {
            TextField("A note about \(person.name)", text: $noteText, axis: .vertical).lineLimit(1...4)
            Picker("", selection: $noteCategory) { ForEach(CoachingNote.Category.allCases, id: \.self) { Text($0.label).tag($0) } }.labelsHidden().frame(width: 130)
            Button("Add") { let t = noteText; noteText = ""; Task { await people.addNote(t, category: noteCategory, to: person.id, in: notebook, library: model.library) } }
                .disabled(noteText.trimmingCharacters(in: .whitespaces).isEmpty)
        }
        if person.coachingNotes.isEmpty { Text("No notes yet. Notes are the primary context for the coaching insights.").font(.caption).foregroundStyle(.secondary) }
        ForEach(person.coachingNotes.sorted { $0.createdAt > $1.createdAt }) { n in
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 1) {
                    Text(n.text).textSelection(.enabled)
                    Text("\(n.category.label) · \(n.createdAt.formatted(date: .abbreviated, time: .omitted))").font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button(role: .destructive) { Task { await people.deleteNote(n.id, from: person.id, in: notebook, library: model.library) } } label: { Image(systemName: "trash") }.buttonStyle(.plain).foregroundStyle(.secondary)
            }
            .padding(10).background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 8))
        }
        heading("Goals")
        HStack {
            TextField("A goal for \(person.name)", text: $goalText)
            Button("Add") { let g = goalText; goalText = ""; people.addGoal(g, targetDate: nil, to: person.id, in: notebook) }.disabled(goalText.trimmingCharacters(in: .whitespaces).isEmpty)
        }
        ForEach(person.goals) { g in
            HStack {
                Text(g.goal).strikethrough(g.status == .completed || g.status == .dropped)
                Spacer()
                Picker("", selection: Binding(get: { g.status }, set: { people.setGoal(g.id, status: $0, of: person.id, in: notebook) })) { ForEach(CoachingGoal.Status.allCases, id: \.self) { Text($0.rawValue).tag($0) } }.labelsHidden().frame(width: 120)
                Button(role: .destructive) { people.deleteGoal(g.id, of: person.id, in: notebook) } label: { Image(systemName: "trash") }.buttonStyle(.plain).foregroundStyle(.secondary)
            }
        }
        if let ci = person.coachingInsights, !ci.goalObservations.isEmpty { heading("On the goals"); ForEach(ci.goalObservations, id: \.self) { Text("• " + $0).font(.callout) } }
    }

    @ViewBuilder private var changes: some View {
        if person.recentChanges.isEmpty { Text("No changes seen between reads yet. The first read sets the baseline.").foregroundStyle(.secondary) }
        ForEach(person.recentChanges) { c in
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: c.severity == "important" ? "exclamationmark.circle.fill" : c.severity == "notable" ? "circle.fill" : "circle").foregroundStyle(c.severity == "important" ? Color.orange : Color.blue).font(.caption)
                VStack(alignment: .leading, spacing: 1) {
                    Text(c.description)
                    Text("\(c.category) · \(c.detectedAt.formatted(date: .abbreviated, time: .shortened))").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }

    @ViewBuilder private var pages: some View {
        if person.links.isEmpty { Text("No pages. Edit… to add some.").foregroundStyle(.secondary) }
        ForEach(person.links, id: \.url) { l in
            HStack { Text(l.platform.label).font(.callout.weight(.medium)).frame(width: 110, alignment: .leading); Link(l.url, destination: URL(string: l.url) ?? URL(fileURLWithPath: "/")).font(.callout).lineLimit(1) }
        }
        if !person.sources.isEmpty {
            heading("Reads")
            ForEach(person.sources.suffix(12).reversed()) { s in
                HStack {
                    Image(systemName: s.success ? "checkmark.circle" : "xmark.circle").foregroundStyle(s.success ? Color.green : Color.red)
                    Text((SocialPlatform(rawValue: s.platform)?.label ?? s.platform) + " · " + s.capturedAt.formatted(date: .abbreviated, time: .shortened)).font(.callout)
                    Text(s.success ? (s.dataFields.isEmpty ? "read, nothing structured" : s.dataFields.joined(separator: ", ")) : (s.error ?? "failed")).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
            }
        }
    }
}
