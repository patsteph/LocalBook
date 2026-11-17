import AppKit
import LocalBookCore
import LocalBookEngines
import SwiftUI

/// Settings, one tab per subject. The tabs are the map of every Settings panel the Python app has, grouped the
/// Mac way; each fills in during the phase named in `01-absorption-map.md`.
struct SettingsView: View {
    var body: some View {
        TabView {
            Tab("General", systemImage: "gear") { GeneralSettings() }
            Tab("Sync", systemImage: "arrow.triangle.2.circlepath") { SyncSettings() }
            Tab("Models", systemImage: "cpu") { LLMStudioView() }
            Tab("Library", systemImage: "books.vertical") { LibrarySettings() }
            Tab("Agents", systemImage: "person.2") { SettingsPlaceholder(line: "Curator, Collector, Correspondent, People, schedules and keys for companions. Phases 4 and 5.") }
            Tab("Security", systemImage: "lock") { SettingsPlaceholder(line: "Encrypted volume, recovery phrase, Touch ID, credential locker. Phase 4.") }
            Tab("Diagnostics", systemImage: "stethoscope") { DiagnosticsSettings() }
        }
        .scenePadding()
        .frame(width: 680, height: 560)
    }
}

struct GeneralSettings: View {
    @AppStorage("answerLength") private var answerLength = "standard"

    var body: some View {
        Form {
            Section("Answers") {
                Picker("Answer length", selection: $answerLength) {
                    ForEach(AnswerLength.allCases) { Text($0.label).tag($0.rawValue) }
                }
                .pickerStyle(.segmented)
                Text("Short ≈ 200 words, Standard ≈ 350, Detailed ≈ 500. The same setting the iPhone app has.")
                    .font(.callout).foregroundStyle(.secondary)
            }
            ReadAloudSettings()
        }
        .formStyle(.grouped)
    }
}

/// The voice and speed Read Aloud uses, with a line to hear it. Shared with the phone through the same defaults keys.
struct ReadAloudSettings: View {
    @AppStorage("readAloudVoice") private var voice = ReadAloudVoice.standard.id
    @AppStorage("readAloudSpeed") private var speed = 1.0
    @State private var reader = ReadAloud()

    var body: some View {
        Section("Read aloud") {
            Picker("Voice", selection: $voice) {
                ForEach(ReadAloudVoice.Accent.allCases, id: \.self) { accent in
                    Section(accent == .american ? "American" : "British") {
                        ForEach(ReadAloudVoice.all.filter { $0.accent == accent }) { v in
                            Text("\(v.name) · \(v.feminine ? "woman" : "man")").tag(v.id)
                        }
                    }
                }
            }
            .onChange(of: voice) { _, _ in if reader.isActive { reader.start(ReadAloudVoice.sampleLine) } }
            HStack {
                Slider(value: $speed, in: 0.7...1.3, step: 0.05) { Text("Speed") }
                Text(speed.formatted(.number.precision(.fractionLength(2))) + "×").monospacedDigit().foregroundStyle(.secondary).frame(width: 44, alignment: .trailing)
            }
            HStack {
                Button(reader.isActive ? "Stop" : "Hear it") { if reader.isActive { reader.stop() } else { reader.start(ReadAloudVoice.sampleLine) } }
                if reader.state == .preparing { ProgressView().controlSize(.small) }
                if case .failed(let why) = reader.state { Text(why).font(.caption).foregroundStyle(.orange) }
            }
            Text("Heart comes with the app; another voice is fetched the first time it speaks, then kept. The iPhone app uses the same voice.")
                .font(.callout).foregroundStyle(.secondary)
        }
        .onDisappear { reader.stop() }
    }
}

/// Phase 2 (peer): the Mac this one is paired with and what has been copied. Phase 4 adds hosting: paired devices,
/// what syncs and what stays local, the phone link.
struct SyncSettings: View {
    @Environment(AppModel.self) private var model
    @State private var confirmForget = false
    @State private var unsent = 0
    @State private var showPairing = false

    private var library: LibraryModel { model.library }

    var body: some View {
        Form {
            Section("Your Mac") {
                if model.paired, let mac = MacLink.paired {
                    LabeledContent("Paired with", value: mac.name)
                    LabeledContent("This Mac", value: PhoneIdentity.deviceName)
                    LabeledContent("Pairing key", value: PhoneIdentity.keyIsHardware ? "Secure Enclave" : "Software")
                } else {
                    Text("Not paired. Pair with the Mac that holds your notebooks to copy them here.").foregroundStyle(.secondary)
                    Button("Pair with a Mac…") { showPairing = true }
                }
            }
            Section {
                HStack {
                    Text("Moves every notebook, every source's text, your notes and Studio items from the LocalBook already on this Mac. The old app must be running; nothing in it is changed.")
                        .font(.callout).foregroundStyle(.secondary)
                    Spacer()
                    Button("Move My Library Here…") { model.showImport = true }
                }
            } header: { Text("From the LocalBook on this Mac") }
            if model.paired {
                Section("On this Mac") {
                    LabeledContent("Notebooks", value: "\(library.notebooks.count)")
                    LabeledContent("Passages", value: "\(library.chunkCount)")
                    HStack {
                        Text(syncLine).font(.callout).foregroundStyle(.secondary)
                        Spacer()
                        Button("Sync Now") { model.syncNow() }.disabled(isSyncing)
                    }
                }
                Section {
                    Button("Remove this Mac and delete the library from this Mac…", role: .destructive) {
                        Task { unsent = await library.unsentNoteCount(); confirmForget = true }
                    }
                } footer: {
                    Text("Questions are searched and answered on this Mac. Nothing about them is sent to the paired Mac or anywhere else. The library is a copy of what that Mac chose to share.")
                        .font(.footnote)
                }
            }
        }
        .formStyle(.grouped)
        .sheet(isPresented: $showPairing) { PairingSheet(model: model) }
        .confirmationDialog("Remove this Mac?", isPresented: $confirmForget, titleVisibility: .visible) {
            Button("Remove and delete library", role: .destructive) { Task { await model.forgetMac() } }
        } message: {
            Text("The copy of your library on this Mac is deleted\(unsent > 0 ? ", including \(unsent) note\(unsent == 1 ? "" : "s") made here that haven't reached the paired Mac yet" : ""). You can pair again any time.")
        }
    }

    private var isSyncing: Bool { if case .syncing = library.syncState { return true } else { return false } }

    private var syncLine: String {
        switch library.syncState {
        case .syncing(let line): line
        case .failed(let why): "Sync failed: \(why)"
        case .idle: library.lastSyncLine
        }
    }
}

/// Settings › Library: what is on this Mac, the folders it watches, and the maintenance commands of the old app.
struct LibrarySettings: View {
    @Environment(AppModel.self) private var model
    @State private var counts = LibraryStore.Counts()
    @State private var folders: [LibraryStore.WatchedFolder] = []
    @State private var busy = false
    @State private var status = ""
    @State private var rewrite = false

    private var library: LibraryModel { model.library }

    var body: some View {
        Form {
            Section("On this Mac") {
                LabeledContent("Notebooks", value: "\(counts.notebooks)")
                LabeledContent("Sources", value: "\(counts.sources)")
                LabeledContent("Passages", value: "\(counts.chunks)")
                LabeledContent("Sources made here", value: "\(counts.localSources)")
                HStack {
                    Text("Data folder").foregroundStyle(.secondary)
                    Spacer()
                    Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([AppFolders.base]) }
                }
            }
            Section {
                if folders.isEmpty {
                    Text("No folders yet. A watched folder is read every five minutes; new and changed files become sources of its notebook. Nothing in the folder is ever written.")
                        .font(.callout).foregroundStyle(.secondary)
                }
                ForEach(folders) { f in
                    HStack(alignment: .firstTextBaseline) {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(URL(filePath: f.path).lastPathComponent).fontWeight(.medium)
                            Text(f.path).font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                            Text("→ \(notebookTitle(f.notebookId))\(f.recursive ? ", with subfolders" : "") · \(lastScan(f))")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("Scan Now") { run { _ = await library.scan(f) { s in status = s } } }.disabled(busy)
                        Button("Stop Watching") { run { await library.unwatchFolder(f.id) } }.disabled(busy)
                    }
                }
                HStack {
                    Spacer()
                    Button("Add Folder…") { NotificationCenter.default.post(name: .watchFolder, object: nil) }
                }
            } header: { Text("Watched folders") }
            Section {
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Re-index sources made here")
                        Text("Cuts and embeds the \(counts.localSources) sources this Mac read itself again, with the current search model.").font(.caption).foregroundStyle(.secondary)
                        Toggle("Also rewrite their summaries and questions (slow)", isOn: $rewrite).font(.caption)
                    }
                    Spacer()
                    Button("Re-index") { run { let r = await library.reindexLocalSources(enrich: rewrite) { s in status = s }; status = done(r) } }
                        .disabled(busy || counts.localSources == 0)
                }
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Repair empty vectors")
                        Text(counts.emptyVectors == 0 ? "Every passage has its vector." : "\(counts.emptyVectors) passages have no vector and cannot be found by meaning.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button("Repair") { run { let r = await library.repairEmptyVectors { s in status = s }; status = done(r) } }
                        .disabled(busy || counts.emptyVectors == 0)
                }
                if busy || !status.isEmpty {
                    HStack(spacing: 8) {
                        if busy { ProgressView().controlSize(.small) }
                        Text(status).font(.callout).foregroundStyle(.secondary)
                    }
                }
            } header: { Text("Maintenance") }
            Section {
                HStack {
                    Text("Moves every notebook, every source's text, your notes and Studio items from the LocalBook already on this Mac. The old app must be running; nothing in it is changed.")
                        .font(.callout).foregroundStyle(.secondary)
                    Spacer()
                    Button("Move My Library Here…") { model.showImport = true }
                }
            } header: { Text("From the LocalBook on this Mac") }
        }
        .formStyle(.grouped)
        .task { await reload() }
        .onReceive(NotificationCenter.default.publisher(for: .librarySynced)) { _ in Task { await reload() } }
    }

    private func reload() async {
        guard let store = library.store else { return }
        counts = await store.counts()
        folders = await store.watchedFolders()
    }

    private func run(_ work: @escaping @MainActor () async -> Void) {
        busy = true
        Task { @MainActor in
            await work()
            busy = false
            await reload()
            NotificationCenter.default.post(name: .librarySynced, object: nil)
        }
    }

    private func done(_ r: LibraryModel.MaintenanceReport) -> String {
        r.failed.isEmpty ? "Done: \(r.done)." : "Done: \(r.done); \(r.failed.count) failed. \(r.failed.first ?? "")"
    }

    private func notebookTitle(_ id: String) -> String { library.notebooks.first { $0.id == id }?.title ?? "a notebook that is gone" }

    private func lastScan(_ f: LibraryStore.WatchedFolder) -> String {
        guard let at = f.lastScan, let date = ISO8601DateFormatter().date(from: at) else { return "not scanned yet" }
        return "last scan \(date.formatted(.relative(presentation: .named))): \(f.lastReport ?? "")"
    }
}

struct SettingsPlaceholder: View {
    let line: String
    var body: some View {
        Form {
            Text(line).foregroundStyle(.secondary)
        }
        .formStyle(.grouped)
    }
}
