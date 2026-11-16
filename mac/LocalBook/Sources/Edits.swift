import LocalBookCore
import LocalBookEngines
import SwiftUI

/// The prompts the sidebar's menus open (rename, new, delete, a problem). Context menus cannot present a sheet or an
/// alert themselves, so they set a field here and `EditPromptsHost`, once at the window root, shows the right one.
@MainActor @Observable
final class EditPrompts {
    var renameSource: LibraryStore.SourceInfo?
    var deleteSource: LibraryStore.SourceInfo?
    var newNotebook = false
    var renameNotebook: LibraryNotebookInfo?
    var deleteNotebook: LibraryNotebookInfo?
    var newSection = false
    var renameSection: LibraryModel.Section?
    var deleteSection: LibraryModel.Section?
    /// The text field of whichever prompt is open.
    var text = ""
    var problem: String?
    var busy = false
}

/// The desktop's notebook palette.
enum NotebookColors {
    static let all: [(name: String, hex: String)] = [
        ("Red", "#EF4444"), ("Orange", "#F97316"), ("Yellow", "#EAB308"), ("Green", "#22C55E"), ("Teal", "#14B8A6"),
        ("Blue", "#3B82F6"), ("Indigo", "#6366F1"), ("Purple", "#8B5CF6"), ("Pink", "#EC4899"), ("Gray", "#6B7280"),
    ]
}

/// Every action on a source, in one menu used by the sidebar, the Sources pane and the inspector.
struct SourceContextMenu: View {
    let source: LibraryStore.SourceInfo
    @Environment(AppModel.self) private var model

    var body: some View {
        SourceOpenButton(key: SourceWindowKey(notebookId: source.notebookId, sourceId: source.id, name: source.name))
        Button("Rename…") { model.prompts.text = source.name; model.prompts.renameSource = source }
        Menu("Move to") {
            ForEach(model.library.notebooks.filter { $0.id != source.notebookId }) { nb in
                Button(nb.title) { model.edit { await model.library.moveSource(source, to: nb.id) } }
            }
        }
        .disabled(!model.paired)
        Divider()
        Button("Delete…", role: .destructive) { model.prompts.deleteSource = source }
    }
}

/// Every action on a notebook: the desktop sidebar's menu, through the paired Mac.
struct NotebookContextMenu: View {
    let notebook: LibraryNotebookInfo
    @Environment(AppModel.self) private var model

    var body: some View {
        Button("Ask") { model.selection = .notebook(notebook.id) }
        Button("Show Sources") { model.selection = .notebook(notebook.id); model.selection = .sources }
        Divider()
        Button("New Notebook…") { model.prompts.text = ""; model.prompts.newNotebook = true }
        Button("Rename…") { model.prompts.text = notebook.title; model.prompts.renameNotebook = notebook }
        Menu("Color") {
            ForEach(NotebookColors.all, id: \.hex) { c in
                Button {
                    model.edit { await model.library.editNotebooks("color", ["notebook_id": notebook.id, "color": c.hex]) }
                } label: {
                    Label { Text(c.name) } icon: { Image(systemName: notebook.color?.uppercased() == c.hex ? "circle.fill" : "circle").foregroundStyle(Color(hex: c.hex) ?? .primary) }
                }
            }
        }
        Menu("Move to Section") {
            Button("None") { model.edit { await model.library.editNotebooks("section", ["notebook_id": notebook.id, "section_id": NSNull()]) } }
                .disabled(notebook.section == nil)
            ForEach(model.macSections) { s in
                Button(s.name) { model.edit { await model.library.editNotebooks("section", ["notebook_id": notebook.id, "section_id": s.id]) } }
                    .disabled(notebook.section == s.name)
            }
            Divider()
            Button("New Section…") { model.prompts.text = ""; model.prompts.newSection = true }
        }
        Divider()
        Button("Delete Notebook…", role: .destructive) { model.prompts.deleteNotebook = notebook }
    }
}

/// Shows whichever prompt is set, once, at the window root.
struct EditPromptsHost: ViewModifier {
    @Environment(AppModel.self) private var model

    func body(content: Content) -> some View {
        @Bindable var prompts = model.prompts
        let library = model.library
        content
            .alert("Rename Source", isPresented: isSet($prompts.renameSource)) {
                TextField("Name", text: $prompts.text)
                Button("Save") { if let s = prompts.renameSource { let t = prompts.text; model.edit { await library.renameSource(s, to: t) } } }
                Button("Cancel", role: .cancel) {}
            }
            .confirmationDialog("Delete “\(prompts.deleteSource?.name ?? "")”?", isPresented: isSet($prompts.deleteSource), titleVisibility: .visible) {
                Button("Delete", role: .destructive) { if let s = prompts.deleteSource { model.edit { await library.deleteSource(s) } } }
            } message: { Text("It is removed from the paired Mac too. This cannot be undone.") }
            .alert("New Notebook", isPresented: $prompts.newNotebook) {
                TextField("Title", text: $prompts.text)
                Button("Create") {
                    let t = prompts.text
                    model.edit {
                        var made: String?
                        let problem = await library.editNotebooks("create", ["title": t]) { made = $0["id"] as? String }
                        if let made { model.selection = .notebook(made) }
                        return problem
                    }
                }
                Button("Cancel", role: .cancel) {}
            } message: { Text("Made on the paired Mac and synced here. Add sources to it there, or drop them on it here.") }
            .alert("Rename Notebook", isPresented: isSet($prompts.renameNotebook)) {
                TextField("Title", text: $prompts.text)
                Button("Save") { if let nb = prompts.renameNotebook { let t = prompts.text; model.edit { await library.editNotebooks("rename", ["notebook_id": nb.id, "title": t]) } } }
                Button("Cancel", role: .cancel) {}
            }
            .confirmationDialog("Delete “\(prompts.deleteNotebook?.title ?? "")” and everything in it?", isPresented: isSet($prompts.deleteNotebook), titleVisibility: .visible) {
                Button("Delete Notebook", role: .destructive) {
                    if let nb = prompts.deleteNotebook {
                        model.edit {
                            let problem = await library.editNotebooks("delete", ["notebook_id": nb.id])
                            if problem == nil, model.currentNotebookId == nb.id { model.selection = library.notebooks.first.map { .notebook($0.id) } }
                            return problem
                        }
                    }
                }
            } message: { Text("Its sources, notes and Studio items are deleted on the paired Mac and here. This cannot be undone.") }
            .alert("New Section", isPresented: $prompts.newSection) {
                TextField("Name", text: $prompts.text)
                Button("Create") { let t = prompts.text; model.edit { await library.editNotebooks("section_create", ["name": t]) } }
                Button("Cancel", role: .cancel) {}
            }
            .alert("Rename Section", isPresented: isSet($prompts.renameSection)) {
                TextField("Name", text: $prompts.text)
                Button("Save") { if let s = prompts.renameSection { let t = prompts.text; model.edit { await library.editNotebooks("section_rename", ["section_id": s.id, "name": t]) } } }
                Button("Cancel", role: .cancel) {}
            }
            .confirmationDialog("Delete the section “\(prompts.deleteSection?.name ?? "")”?", isPresented: isSet($prompts.deleteSection), titleVisibility: .visible) {
                Button("Delete Section", role: .destructive) { if let s = prompts.deleteSection { model.edit { await library.editNotebooks("section_delete", ["section_id": s.id]) } } }
            } message: { Text("Its notebooks are kept; they move out of any section.") }
            .alert("Something went wrong", isPresented: isSet($prompts.problem)) {
                Button("OK", role: .cancel) {}
            } message: { Text(prompts.problem ?? "") }
    }

    private func isSet<T>(_ value: Binding<T?>) -> Binding<Bool> {
        Binding(get: { value.wrappedValue != nil }, set: { if !$0 { value.wrappedValue = nil } })
    }
}
