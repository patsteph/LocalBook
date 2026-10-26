import SwiftUI

struct SettingsView: View {
    let library: LibraryModel
    let onForget: () -> Void
    @State private var confirmForget = false
    @State private var freed = ""
    @State private var unsent = 0
    @State private var length = AnswerLength.current.rawValue
    @State private var searchUnits = UserDefaults.standard.string(forKey: "searchUnits") ?? "ane"
    @State private var stoppable = UserDefaults.standard.bool(forKey: "stoppablePrefill")

    var body: some View {
        NavigationStack {
            Form {
                Section("Your Mac") {
                    if let mac = MacLink.paired {
                        LabeledContent("Paired with", value: mac.name)
                        LabeledContent("This phone", value: PhoneIdentity.deviceName)
                    }
                    Button("Remove this Mac and delete the library from this phone", role: .destructive) { freed = Self.librarySize(); Task { unsent = await library.unsentNoteCount(); confirmForget = true } }
                }
                Section("On this phone") {
                    LabeledContent("Notebooks", value: "\(library.notebooks.count)")
                    LabeledContent("Passages", value: "\(library.chunkCount)")
                    NavigationLink("My notes") { MyNotesView(library: library) }
                    Text(library.lastSyncLine).font(.footnote).foregroundStyle(.secondary)
                }
                Section {
                    Text("Questions are searched and answered on this phone. Nothing about them is sent to your Mac or anywhere else. The library is a copy of what your Mac chose to share; keep a notebook on one device only, on your Mac, to stop it arriving.")
                        .font(.footnote)
                } header: { Text("Privacy") }
                PictureSearchSection(library: library)
                Section {
                    Picker("Answer length", selection: $length) {
                        ForEach(AnswerLength.allCases) { Text($0.label).tag($0.rawValue) }
                    }
                    .pickerStyle(.segmented)
                    .onChange(of: length) { _, v in UserDefaults.standard.set(v, forKey: "answerLength") }
                } header: { Text("Answers") } footer: {
                    Text("Short ≈ 200 words, Standard ≈ 350, Detailed ≈ 500. Longer answers take longer to write.")
                }
                DisclosureGroup("Advanced") {
                    Toggle("Stop safely when the screen locks", isOn: $stoppable)
                        .onChange(of: stoppable) { _, on in
                            library.useStoppablePrefill = on
                            Task { await library.reloadAnswerModel() }
                        }
                    Picker("Search runs on", selection: $searchUnits) {
                        Text("Neural Engine").tag("ane")
                        Text("GPU").tag("gpu")
                    }
                    .onChange(of: searchUnits) { _, v in UserDefaults.standard.set(v, forKey: "searchUnits") }
                    Text("Takes effect the next time the app opens. Neural Engine is the proven setting; GPU is being timed.")
                        .font(.footnote).foregroundStyle(.secondary)
                    Text("Experimental. Off is the proven setting. On reads each question in small steps so a lock can stop it cleanly; it is still being tested, and changing it reloads the answer model.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Section("About") {
                    Text(library.recipeLine).font(.footnote).foregroundStyle(.secondary)
                    Text(library.rerankerLine).font(.footnote).foregroundStyle(.secondary)
                    if FileManager.default.fileExists(atPath: SoakTrace.url.path) {
                        ShareLink("Share diagnostics (startup and event log)", item: SoakTrace.url)
                    }
                    LabeledContent("Pairing key", value: PhoneIdentity.keyIsHardware ? "Secure Enclave" : "Software (Simulator)")
                    LabeledContent("Version", value: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "—")
                }
            }
            .navigationTitle("Settings")
            .confirmationDialog("Remove this Mac?", isPresented: $confirmForget, titleVisibility: .visible) {
                Button("Remove and delete library", role: .destructive) {
                    Task {
                        await library.wipeLibrary()
                        MacLink.paired = nil
                        onForget()
                    }
                }
            } message: {
                Text("The copy of your library on this phone is deleted\(unsent > 0 ? ", including \(unsent) note\(unsent == 1 ? "" : "s") you made here that haven't reached your Mac yet and will be lost" : "")\(freed.isEmpty ? "" : ", freeing about \(freed)"). You can pair again any time.")
            }
        }
    }

    private static func librarySize() -> String {
        let dir = LibraryStore.defaultURL().deletingLastPathComponent()
        var total: Int64 = 0
        if let files = FileManager.default.enumerator(at: dir, includingPropertiesForKeys: [.fileSizeKey]) {
            for case let url as URL in files { total += Int64((try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0) }
        }
        return total > 0 ? ByteCountFormatter.string(fromByteCount: total, countStyle: .file) : ""
    }
}
