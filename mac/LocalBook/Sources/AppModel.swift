import Foundation
import Observation
import LocalBookCore
import LocalBookEngines

/// What the window shows, built around the package's `LibraryModel`: the same engine the iPhone runs, answering on
/// this Mac from the notebooks synced over the phone-class link. Phase 2 of `docs/swift-migration/`.
@Observable @MainActor
final class AppModel {
    let library = LibraryModel()

    enum Destination: Hashable {
        case notebook(String)
        /// The selected notebook's sources, in a pane (View › Show Sources, ⌥⌘1). Not a sidebar row.
        case sources
        case studio
        case meetings
        case notes
    }

    var selection: Destination? {
        didSet {
            guard case .notebook(let id)? = selection, library.scopeToNotebook != id else { return }
            library.scopeToNotebook = id
            UserDefaults.standard.set(id, forKey: "lastNotebook")
        }
    }

    /// Paired with a Python Mac and the code confirmed there. Mirrors `MacLink.paired` so views can observe it.
    var paired = MacLink.paired?.verified == true
    /// The pairing sheet (menu: Notebook › Pair with a Mac…, or the empty states).
    var showPairing = false
    /// The inspector on the right edge (View › Show Inspector, ⌥⌘I): the notebook's sources.
    var showInspector = false
    /// A Studio kind the menu asked for (Studio › Briefing…): the Studio pane opens its make sheet.
    var makeRequest: StudioItem.Kind?
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
        guard paired else { return }
        Task { await runSync() }
        await startModels()
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
