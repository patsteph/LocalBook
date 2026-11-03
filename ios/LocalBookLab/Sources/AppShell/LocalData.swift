import SwiftUI

/// Settings > Local Data: everything kept on this phone that you made, with a way to see it and clear it.
struct LocalDataView: View {
    let library: LibraryModel
    var body: some View {
        List {
            NavigationLink("My notes") { MyNotesView(library: library) }
            NavigationLink("My audio recordings") { MyRecordingsView(library: library) }
            NavigationLink("My Studio files") { MyStudioFilesView(library: library) }
        }
        .navigationTitle("Local data")
    }
}

private func sizeText(_ bytes: Int) -> String { ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file) }

struct MyRecordingsView: View {
    let library: LibraryModel
    struct Recording: Identifiable {
        let url: URL; let date: Date; let bytes: Int
        let saved: MeetingSession.SavedMeta?; let writtenUp: Bool
        var id: String { url.lastPathComponent }
    }
    struct WriteUp: Identifiable { let url: URL; let notebookId: String; var id: String { url.lastPathComponent } }
    @State private var items: [Recording] = []
    @State private var confirmAll = false
    @State private var confirmSaved = false
    @State private var writing: WriteUp?

    private func load() {
        let keys: [URLResourceKey] = [.contentModificationDateKey, .fileSizeKey]
        let files = (try? FileManager.default.contentsOfDirectory(at: MeetingSession.folder, includingPropertiesForKeys: keys)) ?? []
        items = files.filter { $0.pathExtension == "m4a" }.map { u in
            let v = try? u.resourceValues(forKeys: Set(keys))
            return Recording(url: u, date: v?.contentModificationDate ?? .distantPast, bytes: v?.fileSize ?? 0,
                             saved: MeetingSession.savedMeta(for: u), writtenUp: MeetingSession.hasReview(u))
        }.sorted { $0.date > $1.date }
    }

    private func remove(_ r: Recording) { MeetingSession.removeFiles(for: r.url) }

    private func name(_ r: Recording) -> String {
        if let s = r.saved { return s.title }
        return "Recording " + r.date.formatted(date: .abbreviated, time: .shortened)
    }

    private func detail(_ r: Recording) -> String {
        let when = r.date.formatted(date: .abbreviated, time: .shortened)
        if let s = r.saved { return "Saved in \(s.notebookTitle) · \(when) · \(sizeText(r.bytes))" }
        return (r.writtenUp ? "Written up, not saved yet" : "Not written up yet") + " · \(when) · \(sizeText(r.bytes))"
    }

    private var savedItems: [Recording] { items.filter { $0.saved != nil } }

    var body: some View {
        List {
            if items.isEmpty { Text("No recordings on this phone.").foregroundStyle(.secondary) }
            else {
                Section { ForEach(items) { r in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(name(r))
                            Text(detail(r)).font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        if r.saved == nil {
                            Menu(r.writtenUp ? "Continue" : "Write up") {
                                ForEach(library.notebooks) { nb in Button(nb.title) { writing = WriteUp(url: r.url, notebookId: nb.id) } }
                            }.font(.footnote.weight(.semibold))
                        } else { Image(systemName: "checkmark.circle.fill").foregroundStyle(.green) }
                    }
                    .swipeActions { Button("Delete", role: .destructive) { remove(r); load() } }
                } } footer: {
                    Text("Write up works out who spoke and saves the text into a notebook. Once a recording is saved you can delete the audio and keep the text.")
                }
            }
        }
        .navigationTitle("My audio recordings")
        .toolbar {
            if !items.isEmpty {
                ToolbarItem(placement: .primaryAction) {
                    Menu {
                        if !savedItems.isEmpty { Button("Delete saved recordings (keep the text)", role: .destructive) { confirmSaved = true } }
                        Button("Delete everything", role: .destructive) { confirmAll = true }
                    } label: { Image(systemName: "trash") }
                }
            }
        }
        .confirmationDialog("Delete \(savedItems.count) saved recording\(savedItems.count == 1 ? "" : "s") (\(sizeText(savedItems.reduce(0) { $0 + $1.bytes })))? The notes stay in your notebooks.",
                            isPresented: $confirmSaved, titleVisibility: .visible) {
            Button("Delete audio", role: .destructive) { savedItems.forEach(remove); load() }
        }
        .confirmationDialog("Delete all \(items.count) recordings (\(sizeText(items.reduce(0) { $0 + $1.bytes })))? Recordings not yet saved will be lost.",
                            isPresented: $confirmAll, titleVisibility: .visible) {
            Button("Delete everything", role: .destructive) { items.forEach(remove); load() }
        }
        .sheet(item: $writing, onDismiss: load) { w in MeetingView(library: library, notebookId: w.notebookId, open: w.url) }
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
