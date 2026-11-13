import Foundation
import Observation
import LocalBookCore

/// What the window shows. Phase 0 holds the shape only; Phase 2 replaces the stored lists with the package's
/// `LibraryModel` (notebooks, sources, sync state) and the engines.
@Observable @MainActor
final class AppModel {
    /// A notebook as the sidebar lists it, in the desktop app's order.
    struct Notebook: Identifiable, Hashable {
        let id: String
        var name: String
        var section: String
        var colorHex: String?
    }

    enum Destination: Hashable {
        case notebook(String)
        case studio
        case meetings
        case notes
    }

    var notebooks: [Notebook] = []
    var selection: Destination?
    /// One calm line about readiness: "Not paired", "Getting ready", "Ready".
    var readiness = "Not paired with a Mac yet"
    var isPaired = false

    var selectedNotebook: Notebook? {
        guard case .notebook(let id)? = selection else { return nil }
        return notebooks.first { $0.id == id }
    }

    struct NotebookSection: Identifiable {
        var name: String
        var notebooks: [Notebook]
        var id: String { name }
    }

    /// Notebooks grouped by the desktop app's sections, in its order.
    var sections: [NotebookSection] {
        var order: [String] = []
        var grouped: [String: [Notebook]] = [:]
        for n in notebooks {
            if grouped[n.section] == nil { order.append(n.section) }
            grouped[n.section, default: []].append(n)
        }
        return order.map { NotebookSection(name: $0, notebooks: grouped[$0] ?? []) }
    }

    init() {
        _ = try? AppFolders.ensure(AppFolders.base)
    }
}
