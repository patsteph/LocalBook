import SwiftUI

/// Notebooks grouped the way the desktop app groups them: those in no section first, then each section.
struct NotebookGroups {
    struct Group: Identifiable { let id: String; let section: String?; let items: [LibraryNotebookInfo] }
    let groups: [Group]

    init(_ notebooks: [LibraryNotebookInfo]) {
        var out: [Group] = []
        for n in notebooks {                                   // already in the desktop's order
            if let i = out.indices.last, out[i].section == n.section {
                out[i] = Group(id: out[i].id, section: n.section, items: out[i].items + [n])
            } else {
                out.append(Group(id: "\(out.count)-\(n.section ?? "")", section: n.section, items: [n]))
            }
        }
        groups = out
    }
}

struct NotebookPicker: View {
    let library: LibraryModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                ForEach(NotebookGroups(library.notebooks).groups) { group in
                    Section(group.section ?? "Notebooks") {
                        ForEach(group.items) { n in
                            Button {
                                library.scopeToNotebook = n.id
                                UserDefaults.standard.set(n.id, forKey: "lastNotebook")
                                dismiss()
                            } label: {
                                HStack {
                                    Text(n.title).foregroundStyle(.primary)
                                    Spacer()
                                    if n.id == library.scopeToNotebook { Image(systemName: "checkmark").foregroundStyle(.tint) }
                                }
                            }
                        }
                    }
                }
            }
            .navigationTitle("Ask which notebook?")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
        }
        .presentationDetents([.medium, .large])
    }
}
