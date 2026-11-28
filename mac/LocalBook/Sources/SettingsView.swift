import AppKit
import Combine
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
            Tab("Agents", systemImage: "person.2") { AgentsSettings() }
            Tab("Security", systemImage: "lock") { SecuritySettings() }
            Tab("Diagnostics", systemImage: "stethoscope") { DiagnosticsSettings() }
        }
        .scenePadding()
        .frame(width: 680, height: 560)
    }
}

struct GeneralSettings: View {
    @AppStorage("answerLength") private var answerLength = "standard"
    @AppStorage(VoiceModifier.defaultsKey) private var voiceModifier = true

    var body: some View {
        Form {
            Section("Answers") {
                Picker("Answer length", selection: $answerLength) {
                    ForEach(AnswerLength.allCases) { Text($0.label).tag($0.rawValue) }
                }
                .pickerStyle(.segmented)
                Text("Short ≈ 200 words, Standard ≈ 350, Detailed ≈ 500. The same setting the iPhone app has.")
                    .font(.callout).foregroundStyle(.secondary)
                Toggle("Keep the tone even across models", isOn: $voiceModifier)
                Text("Puts a two-sentence note about tone in front of every answer's instructions, fitted to the model's family (Gemma tends to hedge, Phi to over-format). Tone only; the notebook rules stay the same.")
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

/// Settings › Sync. Two directions over one link: this Mac as a copy of another Mac's library (the old app's or a
/// Swift one: `MacLink`, the phone's side of the link), and this Mac as the host other devices copy from
/// (`PhoneHost`: the iPhone and other Macs). Mac-to-Mac sync is the second Mac pairing like a phone; which Mac runs
/// the agents' schedules is `SyncRole`.
struct SyncSettings: View {
    @Environment(AppModel.self) private var model
    @State private var confirmForget = false
    @State private var unsent = 0
    @State private var showPairing = false

    private var library: LibraryModel { model.library }
    @State private var pending = 0

    var body: some View {
        Form {
            Section {
                if model.paired, let mac = MacLink.paired {
                    LabeledContent("Paired with", value: mac.name)
                    LabeledContent("This Mac", value: PhoneIdentity.deviceName)
                    LabeledContent("Pairing key", value: PhoneIdentity.keyIsHardware ? "Secure Enclave" : "Software")
                } else {
                    Text("Not paired: this Mac holds its own library, and the devices below copy from it. To make this Mac a copy of another Mac's library instead, pair with that Mac.").foregroundStyle(.secondary)
                    Button("Pair with a Mac…") { showPairing = true }
                }
            } header: { Text("The Mac that holds your library") } footer: {
                Text("A second Mac pairs with the first the way the iPhone does (that Mac: Let a device pair with this one), copies the notebooks it is given over your own network, and sends its notes, Studio items and edits back. Only what changed moves, in either direction.")
                    .font(.footnote)
            }
            Section {
                HStack {
                    Text("Moves every notebook, every source's text, your notes and Studio items from the LocalBook already on this Mac. The old app must be running; nothing in it is changed.")
                        .font(.callout).foregroundStyle(.secondary)
                    Spacer()
                    Button("Move My Library Here…") { model.showImport = true }
                }
            } header: { Text("From the LocalBook on this Mac") }
            PhoneHostSections()
            Section {
                if model.paired {
                    Toggle("Run the agents' schedules on this Mac too", isOn: Binding(get: { model.companionRunsSchedules }, set: { model.companionRunsSchedules = $0 }))
                }
                Text(SyncRole.line(role: model.syncRole, companionOptedIn: model.companionRunsSchedules)).font(.callout).foregroundStyle(.secondary)
            } header: { Text("Agent schedules") }
            if model.paired {
                Section("On this Mac") {
                    LabeledContent("Notebooks", value: "\(library.notebooks.count)")
                    LabeledContent("Passages", value: "\(library.chunkCount)")
                    HStack {
                        Text(syncLine).font(.callout).foregroundStyle(.secondary)
                        Spacer()
                        Button("Sync Now") { model.syncNow() }.disabled(isSyncing)
                    }
                    if pending > 0 {
                        Text("\(pending) change\(pending == 1 ? "" : "s") made here \(pending == 1 ? "is" : "are") waiting for your paired Mac; \(pending == 1 ? "it goes" : "they go") over the next time it can be reached.")
                            .font(.callout).foregroundStyle(.secondary)
                    }
                    if let problem = library.editProblem { Text(problem).font(.callout).foregroundStyle(.orange) }
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
        .task(id: library.syncState) { pending = await library.pendingEditCount() }
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
/// This Mac as the host of the phone link: pair an iPhone, see the devices paired, choose which notebooks they get.
struct PhoneHostSections: View {
    @Environment(AppModel.self) private var model
    @State private var now = Date()
    @State private var forgetting: PairedPhone?
    private let clock = Timer.publish(every: 1, on: .main, in: .common).autoconnect()

    private var host: PhoneHostController { model.phoneHost }

    var body: some View {
        Group {
        Section {
            HStack(spacing: 8) {
                Circle().fill(statusColor).frame(width: 8, height: 8)
                Text(statusLine).font(.callout).foregroundStyle(.secondary)
                Spacer()
                if host.pairingOpen {
                    Button("Close") { Task { await host.closePairing() } }
                } else {
                    Button("Let a device pair with this one") { Task { await host.openPairing() } }
                }
            }
            if host.pairingOpen, let until = host.pairingOpenUntil {
                let left = max(0, Int(until.timeIntervalSince(now)))
                Text("Open for \(left / 60):\(String(format: "%02d", left % 60)). On the iPhone: Find my Mac; on another Mac: Settings › Sync › Pair with a Mac…; then choose “\(PhoneIdentity.deviceName)”."
                     + (host.addresses.isEmpty ? "" : " Its address, if the phone does not find it: \(host.addresses.joined(separator: ", "))."))
                    .font(.footnote).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            ForEach(host.requests) { request in
                HStack {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("\(request.name) shows this code?").font(.callout)
                        Text(request.code).font(.system(size: 28, weight: .bold, design: .monospaced))
                    }
                    Spacer()
                    Button("Not mine") { Task { await host.reject(request) } }
                    Button("Codes match") { Task { await host.confirm(request) } }.buttonStyle(.borderedProminent)
                }
            }
            if let problem = host.problem { Text(problem).font(.callout).foregroundStyle(.orange).fixedSize(horizontal: false, vertical: true) }
            ForEach(host.phones) { phone in
                HStack {
                    Image(systemName: phone.deviceId.hasPrefix("mac-") ? "laptopcomputer" : "iphone")
                    Text(phone.name)
                    Spacer()
                    Text(lastSeen(phone)).font(.footnote).foregroundStyle(.secondary)
                    Button("Forget") { forgetting = phone }
                }
            }
        } header: { Text("Devices paired with this Mac") } footer: {
            Text("Paired devices (iPhones and other Macs) copy the notebooks below over your own network and send their notes, Studio items and edits back here. Nothing goes to the cloud.")
                .font(.footnote)
        }
        if !model.library.notebooks.isEmpty {
            Section {
                ForEach(model.library.notebooks) { nb in
                    Toggle(nb.title, isOn: Binding(get: { !host.keptLocal.contains(nb.id) },
                                                   set: { on in Task { await host.setSynced(nb.id, on) } }))
                }
            } header: { Text("Synced to paired devices") } footer: {
                Text("Off: kept on this Mac only.").font(.footnote)
            }
        }
        }
        .onReceive(clock) { now = $0 }
        .task { await host.refresh() }
        .alert("Forget \(forgetting?.name ?? "this device")?", isPresented: Binding(get: { forgetting != nil }, set: { if !$0 { forgetting = nil } })) {
            Button("Forget", role: .destructive) { if let p = forgetting { Task { await host.forget(p) } }; forgetting = nil }
            Button("Cancel", role: .cancel) { forgetting = nil }
        } message: { Text("It can no longer sync with this Mac until it pairs again. Its own copy of the library stays on it.") }
    }

    private var statusColor: Color {
        switch host.status { case .listening: .green; case .off: .gray; case .failed: .orange }
    }

    private var statusLine: String {
        switch host.status {
        case .listening: host.phones.isEmpty ? "Ready for your iPhone or another Mac to pair" : "Listening for \(host.phones.count) paired device\(host.phones.count == 1 ? "" : "s")"
        case .off: "Not listening"
        case .failed(let why): "Phone link off: \(why)"
        }
    }

    private func lastSeen(_ phone: PairedPhone) -> String {
        guard let at = phone.lastSeen else { return "Paired \(phone.pairedAt.formatted(date: .abbreviated, time: .omitted))" }
        let f = RelativeDateTimeFormatter(); f.unitsStyle = .short
        return "Seen \(f.localizedString(for: at, relativeTo: Date()))"
    }
}

struct LibrarySettings: View {
    @Environment(AppModel.self) private var model
    @AppStorage(StudioEngine.criticKey) private var visualCritic = false
    @AppStorage(AppModel.imageModelKey) private var imageModel = AppModel.imageModelChoice
    @AppStorage(StudioEngine.pictureQualityKey) private var pictureQuality = "best"
    @State private var counts = LibraryStore.Counts()
    @State private var folders: [LibraryStore.WatchedFolder] = []
    @State private var busy = false
    @State private var status = ""
    @State private var rewrite = false

    private var library: LibraryModel { model.library }

    /// The chosen image model, if one is.
    private var chosenImagery: (any VisualImagery)? { model.imagery }

    /// What Settings says about the chosen image model.
    private var imageryLine: String {
        switch chosenImagery?.state ?? .absent {
        case .absent: "The weights (about \(imageModel == "klein" ? 6 : 10) GB) download after the answer model is ready."
        case .downloading(let p, let part): "Downloading, \(Int(p * 100)) % (\(part))."
        case .ready: "Ready. Value propositions and infographics get a painted backdrop under their title; the first takes a minute or two while the model loads."
        case .failed(let why): "The download failed (\(why))."
        }
    }

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
                Toggle("Have the answer model look over each visual", isOn: $visualCritic)
                Text(library.answerModelSeesImages
                     ? "After a visual is drawn, the model looks at the picture and scores it; one below the bar is filled once more with its note. Adds a few seconds per visual."
                     : library.gemma == nil
                     ? "Waiting for the answer model to finish loading."
                     : "The answer model loaded without its vision tower, so it cannot look at pictures. " + (AnswerModels.visionProblem.map { "The loader said: \($0)" } ?? "Choose a model with vision, such as Gemma 4."))
                    .font(.callout).foregroundStyle(.secondary)
                Picker("Image model for backdrops", selection: $imageModel) {
                    Text("FLUX.1 schnell").tag("schnell")
                    Text("FLUX.2 Klein 4B").tag("klein")
                    Text("None").tag("none")
                }
                .onChange(of: imageModel) { _, _ in model.applyImageModel() }
                if let chosen = chosenImagery {
                    Text(imageModel == "klein" ? KleinImagery.note : SchnellImagery.note).font(.callout).foregroundStyle(.secondary)
                    HStack(spacing: 8) {
                        if case .downloading = chosen.state { ProgressView().controlSize(.small) }
                        Text(imageryLine).font(.callout).foregroundStyle(.secondary)
                        Spacer()
                        if case .failed = chosen.state { Button("Try again") { Task { await chosen.prepare() } } }
                    }
                    Picker("Picture quality", selection: $pictureQuality) {
                        Text("Best: two passes").tag("best")
                        Text("Fast: one pass").tag("fast")
                    }
                    Text("Best paints the backdrop, then paints it again from itself for finer detail; about twice the time. Either way the answer model writes the art direction first, and the diagram's colours follow the picture.")
                        .font(.callout).foregroundStyle(.secondary)
                } else {
                    Text("No image model: these forms draw a coloured band instead. Nothing is downloaded.").font(.callout).foregroundStyle(.secondary)
                }
                if imageModel != "klein", KleinImagery.anythingOnDisk {
                    HStack { Spacer(); Button("Remove Klein weights") { model.klein.deleteWeights() } }
                }
                if imageModel != "schnell", SchnellImagery.anythingOnDisk {
                    HStack { Spacer(); Button("Remove schnell weights") { model.schnell.deleteWeights() } }
                }
            } header: { Text("Studio visuals") }
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
