import Foundation
import Observation
import LocalBookCore
import LocalBookEngines

/// What the window shows, built around the package's `LibraryModel`: the same engine the iPhone runs, answering on
/// this Mac from the notebooks synced over the phone-class link. Phase 2 of `docs/swift-migration/`.
@Observable @MainActor
final class AppModel {
    let library: LibraryModel
    /// Paints the backdrops of visuals, fetched after launch like the other models. Two to choose from by
    /// machine: FLUX.1 schnell (nothing from outside the FLUX checkpoint; the default) and FLUX.2 Klein 4B (its
    /// Qwen3 text encoder, from Alibaba, is named in its note).
    /// Settings key: "schnell", "klein" or "none".
    nonisolated static let imageModelKey = "studio.imageModel"
    let klein = KleinImagery()
    let schnell = SchnellImagery()
    /// The setting as it stands: a Mac that already carries Klein from before the choice existed keeps it.
    nonisolated static var imageModelChoice: String {
        UserDefaults.standard.string(forKey: imageModelKey) ?? (KleinImagery.downloaded ? "klein" : "schnell")
    }
    /// The image model the setting names, or nil for none.
    var imagery: (any VisualImagery)? {
        switch Self.imageModelChoice {
        case "klein": klein
        case "schnell": schnell
        default: nil
        }
    }
    /// Drops and intents that add to a notebook.
    let intake = IntakeModel()
    /// This Mac as the host of the phone link: the iPhone pairs with it and syncs from it (Phase 4, first slice).
    let phoneHost: PhoneHostController
    /// The loopback API for agents, OpenAI-style companions and the browser extension (Settings › Agents).
    let localAPI: LocalAPIController
    let backup = BackupController()

    enum Destination: Hashable {
        case notebook(String)
        /// One source, chosen under its notebook in the sidebar.
        case source(SourceWindowKey)
        /// The selected notebook's sources, in a pane (View › Show Sources, ⌥⌘1). Not a sidebar row.
        case sources
        case studio
        case meetings
        case notes
        case insights
        /// The Canvas (View › Show Canvas, ⌥⌘5) and the Constellation (⌥⌘6).
        case canvas
        case constellation
        /// The Curator (⌥⌘7) and the Collector (⌥⌘8).
        case curator
        case collector
        /// The Correspondent (⌥⌘9) and the People profiler (⌥⌘0).
        case correspondent
        case people
    }

    var selection: Destination? {
        didSet {
            let id: String
            switch selection {
            case .notebook(let n)?: id = n
            case .source(let key)?: id = key.notebookId
            default: return
            }
            guard library.scopeToNotebook != id else { return }
            library.scopeToNotebook = id
            UserDefaults.standard.set(id, forKey: "lastNotebook")
        }
    }

    /// The Mac's sections with their ids (the synced list carries names only), for "Move to Section".
    var macSections: [LibraryModel.Section] = []
    /// The rename, new and delete prompts the sidebar's menus open; one host at the window root shows them.
    let prompts = EditPrompts()

    /// The section names in use on this Mac (nothing is asked of the paired Mac).
    func loadSections() async { macSections = await library.sections() }

    /// Runs one change (made here at once, queued for the paired Mac), shows its problem if any, then tells open lists to reload.
    func edit(_ work: @escaping @MainActor () async -> String?) {
        prompts.busy = true
        Task { @MainActor in
            let problem = await work()
            prompts.busy = false
            if let problem { prompts.problem = problem }
            NotificationCenter.default.post(name: .librarySynced, object: nil)
            await loadSections()
            if !library.notebooks.isEmpty { await startModels() }      // the first notebook made on an unpaired Mac: warm the models now
        }
    }

    /// Paired with a Python Mac and the code confirmed there. Mirrors `MacLink.paired` so views can observe it.
    var paired = MacLink.paired?.verified == true
    /// The pairing sheet (menu: Notebook › Pair with a Mac…, or the empty states).
    var showPairing = false
    /// The import sheet: move the library of the LocalBook already on this Mac into this app.
    var showImport = false
    var showFindSources = false
    /// Help › Keyboard Shortcuts (⌘/): the guide to every shortcut.
    var showShortcuts = false
    /// The inspector on the right edge (View › Show Inspector, ⌥⌘I): the notebook's sources.
    var showInspector = false
    /// A Studio kind the menu asked for (Studio › Briefing…): the Studio pane opens its make sheet.
    var makeRequest: StudioItem.Kind?
    /// An Insights menu choice: the Insights pane opens its sheet for this kind.
    var insightRequest: Insight.Kind?
    var writingRequest = false
    /// A Studio item or an insight the Canvas asked to open, by id; the pane selects it when it appears.
    var studioOpenRequest: String?
    /// An Agents menu or slash choice for the Curator or the Collector pane to act on when it is on screen.
    var curatorRequest: CuratorPiece.Kind?
    enum CollectorRequest: Equatable { case collect, research(String), show }
    var collectorRequest: CollectorRequest?
    enum CorrespondentRequest: Equatable { case checkMail, show }
    var correspondentRequest: CorrespondentRequest?
    enum PeopleRequest: Equatable { case collect, add(String), show }
    var peopleRequest: PeopleRequest?
    var insightOpenRequest: String?
    /// The subject typed after a `/briefing`-style command in the Ask field; the make sheet opens with it filled in.
    var makeSubject = ""
    /// The phrase typed after `/web` in the Ask field; the Find Sources sheet opens with it filled in.
    var findSourcesQuery = ""
    /// A question from an intent or a `localbook://ask` link, asked by the Ask view once it is on screen.
    var pendingQuestion: String?
    /// Record › Voice Note (⇧⌘V): the note editor with dictation already listening.
    var showVoiceNote = false
    /// Files that arrived without a notebook to land in (opened from the Finder or the Dock, or added with no
    /// notebook selected): a small sheet asks which notebook, then they go the drop's way.
    var filesToPlace: [URL] = []
    /// File › Add Web Page… (⇧⌘L), or links pasted with no notebook on screen: the sheet's text, shown when non-nil.
    var linkDraft: String?
    /// File › Watch Folder…: the folder picked in the open panel, waiting for its notebook in a small sheet.
    var folderToWatch: URL?
    /// The last line the folder scan wrote, for Settings › Library.
    var watchStatus = ""

    /// Starts watching `url` for `notebook` and scans it at once.
    func addWatchedFolder(_ url: URL, notebook: String, recursive: Bool) async -> String? {
        do {
            let folder = try await library.watchFolder(url, notebook: notebook, recursive: recursive)
            intake.status = "Reading \(url.lastPathComponent)…"
            let report = await library.scan(folder) { [weak self] s in self?.intake.status = s }
            intake.status = ""
            watchStatus = "\(url.lastPathComponent): \(report.line)"
            NotificationCenter.default.post(name: .librarySynced, object: nil)
            return nil
        } catch { return error.localizedDescription }
    }

    /// Every watched folder, now, then every five minutes while the app runs (the old app's cadence).
    func startWatching() {
        guard watchLoop == nil else { return }
        watchLoop = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                let lines = await self.library.scanWatchedFolders()
                if !lines.isEmpty {
                    self.watchStatus = lines.joined(separator: " · ")
                    NotificationCenter.default.post(name: .librarySynced, object: nil)
                }
                try? await Task.sleep(for: .seconds(300))
            }
        }
    }
    private var watchLoop: Task<Void, Never>?

    /// Add web addresses to the current notebook, or open the Add Web Page sheet with them when there is none.
    func addLinks(_ urls: [URL]) {
        guard !urls.isEmpty else { return }
        if let id = currentNotebookId { intake.take(links: urls, notebook: id, library: library) }
        else { linkDraft = urls.map(\.absoluteString).joined(separator: "\n") }
    }

    /// Add `urls` to the current notebook, or ask which notebook when there is none (or when `ask` says to).
    func addFiles(_ urls: [URL], ask: Bool = false) {
        guard !urls.isEmpty else { return }
        if !ask, let id = currentNotebookId { intake.take(files: urls, notebook: id, library: library) }
        else { filesToPlace.append(contentsOf: urls) }
    }
    /// What the Sources pane and the inspector list, and what Ask returns to: the scoped notebook.
    var currentNotebookId: String? { library.scopeToNotebook }
    private var modelsStarted = false

    init() {
        _ = try? AppFolders.ensure(AppFolders.base)
        let lib = LibraryModel()
        library = lib
        phoneHost = PhoneHostController(library: lib)
        localAPI = LocalAPIController(library: lib)
        lib.askMode = .phone                     // this Mac searches and answers itself, as the phone does
    }

    struct NotebookSection: Identifiable {
        var name: String
        var notebooks: [LibraryNotebookInfo]
        var id: String { name }
    }

    /// Notebooks grouped by the desktop app's sections, in its order (the rule the phone follows too).
    var sections: [NotebookSection] {
        var order: [String] = []
        var grouped: [String: [LibraryNotebookInfo]] = [:]
        for n in library.notebooks {
            let name = n.section?.isEmpty == false ? n.section! : "Notebooks"
            if grouped[name] == nil { order.append(name) }
            grouped[name, default: []].append(n)
        }
        return order.map { NotebookSection(name: $0, notebooks: grouped[$0] ?? []) }
    }

    var selectedNotebook: LibraryNotebookInfo? {
        library.notebooks.first { $0.id == library.scopeToNotebook }
    }

    /// The window title: the notebook, or the app when there is none yet.
    var title: String { selectedNotebook?.title ?? "LocalBook" }

    /// One calm line about readiness, for the window subtitle.
    var readiness: String {
        guard paired else { return "Not paired with a Mac" }
        if case .syncing(let line) = library.syncState { return line }
        if case .failed = library.syncState { return "Mac not reachable, showing local copy" }
        if case .failed = library.searchState { return "Search model needs attention (Settings › Models)" }
        if case .failed = library.answerModelState { return "Answer model needs attention (Settings › Models)" }
        if library.notebooks.isEmpty { return "Waiting for the first notebook" }
        return ready ? "Ready" : "Getting ready"
    }

    /// Search and the answer model are both loaded: a question is answered at once.
    var ready: Bool { library.searchState == .ready && library.answerModelState == .ready }

    /// Launch: open the library (no models), and when paired, sync and warm the models in the background so the
    /// composer is live in under a second and the first question never waits for a model to load.
    func start() async {
        applyImageModel()
        backup.applyPendingRestore()          // a staged restore goes in before anything opens the library
        await library.open()
        syncSelectionToScope()
        Task { await phoneHost.start() }         // the iPhone can find this Mac from the first second
        Task { await localAPI.start() }          // and so can the agents and the browser extension on it
        Agents.collector.startSchedule(library: library)   // due Collectors run while the app is open
        Agents.correspondent.mailFolder = MailFolderAccess.restore()
        Agents.correspondent.startSchedule(library: library)
        Agents.people.startSchedule(library: library)   // due People profilers read while the app is open
        if paired { Task { await runSync(); await loadSections() } }
        // A library moved over from the old app answers without a pairing; the models warm for it too.
        if paired || !library.notebooks.isEmpty { await startModels() }
    }

    /// A notebook made here was created on the paired Mac under its own id: the selection follows it.
    func notebookRenumbered(from old: String, to new: String) {
        if case .notebook(let id) = selection, id == old { selection = .notebook(new) }
        NotificationCenter.default.post(name: .librarySynced, object: nil)
    }

    /// The import finished: the sidebar reloads and the models warm if they have not.
    func importDone() {
        syncSelectionToScope()
        NotificationCenter.default.post(name: .librarySynced, object: nil)
        Task { await startModels() }
    }

    /// Search (Neural Engine) and the answer model (GPU) load at the same time, as on the phone.
    func startModels() async {
        guard !modelsStarted else { return }
        modelsStarted = true
        async let search: Void = {
            await self.library.prepareSearch()
            await self.library.loadVisionIfInstalled()
        }()
        async let answers: Void = self.library.prepareAnswerModel()
        _ = await (search, answers)
        startWatching()
        // The chosen image model's weights come down once the models that answer are warm, so they never slow those.
        if let imagery { Task { await imagery.prepare() } }
    }

    /// The setting changed, or the app started: the engine paints with the chosen model, and its weights come
    /// down if the other models are already warm (else `startModels` fetches them after).
    func applyImageModel() {
        StudioEngine.imagery = imagery
        if modelsStarted, let imagery { Task { await imagery.prepare() } }
    }

    /// The pairing sheet finished: the first notebook is here, the rest keeps copying.
    func pairingDone() {
        paired = MacLink.paired?.verified == true
        showPairing = false
        syncSelectionToScope()
        Task { await startModels() }
    }

    /// `@curator …` or the Curator slash command: one answer across every notebook, shown in the live area.
    func askCurator(_ text: String) {
        Task { await library.askCurator(text) }
    }

    func syncNow() {
        guard paired else { return }
        if case .syncing = library.syncState { return }
        Task { await runSync() }
    }

    /// A sync, then the lists that show sources reload.
    func runSync() async {
        await library.sync()
        NotificationCenter.default.post(name: .librarySynced, object: nil)
    }

    /// Remove the pairing and the copy of the library on this Mac.
    func forgetMac() async {
        await library.wipeLibrary()
        MacLink.paired = nil
        paired = false
        selection = nil
    }

    /// Keep the sidebar on the notebook the engine is scoped to (set by `refresh()` to the last one, or the first).
    func syncSelectionToScope() {
        guard let id = library.scopeToNotebook else { return }
        switch selection {
        case .notebook(let current) where current == id: return
        case .notebook, nil: selection = .notebook(id)
        default: return
        }
    }

    /// Back to the conversation with the current notebook (Notebook › Ask, ⌘L).
    func showAsk() {
        if let id = library.scopeToNotebook { selection = .notebook(id) } else { selection = nil }
    }

    /// Studio › Briefing, Quiz, …: show the Studio pane and open that kind's make sheet.
    func make(_ kind: StudioItem.Kind, subject: String = "") {
        selection = .studio
        makeSubject = subject
        makeRequest = kind
    }
}
