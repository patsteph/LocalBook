import SwiftUI

/// Settings > Local Data: everything kept on this phone that you made, with a way to see it and clear it.
struct LocalDataView: View {
    let library: LibraryModel
    var body: some View {
        List {
            NavigationLink("My notes") { MyNotesView(library: library) }
            NavigationLink("My audio recordings") { MyRecordingsView() }
            NavigationLink("My Studio files") { MyStudioFilesView(library: library) }
        }
        .navigationTitle("Local data")
    }
}

private func sizeText(_ bytes: Int) -> String { ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file) }

struct MyRecordingsView: View {
    struct Recording: Identifiable { let url: URL; let date: Date; let bytes: Int; let written: Bool; var id: String { url.lastPathComponent } }
    @State private var items: [Recording] = []
    @State private var confirmAll = false

    private func load() {
        let keys: [URLResourceKey] = [.contentModificationDateKey, .fileSizeKey]
        let files = (try? FileManager.default.contentsOfDirectory(at: MeetingSession.folder, includingPropertiesForKeys: keys)) ?? []
        items = files.filter { $0.pathExtension == "m4a" }.map { u in
            let v = try? u.resourceValues(forKeys: Set(keys))
            let sidecar = u.deletingPathExtension().appendingPathExtension("review.json")
            return Recording(url: u, date: v?.contentModificationDate ?? .distantPast, bytes: v?.fileSize ?? 0,
                             written: FileManager.default.fileExists(atPath: sidecar.path))
        }.sorted { $0.date > $1.date }
    }

    private func remove(_ r: Recording) {
        try? FileManager.default.removeItem(at: r.url)
        try? FileManager.default.removeItem(at: r.url.deletingPathExtension().appendingPathExtension("review.json"))
    }

    var body: some View {
        List {
            if items.isEmpty { Text("No recordings on this phone.").foregroundStyle(.secondary) }
            else {
                Section { ForEach(items) { r in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(r.date.formatted(date: .abbreviated, time: .shortened))
                        Text(sizeText(r.bytes) + (r.written ? " · written up, not saved yet" : "")).font(.caption).foregroundStyle(.secondary)
                    }
                    .swipeActions { Button("Delete", role: .destructive) { remove(r); load() } }
                } } footer: {
                    Text("Saved meetings keep their text in the notebook. Deleting a recording here frees space and does not remove saved notes.")
                }
            }
        }
        .navigationTitle("My audio recordings")
        .toolbar { if !items.isEmpty { ToolbarItem(placement: .primaryAction) { Button("Delete all", role: .destructive) { confirmAll = true } } } }
        .confirmationDialog("Delete all \(items.count) recordings (\(sizeText(items.reduce(0) { $0 + $1.bytes })))?", isPresented: $confirmAll, titleVisibility: .visible) {
            Button("Delete all", role: .destructive) { items.forEach(remove); load() }
        }
        .onAppear(perform: load)
    }
}

struct MyStudioFilesView: View {
    let library: LibraryModel
    @State private var items: [StudioItem] = []

    var body: some View {
        List {
            if items.isEmpty { Text("Nothing made in Studio yet.").foregroundStyle(.secondary) }
            ForEach(items) { i in
                HStack {
                    Image(systemName: i.kind.symbol).foregroundStyle(.secondary).frame(width: 28)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(i.title)
                        Text(i.kind.short + " · " + (library.notebooks.first { $0.id == i.notebookId }?.title ?? "") + " · " + i.created.formatted(date: .abbreviated, time: .omitted))
                            .font(.caption).foregroundStyle(.secondary)
                    }
                }
                .swipeActions { Button("Delete", role: .destructive) { StudioStore.delete(i); items = StudioStore.all(notebook: nil) } }
            }
        }
        .navigationTitle("My Studio files")
        .onAppear { items = StudioStore.all(notebook: nil) }
    }
}
