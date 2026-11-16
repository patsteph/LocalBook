import Foundation
import Observation
import LocalBookCore
import LocalBookEngines

/// What the window shows, built around the package's `LibraryModel`: the same engine the iPhone runs, answering on
/// this Mac from the notebooks synced over the phone-class link. Phase 2 of `docs/swift-migration/`.
@Observable @MainActor
final class AppModel {
    let library = LibraryModel()
    /// Drops and intents that add to a notebook.
    let intake = IntakeModel()

    enum Destination: Hashable {
        case notebook(String)
        /// One source, chosen under its notebook in the sidebar.
        case source(SourceWindowKey)
        /// The selected notebook's sources, in a pane (View › Show Sources, ⌥⌘1). Not a sidebar row.
        case sources
        case studio
        case meetings
        case notes
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

    /// Why the last section list failed, for the Move to Section menu; nil once it works.
    var sectionsProblem: String?
    func loadSections() async {
        do { macSections = try await library.sectionsFromMac(); sectionsProblem = nil }
        catch { sectionsProblem = error.localizedDescription }
    }

    /// Runs one change through the Mac, shows its problem if any, then tells open lists to reload.
    func edit(_ work: @escaping @MainActor () async -> String?) {
        prompts.busy = true
        Task { @MainActor in
            let problem = await work()
            prompts.busy = false
            if let problem { prompts.problem = problem }
            NotificationCenter.default.post(name: .librarySynced, object: nil)
            await loadSections()
        }
    }

    /// Paired with a Python Mac and the code confirmed there. Mirrors `MacLink.paired` so views can observe it.
    var paired = MacLink.paired?.verified == true
    /// The pairing sheet (menu: Notebook › Pair with a Mac…, or the empty states).
    var showPairing = false
    /// The import sheet: move the library of the LocalBook already on this Mac into this app.
    var showImport = false
    /// The inspector on the right edge (View › Show Inspector, ⌥⌘I): the notebook's sources.
    var showInspector = false
    /// A Studio kind the menu asked for (Studio › Briefing…): the Studio pane opens its make sheet.
    var makeRequest: StudioItem.Kind?
    /// A question from an intent or a `localbook://ask` link, asked by the Ask view once it is on screen.
    var pendingQuestion: String?
    /// Record › Voice Note (⇧⌘V): the note editor with dictation already listening.
    var showVoiceNote = false
    /// Files that arrived without a notebook to land in (opened from the Finder or the Dock, or added with no
    /// notebook selected): a small sheet asks which notebook, then they go the drop's way.
    var filesToPlace: [URL] = []

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
        library.askMode = .phone                 // this Mac searches and answers itself, as the phone does
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
        await library.open()
        syncSelectionToScope()
        if paired { Task { await runSync(); await loadSections() } }
        // A library moved over from the old app answers without a pairing; the models warm for it too.
        if paired || !library.notebooks.isEmpty { await startModels() }
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
    }

    /// The pairing sheet finished: the first notebook is here, the rest keeps copying.
    func pairingDone() {
        paired = MacLink.paired?.verified == true
        showPairing = false
        syncSelectionToScope()
        Task { await startModels() }
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
    func make(_ kind: StudioItem.Kind) {
        selection = .studio
        makeRequest = kind
    }
}
