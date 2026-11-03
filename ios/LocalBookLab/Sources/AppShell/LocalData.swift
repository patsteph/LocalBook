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
    @State private var linking: Recording?

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
                                Menu("Write up in…") {
                                    ForEach(library.notebooks) { nb in Button(nb.title) { writing = WriteUp(url: r.url, notebookId: nb.id) } }
                                }
                                Button("It's already saved…", systemImage: "link") { linking = r }
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
        .sheet(item: $linking, onDismiss: load) { r in LinkRecordingSheet(library: library, recording: r.url, date: r.date) }
        .onAppear(perform: load)
        .task { load(); await linkOlderRecordings(); load() }
    }

    private static func parse(_ s: String) -> Date? {
        let a = ISO8601DateFormatter(); a.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let b = ISO8601DateFormatter()
        if let d = a.date(from: s) ?? b.date(from: s) { return d }
        let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.timeZone = .current
        for fmt in ["yyyy-MM-dd'T'HH:mm:ss.SSSSSS", "yyyy-MM-dd'T'HH:mm:ss", "yyyy-MM-dd HH:mm:ss"] { f.dateFormat = fmt; if let d = f.date(from: s) { return d } }
        return nil
    }

    /// Recordings saved before the phone kept track of where they went: match each one to the meeting note that was
    /// saved from it (a note that opens with the meeting header, added soon after the recording ended). Done once, then remembered.
    private func linkOlderRecordings() async {
        let loose = items.filter { $0.saved == nil && !$0.writtenUp }
        guard !loose.isEmpty else { return }
        struct Pair { let rec: Recording; let nb: String; let nbTitle: String; let src: LibraryStore.SourceInfo; let delta: TimeInterval }
        var pairs: [Pair] = []
        for nb in library.notebooks {
            for src in await library.sources(in: nb.id) where src.type != "image" {
                guard let when = StudioPull.parseDate(src.created) else { continue }
                for r in loose where abs(when.timeIntervalSince(r.date)) <= 72 * 3600 {
                    pairs.append(Pair(rec: r, nb: nb.id, nbTitle: nb.title, src: src, delta: abs(when.timeIntervalSince(r.date))))
                }
            }
        }
        var claimedRecordings = Set<String>(), claimedSources = Set<String>()
        var isMeeting: [String: Bool] = [:]
        for p in pairs.sorted(by: { $0.delta < $1.delta }) {
            let key = p.nb + "/" + p.src.id
            guard !claimedRecordings.contains(p.rec.id), !claimedSources.contains(key) else { continue }
            if isMeeting[key] == nil {
                let text = await library.sourceText(notebook: p.nb, sourceId: p.src.id)
                isMeeting[key] = LibraryModel.looksLikeMeeting(text)
            }
            guard isMeeting[key] == true else { continue }
            claimedRecordings.insert(p.rec.id); claimedSources.insert(key)
            let e = JSONEncoder(); e.dateEncodingStrategy = .iso8601
            if let data = try? e.encode(MeetingSession.SavedMeta(title: p.src.name, notebookId: p.nb, notebookTitle: p.nbTitle, savedAt: p.rec.date)) {
                try? data.write(to: MeetingSession.savedMetaURL(for: p.rec.url), options: .atomic)
            }
        }
    }
}

/// Pick the note a recording was saved as, so the two are tied together and it isn't written up twice.
struct LinkRecordingSheet: View {
    let library: LibraryModel
    let recording: URL
    let date: Date
    @Environment(\.dismiss) private var dismiss
    @State private var notebookId = ""
    @State private var sources: [LibraryStore.SourceInfo] = []

    private func load() async {
        let all = await library.sources(in: notebookId)
        sources = all.sorted {
            abs((StudioPull.parseDate($0.created) ?? .distantPast).timeIntervalSince(date)) < abs((StudioPull.parseDate($1.created) ?? .distantPast).timeIntervalSince(date))
        }
    }

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Picker("Notebook", selection: $notebookId) { ForEach(library.notebooks) { Text($0.title).tag($0.id) } }
                } footer: { Text("Closest in time to the recording first.") }
                Section {
                    ForEach(sources) { s in
                        Button {
                            let e = JSONEncoder(); e.dateEncodingStrategy = .iso8601
                            let title = library.notebooks.first { $0.id == notebookId }?.title ?? ""
                            if let data = try? e.encode(MeetingSession.SavedMeta(title: s.name, notebookId: notebookId, notebookTitle: title, savedAt: date)) {
                                try? data.write(to: MeetingSession.savedMetaURL(for: recording), options: .atomic)
                            }
                            dismiss()
                        } label: {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(s.name).foregroundStyle(.primary).lineLimit(2)
                                if let d = StudioPull.parseDate(s.created) { Text(d.formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundStyle(.secondary) }
                            }
                        }
                    }
                }
            }
            .navigationTitle("Which note is it?").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dismiss() } } }
            .task { notebookId = library.notebooks.first?.id ?? ""; await load() }
            .onChange(of: notebookId) { _, _ in Task { await load() } }
        }
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
