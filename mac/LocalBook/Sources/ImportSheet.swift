import AppKit
import LocalBookCore
import LocalBookEngines
import SwiftUI

/// Move my library here: one click for the person who already has LocalBook on this Mac. Point the app at the old
/// app's folder once (the sandbox needs the nod; the token in it lets this app talk to the running old app), see what
/// is there, move it. Nothing in the old app changes; this app answers from the copy, with or without a pairing.
struct ImportSheet: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss

    enum Step: Equatable { case choose, found, running, done, failed(String) }
    @State private var step: Step = .choose
    @State private var app: LibraryImport.OldApp?
    @State private var line = ""
    @State private var report: LibraryImport.Report?
    @State private var working = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Group {
                switch step {
                case .choose: choose
                case .found: found
                case .running: running
                case .done: done
                case .failed(let why): failed(why)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
            .padding(24)
            Divider()
            HStack {
                if step == .choose {
                    Text("The old LocalBook must be open and finished starting.").font(.footnote).foregroundStyle(.secondary)
                }
                Spacer()
                if step == .running {
                    Text("Keep this window open.").font(.footnote).foregroundStyle(.secondary)
                } else {
                    Button(step == .done ? "Done" : "Cancel") { dismiss() }.keyboardShortcut(.cancelAction)
                }
                if step == .found, let app {
                    Button("Move \(app.notebooks) notebook\(app.notebooks == 1 ? "" : "s")") { Task { await run(app) } }
                        .keyboardShortcut(.defaultAction).buttonStyle(.borderedProminent)
                }
            }
            .padding(16)
        }
        .frame(width: 540, height: 420)
    }

    private var choose: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Move your library here").font(.title2.bold())
            Text("Every notebook, including the ones kept on this Mac only, with the text of every source, your notes and your Studio items, copied from the LocalBook already on this Mac. The old app keeps everything; this one answers from the copy.")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Text("Choose the old app's folder so this app may read its key. It is usually Library › Application Support › LocalBook in your home folder; the chooser opens there.")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack {
                Button("Choose the LocalBook folder…") { chooseFolder() }.disabled(working)
                if working { ProgressView().controlSize(.small) }
            }
        }
    }

    private var found: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Found \(app?.name ?? "LocalBook")").font(.title2.bold())
            if let app {
                LabeledContent("Notebooks", value: "\(app.notebooks)")
                LabeledContent("Kept on this Mac only", value: "\(app.deviceOnly)")
                LabeledContent("Folder", value: app.folder.path).lineLimit(1).truncationMode(.middle)
            }
            Text("Moving takes a few minutes for a large library. Notebooks appear in the sidebar as each one finishes, and you can use them while the rest copy. Run it again any time; only what is new is copied.")
                .font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }

    private var running: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Moving your library").font(.title2.bold())
            HStack(spacing: 8) { ProgressView().controlSize(.small); Text(line.isEmpty ? "Starting…" : line).font(.callout).lineLimit(2) }
        }
    }

    private var done: some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("Moved", systemImage: "checkmark.seal.fill").font(.title2.bold()).foregroundStyle(.green)
            if let r = report {
                LabeledContent("Notebooks", value: "\(r.notebooks)")
                LabeledContent("Sources copied this time", value: "\(r.sources) (\(r.chunks.formatted()) passages)")
                LabeledContent("Full texts", value: "\(r.texts)")
                LabeledContent("Notes", value: "\(r.notes)")
                LabeledContent("Studio items", value: "\(r.studio)")
                LabeledContent("Took", value: "\(Int(r.seconds)) s")
                if !r.problems.isEmpty {
                    Text("\(r.problems.count) item\(r.problems.count == 1 ? "" : "s") could not be copied:").font(.footnote).foregroundStyle(.orange)
                    ForEach(r.problems.prefix(4), id: \.self) { Text($0).font(.caption).foregroundStyle(.secondary).lineLimit(1) }
                }
            }
        }
    }

    private func failed(_ why: String) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            Label("Could not move the library", systemImage: "exclamationmark.triangle.fill").font(.title2.bold()).foregroundStyle(.orange)
            Text(why).font(.callout).fixedSize(horizontal: false, vertical: true)
            Button("Try again") { step = .choose }
        }
    }

    private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.directoryURL = LibraryImport.defaultFolder
        panel.prompt = "Use This Folder"
        panel.message = "Choose the LocalBook folder (Library › Application Support › LocalBook)."
        guard panel.runModal() == .OK, let url = panel.url else { return }
        working = true
        Task {
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            do { app = try await LibraryImport.find(folder: url); step = .found }
            catch { step = .failed(error.localizedDescription) }
            working = false
        }
    }

    private func run(_ app: LibraryImport.OldApp) async {
        step = .running
        do {
            report = try await model.library.importOldApp(app) { text in Task { @MainActor in line = text } }
            model.importDone()
            step = .done
        } catch { step = .failed(error.localizedDescription) }
    }
}
