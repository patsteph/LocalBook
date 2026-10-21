import SwiftUI

struct SettingsView: View {
    let library: LibraryModel
    let onForget: () -> Void
    @State private var confirmForget = false
    @State private var stoppable = UserDefaults.standard.bool(forKey: "stoppablePrefill")

    var body: some View {
        NavigationStack {
            Form {
                Section("Your Mac") {
                    if let mac = MacLink.paired {
                        LabeledContent("Paired with", value: mac.name)
                        LabeledContent("This phone", value: PhoneIdentity.deviceName)
                    }
                    Button("Remove this Mac and delete the library from this phone", role: .destructive) { confirmForget = true }
                }
                Section("On this phone") {
                    LabeledContent("Notebooks", value: "\(library.notebooks.count)")
                    LabeledContent("Passages", value: "\(library.chunkCount)")
                    Text(library.lastSyncLine).font(.footnote).foregroundStyle(.secondary)
                }
                Section {
                    Text("Questions are searched and answered on this phone. Nothing about them is sent to your Mac or anywhere else. The library is a copy of what your Mac chose to share; keep a notebook on one device only, on your Mac, to stop it arriving.")
                        .font(.footnote)
                } header: { Text("Privacy") }
                Section {
                    Toggle("Stop safely when the screen locks", isOn: $stoppable)
                        .onChange(of: stoppable) { _, on in
                            library.useStoppablePrefill = on
                            Task { await library.reloadAnswerModel() }
                        }
                } header: { Text("Experimental") } footer: {
                    Text("Off is the proven setting. On reads each question in small steps so a lock can stop it cleanly; it is still being tested, and changing it reloads the answer model.")
                }
                Section("About") {
                    Text(library.recipeLine).font(.footnote).foregroundStyle(.secondary)
                    Text(library.rerankerLine).font(.footnote).foregroundStyle(.secondary)
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
                Text("The copy of your library on this phone is deleted. You can pair again any time.")
            }
        }
    }
}
