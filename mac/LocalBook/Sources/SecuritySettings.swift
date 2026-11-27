import AppKit
import UniformTypeIdentifiers
import LocalBookCore
import SwiftUI

/// Settings › Security: backups, the recovery phrase, and restoring. Touch ID, the credential locker and the
/// encrypted volume are later rows of the gap list.
struct SecuritySettings: View {
    @Environment(AppModel.self) private var model
    @State private var phraseEntry = ""
    @State private var showExistingPhrase = false
    @State private var restoreTarget: URL?
    @State private var restoreState: RestoreState = .idle
    @State private var restorePhrase = ""

    enum RestoreState: Equatable { case idle, checking, needsPhrase, ready(String), failed(String), staging }

    var body: some View {
        @Bindable var backup = model.backup
        Form {
            if let line = backup.restoredLine {
                Section {
                    HStack {
                        Label(line, systemImage: "checkmark.seal").foregroundStyle(.secondary)
                        Spacer()
                        Button("OK") { backup.dismissRestoredLine() }
                    }
                }
            }
            if backup.restorePending {
                Section("Restore waiting") {
                    Text("A backup is ready to go in. Quit LocalBook; the restore finishes when you open it again. What it replaces is kept in a Restore-replaced folder beside the library.")
                        .font(.callout)
                    HStack {
                        Button("Quit now") { backup.quitToRestore() }.buttonStyle(.borderedProminent)
                        Button("Cancel the restore") { backup.cancelRestore() }
                    }
                }
            }

            Section("Backups") {
                LabeledContent("Folder") {
                    HStack {
                        Text(backup.folder?.path(percentEncoded: false).replacingOccurrences(of: NSHomeDirectory(), with: "~") ?? "Not chosen")
                            .foregroundStyle(backup.folder == nil ? .secondary : .primary).lineLimit(1).truncationMode(.middle)
                        Button("Choose…") { backup.chooseFolder() }
                    }
                }
                Toggle("Include podcasts, videos and recordings", isOn: $backup.includeMedia)
                Picker("Keep", selection: $backup.keep) {
                    ForEach([1, 2, 3, 5, 10], id: \.self) { Text("\($0) newest").tag($0) }
                }
                Toggle("Back up daily while LocalBook is open", isOn: $backup.daily)
                HStack {
                    Button { Task { await backup.backUpNow() } } label: { Label("Back up now", systemImage: "externaldrive.badge.timemachine") }
                        .disabled(backup.working || backup.folder == nil)
                    if backup.working { ProgressView().controlSize(.small) }
                    Text(backup.working ? backup.statusLine : (backup.lastBackup.map { "Last backup \($0.formatted(date: .abbreviated, time: .shortened))." } ?? "No backup yet."))
                        .font(.callout).foregroundStyle(.secondary).lineLimit(2)
                }
                if !backup.working, !backup.statusLine.isEmpty { Text(backup.statusLine).font(.callout).foregroundStyle(.secondary) }
                if let why = backup.deviceKeyLine { Text(why).font(.callout).foregroundStyle(.orange) }
                if let why = backup.errorLine { Text(why).font(.callout).foregroundStyle(.orange) }
                Text("Everything the app keeps: the library, notes, sources, Studio items, agent keys and settings. Models are left out; they download again. Each backup is sealed to this Mac's key and to the recovery phrase.")
                    .font(.callout).foregroundStyle(.secondary)
            }

            if !backup.archives.isEmpty {
                Section("In the folder") {
                    ForEach(backup.archives) { a in
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(a.name).lineLimit(1).truncationMode(.middle)
                                Text("\(a.created?.formatted(date: .abbreviated, time: .shortened) ?? "date unknown") · \(ByteCountFormatter.string(fromByteCount: Int64(a.bytes), countStyle: .file))\(a.device == PhoneIdentity.deviceId ? "" : " · another device")")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button("Restore…") { begin(a.url) }.disabled(backup.restorePending)
                            Button(role: .destructive) { backup.delete(a) } label: { Image(systemName: "trash") }.buttonStyle(.borderless)
                        }
                    }
                }
            }

            Section("Recovery phrase") {
                if backup.hasRecoveryKey {
                    Label("A recovery phrase is set. It opens this Mac's backups on any Mac.", systemImage: "key.fill")
                    HStack {
                        Button("Replace…") { backup.createPhrase() }
                        Button("Use a phrase I already have…") { showExistingPhrase = true }
                        Button("Forget", role: .destructive) { backup.forgetPhrase() }
                    }
                } else {
                    Text("Without a phrase, a backup can only be opened by this Mac. A phrase is 24 words to write down once; the same phrase works on the old LocalBook app's backups.")
                        .font(.callout).foregroundStyle(.secondary)
                    HStack {
                        Button("Create a recovery phrase") { backup.createPhrase() }.buttonStyle(.borderedProminent)
                        Button("Use a phrase I already have…") { showExistingPhrase = true }
                    }
                }
                if showExistingPhrase {
                    TextField("24 words", text: $phraseEntry, axis: .vertical).lineLimit(2...4)
                    HStack {
                        Button("Use this phrase") { if backup.useExistingPhrase(phraseEntry) { phraseEntry = ""; showExistingPhrase = false } }
                            .disabled(phraseEntry.split(separator: " ").count < 12)
                        Button("Cancel") { phraseEntry = ""; showExistingPhrase = false }
                    }
                }
            }

            Section("Restore from a file") {
                Button { pick() } label: { Label("Open a backup…", systemImage: "folder") }.disabled(backup.restorePending)
                Text("Also for a backup made on another Mac: it asks for that Mac's recovery phrase. The restore goes in at the next launch and keeps what it replaces.")
                    .font(.callout).foregroundStyle(.secondary)
            }

            Section {
                Text("Touch ID lock, the credential locker and the encrypted volume are still to come (gap list, flight 2 and later).")
                    .font(.callout).foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .sheet(isPresented: Binding(get: { backup.freshPhrase != nil }, set: { if !$0 { backup.discardFreshPhrase() } })) {
            FreshPhraseSheet(phrase: backup.freshPhrase ?? "", confirm: { backup.confirmFreshPhrase() }, cancel: { backup.discardFreshPhrase() })
        }
        .sheet(isPresented: Binding(get: { restoreTarget != nil }, set: { if !$0 { restoreTarget = nil; restoreState = .idle } })) {
            if let url = restoreTarget { restoreSheet(url) }
        }
    }

    private func pick() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = false; panel.allowsMultipleSelection = false
        panel.allowedContentTypes = [.data]
        panel.message = "Choose a LocalBook backup (.lbbackup)."
        guard panel.runModal() == .OK, let url = panel.url else { return }
        begin(url)
    }

    private func begin(_ url: URL) {
        restoreTarget = url; restorePhrase = ""; restoreState = .checking
        Task { await check(url, phrase: nil) }
    }

    private func check(_ url: URL, phrase: String?) async {
        restoreState = .checking
        switch await model.backup.verify(url, phrase: phrase) {
        case .opened(let m):
            let count = m.files.count, mb = Double(m.totalBytes) / 1_048_576
            let when = (try? Date(m.createdAt, strategy: .iso8601)).map { $0.formatted(date: .abbreviated, time: .shortened) } ?? m.createdAt
            restoreState = .ready("\(count) files, \(mb < 10 ? String(format: "%.1f", mb) : String(Int(mb))) MB, made \(when) on \(m.deviceName ?? m.deviceId)."
                                  + (m.isFromThisApp ? "" : " This backup is from the old LocalBook app: it cannot be restored here, but Settings › Library › Move my library here brings that library over."))
        case .needsPhrase: restoreState = .needsPhrase
        case .failed(let why): restoreState = .failed(why)
        }
    }

    @ViewBuilder
    private func restoreSheet(_ url: URL) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Restore \(url.lastPathComponent)").font(.headline)
            switch restoreState {
            case .idle, .checking: HStack { ProgressView().controlSize(.small); Text("Checking the backup…") }
            case .needsPhrase:
                Text("This Mac's key does not open this backup. Enter the recovery phrase of the Mac that made it.")
                TextField("24 words", text: $restorePhrase, axis: .vertical).lineLimit(2...4)
                Button("Unlock") { Task { await check(url, phrase: restorePhrase) } }.disabled(restorePhrase.split(separator: " ").count < 12)
            case .ready(let line):
                Text(line)
                Text("Restoring replaces this Mac's library, notes and settings with the backup's at the next launch. What is replaced is kept in a Restore-replaced folder, so this can be undone by hand.")
                    .font(.callout).foregroundStyle(.secondary)
            case .failed(let why): Text(why).foregroundStyle(.orange)
            case .staging: HStack { ProgressView().controlSize(.small); Text("Getting the restore ready…") }
            }
            HStack {
                Spacer()
                Button("Cancel") { restoreTarget = nil; restoreState = .idle }.keyboardShortcut(.cancelAction)
                if case .ready(let line) = restoreState, !line.contains("old LocalBook app") {
                    Button("Restore at next launch") {
                        Task {
                            restoreState = .staging
                            if let why = await model.backup.restore(url, phrase: restorePhrase.isEmpty ? nil : restorePhrase) { restoreState = .failed(why) }
                            else { restoreTarget = nil; restoreState = .idle }
                        }
                    }.buttonStyle(.borderedProminent)
                }
            }
        }
        .padding(20)
        .frame(width: 460)
    }
}

/// The 24 words, shown once. The phrase is not kept anywhere: only its public key is, once the user confirms.
struct FreshPhraseSheet: View {
    let phrase: String
    let confirm: () -> Void
    let cancel: () -> Void
    @State private var copied = false

    var body: some View {
        let words = phrase.split(separator: " ").map(String.init)
        VStack(alignment: .leading, spacing: 14) {
            Text("Your recovery phrase").font(.headline)
            Text("Write these 24 words down, in order, and keep them somewhere safe. LocalBook keeps no copy: anyone with the phrase can open your backups, and without it a backup from this Mac can only be opened here.")
                .font(.callout).foregroundStyle(.secondary)
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), alignment: .leading), count: 4), spacing: 6) {
                ForEach(Array(words.enumerated()), id: \.offset) { i, w in
                    HStack(spacing: 6) {
                        Text("\(i + 1).").font(.caption.monospacedDigit()).foregroundStyle(.secondary).frame(width: 22, alignment: .trailing)
                        Text(w).font(.body.monospaced())
                    }
                }
            }
            .padding(12)
            .background(RoundedRectangle(cornerRadius: 8).fill(Color(nsColor: .controlBackgroundColor)))
            HStack {
                Button(copied ? "Copied" : "Copy") {
                    NSPasteboard.general.clearContents(); NSPasteboard.general.setString(phrase, forType: .string); copied = true
                }
                Spacer()
                Button("Cancel") { cancel() }.keyboardShortcut(.cancelAction)
                Button("I wrote it down") { confirm() }.buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
            }
        }
        .padding(20)
        .frame(width: 560)
    }
}
