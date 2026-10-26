import SwiftUI

@main
struct LocalBookLabApp: App {
    init() {
        // Start watching foreground/background before any test runs.
        _ = AppStateTracker.shared
        PhoneIdentity.migrateIfNeeded()
    }

    var body: some Scene {
        WindowGroup {
            LabHomeView()
        }
    }
}

/// One row per Phase 0 experiment. Each test answers a question that could still change
/// the architecture; the pass bars are fixed before the numbers exist.
struct LabHomeView: View {
    var body: some View {
        NavigationStack {
            List {
                Section {
                    NavigationLink {
                        ParityTestView()
                    } label: {
                        LabRow(number: 1,
                               title: "Embedding parity",
                               detail: "iPhone Neural Engine vs Mac MLX, same chunks, same questions")
                    }
                    NavigationLink {
                        GemmaTestView()
                    } label: {
                        LabRow(number: 2, title: "Gemma 4 E4B on device",
                               detail: "Memory, speed, time to first word — the Mac's own model")
                    }
                    NavigationLink {
                        FindMacsView()
                    } label: {
                        LabRow(number: 3, title: "Pairing with a Mac",
                               detail: "Step 1: find Macs over Bonjour. Pinned TLS pairing comes next")
                    }
                    NavigationLink {
                        LibraryView()
                    } label: {
                        LabRow(number: 5, title: "Library on the phone",
                               detail: "Phase 1: sync the Mac's chunks and vectors, then search them here")
                    }
                    NavigationLink {
                        SpeechTestView()
                    } label: {
                        LabRow(number: 4, title: "Speech behind a locked screen",
                               detail: "Parakeet and Kokoro: read aloud, transcribe, and keep going when locked")
                    }
                } header: {
                    Text("Phase 0")
                } footer: {
                    Text("Each test ends with a report you can share back to the Mac.")
                }
            }
            .navigationTitle("LocalBook Lab")
        }
    }
}

private struct LabRow: View {
    let number: Int
    let title: String
    let detail: String
    var pending = false

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text("\(number)")
                .font(.system(.body, design: .rounded).weight(.semibold))
                .foregroundStyle(pending ? .secondary : .primary)
                .frame(width: 20)
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.body.weight(.medium))
                Text(pending ? "Next" : detail)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 4)
        .opacity(pending ? 0.6 : 1)
    }
}
