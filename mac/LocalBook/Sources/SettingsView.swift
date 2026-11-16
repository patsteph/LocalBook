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
            Tab("Models", systemImage: "cpu") { ModelsSettings() }
            Tab("Library", systemImage: "books.vertical") { SettingsPlaceholder(line: "Re-index, repair, migrate from the previous LocalBook, backups. Phase 3.") }
            Tab("Agents", systemImage: "person.2") { SettingsPlaceholder(line: "Curator, Collector, Correspondent, People, schedules and keys for companions. Phases 4 and 5.") }
            Tab("Security", systemImage: "lock") { SettingsPlaceholder(line: "Encrypted volume, recovery phrase, Touch ID, credential locker. Phase 4.") }
            Tab("Diagnostics", systemImage: "stethoscope") { SettingsPlaceholder(line: "Health, engine truth, parity, rough edges, logs. Phase 4.") }
        }
        .scenePadding()
        .frame(width: 560, height: 400)
    }
}

struct GeneralSettings: View {
    @AppStorage("answerLength") private var answerLength = "standard"

    var body: some View {
        Form {
            Picker("Answer length", selection: $answerLength) {
                ForEach(AnswerLength.allCases) { Text($0.label).tag($0.rawValue) }
            }
            .pickerStyle(.segmented)
            Text("Short ≈ 200 words, Standard ≈ 350, Detailed ≈ 500. The same setting the iPhone app has.")
                .font(.callout).foregroundStyle(.secondary)
        }
        .formStyle(.grouped)
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

/// Phase 2: the two models the Mac runs (search, answers), the reranker and the recipe. Voices, downloads and the
/// hardware profile join as Read-aloud and LLM Studio land.
struct ModelsSettings: View {
    @Environment(AppModel.self) private var model
    @AppStorage("searchUnits") private var searchUnits = "ane"

    private var library: LibraryModel { model.library }

    var body: some View {
        Form {
            Section("Loaded on this Mac") {
                LabeledContent("Search model", value: searchLine)
                LabeledContent("Answer model", value: answerLine)
                if case .failed = library.answerModelState {
                    Button("Load again") { Task { await library.reloadAnswerModel() } }
                }
                Text(library.rerankerLine).font(.callout).foregroundStyle(.secondary)
                Text(library.recipeLine).font(.callout).foregroundStyle(.secondary)
            }
            Section {
                Picker("Search runs on", selection: $searchUnits) {
                    Text("Neural Engine").tag("ane")
                    Text("GPU").tag("gpu")
                }
                Text("Takes effect the next time the app opens.").font(.callout).foregroundStyle(.secondary)
            } header: { Text("Hardware") }
        }
        .formStyle(.grouped)
    }

    private var searchLine: String {
        switch library.searchState {
        case .notReady: "Not loaded"
        case .preparing(let what): "Loading \(what)"
        case .ready: "Ready (EmbeddingGemma 2, Core ML)"
        case .failed(let why): "Failed: \(why)"
        }
    }

    private var answerLine: String {
        switch library.answerModelState {
        case .idle: "Not loaded"
        case .loading: "Loading…"
        case .ready: "Ready (Gemma 4 E4B, MLX)"
        case .failed(let why): "Failed: \(why)"
        }
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
