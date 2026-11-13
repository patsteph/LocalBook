import SwiftUI

/// Settings, one tab per subject. The tabs are the map of every Settings panel the Python app has, grouped the
/// Mac way; each fills in during the phase named in `01-absorption-map.md`.
struct SettingsView: View {
    var body: some View {
        TabView {
            Tab("General", systemImage: "gear") { GeneralSettings() }
            Tab("Sync", systemImage: "arrow.triangle.2.circlepath") { SettingsPlaceholder(line: "Paired devices, what syncs and what stays local, the phone link. Phase 2 (peer) and Phase 4 (host).") }
            Tab("Models", systemImage: "cpu") { SettingsPlaceholder(line: "The answer model, the search model, voices, downloads and the hardware profile. Phase 2.") }
            Tab("Library", systemImage: "books.vertical") { SettingsPlaceholder(line: "Re-index, repair, migrate from the previous LocalBook, backups. Phase 3.") }
            Tab("Agents", systemImage: "person.2") { SettingsPlaceholder(line: "Curator, Collector, Correspondent, People, schedules and keys for companions. Phases 4 and 5.") }
            Tab("Security", systemImage: "lock") { SettingsPlaceholder(line: "Encrypted volume, recovery phrase, Touch ID, credential locker. Phase 4.") }
            Tab("Diagnostics", systemImage: "stethoscope") { SettingsPlaceholder(line: "Health, engine truth, parity, rough edges, logs. Phase 4.") }
        }
        .scenePadding()
        .frame(width: 560, height: 360)
    }
}

struct GeneralSettings: View {
    @AppStorage("answerLength") private var answerLength = "standard"

    var body: some View {
        Form {
            Picker("Answer length", selection: $answerLength) {
                Text("Short").tag("short")
                Text("Standard").tag("standard")
                Text("Detailed").tag("detailed")
            }
            .pickerStyle(.segmented)
            Text("How much the answer model writes. The same setting the iPhone app has.")
                .font(.callout).foregroundStyle(.secondary)
        }
        .formStyle(.grouped)
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
