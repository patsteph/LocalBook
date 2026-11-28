import Foundation
import LocalBookCore
import Observation
#if canImport(WebKit)
import WebKit
#endif

/// Runs a notebook's People profiler: reads each member's public pages in WebKit (signed in where the site needs it,
/// with cookies the user put there through the sign-in sheet), has the answer model turn the page text into fields,
/// works out the activity picture, writes the coaching insights against the notebook, indexes the profile and the
/// user's notes as sources of the notebook so Ask can answer about the person, and notes what changed since the last
/// read. Nothing about anyone leaves this Mac except the page loads themselves. A timer runs the due notebooks while
/// the app is open.
@MainActor @Observable
public final class PeopleEngine {
    public private(set) var configs: [String: PeopleConfig] = [:]
    public private(set) var running: Set<String> = []          // member ids being read
    public private(set) var status: [String: String] = [:]     // notebook → line
    public private(set) var error: String?
    private var timer: Timer?
    private weak var library: LibraryModel?

    public init() {}

    // MARK: config

    public func config(_ notebook: String) -> PeopleConfig {
        if let c = configs[notebook] { return c }
        let c = PeopleStore.load(notebook: notebook)
        configs[notebook] = c
        return c
    }
    public func update(_ notebook: String, _ change: (inout PeopleConfig) -> Void) {
        var c = config(notebook)
        change(&c)
        c.updatedAt = Date()
        configs[notebook] = c
        PeopleStore.save(c, notebook: notebook)
    }
    public func member(_ id: String, in notebook: String) -> PersonProfile? { config(notebook).member(id) }
    public func save(_ person: PersonProfile, in notebook: String) { update(notebook) { $0.update(person) } }
    public func remove(_ personId: String, in notebook: String, library: LibraryModel) async {
        guard let p = member(personId, in: notebook) else { return }
        await removeSources(of: p, in: notebook, library: library)
        update(notebook) { $0.members.removeAll { $0.id == personId } }
    }
    /// Whether a notebook has a People profiler set up.
    public func isPeopleNotebook(_ notebook: String) -> Bool { !config(notebook).isEmpty }

    // MARK: notes and goals

    public func addNote(_ text: String, category: CoachingNote.Category, to personId: String, in notebook: String, library: LibraryModel) async {
        guard var p = member(personId, in: notebook) else { return }
        p.coachingNotes.append(CoachingNote(text: text, category: category))
        save(p, in: notebook)
        update(notebook) { $0.coachingEnabled = true }
        await indexNotes(p, in: notebook, library: library)
    }
    public func deleteNote(_ noteId: String, from personId: String, in notebook: String, library: LibraryModel) async {
        guard var p = member(personId, in: notebook) else { return }
        p.coachingNotes.removeAll { $0.id == noteId }
        save(p, in: notebook)
        await indexNotes(p, in: notebook, library: library)
    }
    public func addGoal(_ goal: String, targetDate: String?, to personId: String, in notebook: String) {
        guard var p = member(personId, in: notebook) else { return }
        p.goals.append(CoachingGoal(goal: goal, targetDate: targetDate))
        save(p, in: notebook)
        update(notebook) { $0.coachingEnabled = true }
    }
    public func setGoal(_ goalId: String, status: CoachingGoal.Status, of personId: String, in notebook: String) {
        guard var p = member(personId, in: notebook), let i = p.goals.firstIndex(where: { $0.id == goalId }) else { return }
        p.goals[i].status = status
        save(p, in: notebook)
    }
    public func deleteGoal(_ goalId: String, of personId: String, in notebook: String) {
        guard var p = member(personId, in: notebook) else { return }
        p.goals.removeAll { $0.id == goalId }
        save(p, in: notebook)
    }

    // MARK: sign-ins

    #if canImport(WebKit)
    /// One persistent WebKit store for the profiler's sign-ins, apart from everything else the app loads.
    public var webStore: WKWebsiteDataStore {
        let key = "LocalBook.peopleWebStore"
        let id: UUID
        if let s = UserDefaults.standard.string(forKey: key), let u = UUID(uuidString: s) { id = u }
        else { id = UUID(); UserDefaults.standard.set(id.uuidString, forKey: key) }
        return WKWebsiteDataStore(forIdentifier: id)
    }
    /// Whether cookies for the platform's domains are in the store (a sign-in happened; the site decides if it still holds).
    public func signedIn(_ platform: SocialPlatform) async -> Bool {
        guard platform.needsSignIn else { return true }
        let cookies = await webStore.httpCookieStore.allCookies()
        return cookies.contains { c in platform.cookieDomains.contains { c.domain.hasSuffix($0) } && !c.value.isEmpty }
    }
    public func signOut(_ platform: SocialPlatform) async {
        let store = webStore
        for c in await store.httpCookieStore.allCookies() where platform.cookieDomains.contains(where: { c.domain.hasSuffix($0) }) {
            await store.httpCookieStore.deleteCookie(c)
        }
    }
    #endif

    // MARK: schedule

    public func startSchedule(library: LibraryModel) {
        self.library = library
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 1_800, repeats: true) { [weak self] _ in Task { @MainActor in await self?.runDue() } }
        Task { try? await Task.sleep(for: .seconds(150)); await runDue() }
    }
    public func runDue() async {
        guard let library, Agents.schedulesRunHere else { return }
        for id in PeopleStore.notebooks() where config(id).isDue() {
            guard let nb = library.notebooks.first(where: { $0.id == id }) else { continue }
            await collectAll(notebook: nb, library: library, trigger: "schedule")
        }
    }

    // MARK: collection

    public func collectAll(notebook: LibraryNotebookInfo, library: LibraryModel, trigger: String = "manual") async {
        let members = config(notebook.id).members
        for m in members where !m.links.isEmpty { await collect(m.id, notebook: notebook, library: library, trigger: trigger) }
        update(notebook.id) { $0.lastCollectionRun = Date() }
    }

    /// Reads one person's pages and runs the pipeline behind them.
    public func collect(_ personId: String, notebook: LibraryNotebookInfo, library: LibraryModel, trigger: String = "manual") async {
        guard !running.contains(personId), var p = member(personId, in: notebook.id) else { return }
        running.insert(personId); defer { running.remove(personId); status[notebook.id] = nil }
        error = nil
        let before = ProfileChanges.Snapshot(p)
        var problems: [String] = []
        let gemma = GPUGuard.canStart ? await AgentText.model(library) : nil

        for (platform, url) in p.links {
            status[notebook.id] = "Reading \(p.name)'s \(platform.label)…"
            guard let u = URL(string: url.hasPrefix("http") ? url : "https://" + url) else { problems.append("\(platform.label): not an address"); continue }
            #if canImport(WebKit)
            if platform.needsSignIn, !(await signedIn(platform)) {
                p.sources.append(ProfileSource(platform: platform.rawValue, url: url, success: false, error: "not signed in"))
                problems.append("\(platform.label): sign in first (Setup)")
                continue
            }
            #endif
            do {
                let text = try await pageText(u, platform: platform)
                guard !text.isEmpty else { throw LabError.model("the page had no text") }
                let extracted: PeoplePrompts.Extracted
                if let gemma {
                    status[notebook.id] = "Reading what \(platform.label) says about \(p.name)…"
                    let reply = await AgentText.generate(gemma, instructions: PeoplePrompts.extractInstructions, prompt: PeoplePrompts.extractPrompt(pageText: text, platform: platform, url: url), tokens: 900, temperature: 0.1, label: "people")
                    extracted = PeoplePrompts.readExtracted(reply) ?? Self.plainExtract(text, platform: platform)
                } else {
                    extracted = Self.plainExtract(text, platform: platform)
                }
                PeoplePrompts.merge(extracted, from: platform, into: &p)
                if platform == .personalSite, p.blogPosts.isEmpty, extracted.filled.isEmpty { p.bio = p.bio.isEmpty ? String(text.prefix(600)) : p.bio }
                p.sources.append(ProfileSource(platform: platform.rawValue, url: url, dataFields: extracted.filled))
                p.lastCollected[platform.rawValue] = PeopleDates.string(Date())
            } catch {
                p.sources.append(ProfileSource(platform: platform.rawValue, url: url, success: false, error: error.localizedDescription))
                problems.append("\(platform.label): \(error.localizedDescription)")
            }
        }
        if p.sources.count > 60 { p.sources.removeFirst(p.sources.count - 60) }

        // Activity, with the model's two-sentence focus line when it is there.
        p.activityProfile = ActivityAnalysis.analyze(p)
        if let gemma, let prompt = PeoplePrompts.focusPrompt(p) {
            status[notebook.id] = "Summing up what \(p.name) has been up to…"
            if let line = await AgentText.generate(gemma, instructions: PeoplePrompts.focusInstructions, prompt: prompt, tokens: 160, temperature: 0.4, label: "people") {
                p.activityProfile.focusSummary = line.replacingOccurrences(of: "\n", with: " ")
            }
        }
        save(p, in: notebook.id)

        // The profile and the notes as sources of the notebook.
        status[notebook.id] = "Adding \(p.name)'s profile to the notebook…"
        await indexProfile(p, in: notebook.id, library: library)
        if !p.coachingNotes.isEmpty { await indexNotes(p, in: notebook.id, library: library) }

        // Coaching insights from everything, against the notebook.
        if let gemma, PeoplePrompts.hasEnough(p) {
            status[notebook.id] = "Writing coaching insights for \(p.name)…"
            let context = await context(for: p, notebook: notebook, library: library)
            let reply = await AgentText.generate(gemma, instructions: PeoplePrompts.coachingInstructions, prompt: PeoplePrompts.coachingPrompt(p, context: context), tokens: 900, temperature: 0.4, label: "people")
            if let ci = PeoplePrompts.readInsights(reply) { p.coachingInsights = ci; save(p, in: notebook.id); await indexProfile(p, in: notebook.id, library: library) }
        }

        // What moved.
        let changes = ProfileChanges.detect(previous: before, current: p)
        if !changes.isEmpty {
            p.recentChanges = Array((changes + p.recentChanges).prefix(20))
            save(p, in: notebook.id)
            for c in changes { EventLog.record("person_changed", notebook: notebook.id, title: "\(p.name): \(c.description)", detail: c.severity) }
        }
        EventLog.record("person_collected", notebook: notebook.id, title: p.name, detail: problems.isEmpty ? "\(p.links.count) page\(p.links.count == 1 ? "" : "s") read (\(trigger))" : problems.joined(separator: "; "))
        if !problems.isEmpty { error = problems.joined(separator: "\n") }
    }

    /// The page's text, through WebKit with the sign-in store where the site needs one (a plain fetch would show the login wall).
    private func pageText(_ url: URL, platform: SocialPlatform) async throws -> String {
        #if canImport(WebKit)
        let loader = PageLoader()
        let page = try await loader.load(url, timeout: .seconds(40), settle: .seconds(platform.needsSignIn ? 3 : 1.5), dataStore: platform.needsSignIn ? webStore : nil)
        let text = page.text.trimmingCharacters(in: .whitespacesAndNewlines)
        if platform.needsSignIn, Self.looksLikeLoginWall(text, title: page.title) { throw LabError.model("\(platform.label) wants a fresh sign-in (Setup › Sign in)") }
        return page.title.isEmpty ? text : page.title + "\n\n" + text
        #else
        throw LabError.model("No web view on this platform")
        #endif
    }

    static func looksLikeLoginWall(_ text: String, title: String) -> Bool {
        let t = (title + " " + text.prefix(600)).lowercased()
        return t.contains("sign in") && t.contains("password") && t.count < 1_500 || t.contains("log in to x") || t.contains("join linkedin") && t.contains("password")
    }

    /// With no model on hand: the first lines as a headline and bio, so a read still shows something.
    static func plainExtract(_ text: String, platform: SocialPlatform) -> PeoplePrompts.Extracted {
        var e = PeoplePrompts.Extracted()
        let lines = text.split(separator: "\n").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        if let first = lines.first, first.count < 120 { e.headline = first }
        e.bio = String(lines.dropFirst().joined(separator: " ").prefix(600))
        if platform == .github {
            e.repositories = lines.filter { $0.contains("/") == false && $0.count < 60 && $0.range(of: #"^[A-Za-z0-9_.-]+$"#, options: .regularExpression) != nil }.prefix(6).map { String($0) }
            if let c = lines.first(where: { $0.lowercased().contains("contributions in the last year") }) { e.contributions = String(c) }
        }
        return e
    }

    // MARK: notebook context

    private func context(for p: PersonProfile, notebook: LibraryNotebookInfo, library: LibraryModel) async -> PeoplePrompts.Context {
        var ctx = PeoplePrompts.Context()
        let own: Set<String> = [p.profileSourceId, p.notesSourceId]
        var seen: Set<String> = []
        for q in PeoplePrompts.contextQueries(for: p.name) {
            guard let found = try? await library.retrieve(question: q, notebook: notebook.id, excludingSources: own) else { continue }
            for h in found.hits.prefix(3) where !seen.contains(String(h.text.prefix(80))) {
                seen.insert(String(h.text.prefix(80)))
                ctx.passages.append((date: "", source: h.filename, text: h.text))
            }
        }
        // Dates per source, so the passages read oldest first.
        let sources = await library.sources(in: notebook.id)
        let dates = Dictionary(uniqueKeysWithValues: sources.map { ($0.name, String($0.created.prefix(10))) })
        ctx.passages = ctx.passages.map { (date: dates[$0.source] ?? "", source: $0.source, text: $0.text) }.sorted { $0.date < $1.date }
        if let topics = InsightStore.all(notebook: notebook.id).first(where: { $0.kind == .topics })?.decode(TopicsInsight.self) {
            ctx.topics = topics.topics.map { (name: $0.name, count: $0.chunkCount, keywords: $0.keywords) }
        }
        ctx.findings = FindingsStore.all(notebook: notebook.id).prefix(10).map { (kind: $0.kind.rawValue, title: $0.title) }
        ctx.questions = JourneyStore.turns(notebook: notebook.id).suffix(15).map(\.question).filter { $0.count > 10 }
        return ctx
    }

    // MARK: the notebook's sources

    private func indexProfile(_ p: PersonProfile, in notebook: String, library: LibraryModel) async {
        let text = ProfileText.profile(p)
        guard text.count > 40 else { return }
        await dropSource(p.profileSourceId, in: notebook, library: library)
        _ = try? await library.ingestText(text, title: "Profile: \(p.name)", format: "profile", notebook: notebook, url: p.links.first?.url, enrich: false, sourceId: p.profileSourceId)
    }
    private func indexNotes(_ p: PersonProfile, in notebook: String, library: LibraryModel) async {
        await dropSource(p.notesSourceId, in: notebook, library: library)
        let text = ProfileText.notes(p)
        guard !text.isEmpty else { return }
        _ = try? await library.ingestText(text, title: "Coaching notes: \(p.name)", format: "coaching_notes", notebook: notebook, url: nil, enrich: false, sourceId: p.notesSourceId)
    }
    private func dropSource(_ id: String, in notebook: String, library: LibraryModel) async {
        if let s = await library.sources(in: notebook).first(where: { $0.id == id }) { _ = await library.deleteSource(s) }
    }
    private func removeSources(of p: PersonProfile, in notebook: String, library: LibraryModel) async {
        await dropSource(p.profileSourceId, in: notebook, library: library)
        await dropSource(p.notesSourceId, in: notebook, library: library)
    }
}
