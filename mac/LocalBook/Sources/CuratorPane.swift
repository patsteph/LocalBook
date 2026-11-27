import LocalBookCore
import LocalBookEngines
import SwiftUI

/// The Curator: the one agent that reads every notebook. Briefs, the weekly wrap, patterns across notebooks,
/// devil's advocate, a digest per notebook, and the Curator's own name, personality and voice. Everything it writes
/// is kept on the shelf at the left.
struct CuratorPane: View {
    @Environment(AppModel.self) private var model
    @State private var selected: String?
    @State private var thesis = ""
    @State private var editingProfile = false
    private var curator: CuratorEngine { Agents.curator }
    private var library: LibraryModel { model.library }
    private var opened: CuratorPiece? { curator.pieces.first { $0.id == selected } }

    var body: some View {
        HSplitView {
            shelf.frame(minWidth: 280, idealWidth: 340, maxWidth: 480)
            detail.frame(minWidth: 460, maxWidth: .infinity, maxHeight: .infinity)
        }
        .sheet(isPresented: $editingProfile) { CuratorProfileSheet(curator: curator) }
        .onChange(of: model.curatorRequest) { _, r in
            guard let r else { return }
            model.curatorRequest = nil
            Task { await run(r) }
        }
    }

    private func run(_ kind: CuratorPiece.Kind) async {
        let made: CuratorPiece?
        switch kind {
        case .brief: made = await curator.brief(library: library)
        case .weekly: made = await curator.weekly(library: library)
        case .patterns: made = await curator.patterns(library: library)
        case .devilsAdvocate: made = await curator.devilsAdvocate(thesis, library: library)
        case .digest: made = nil
        }
        if let made { selected = made.id }
    }

    private var shelf: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Image(systemName: "sparkles").foregroundStyle(.purple)
                VStack(alignment: .leading, spacing: 0) {
                    Text(curator.profile.name).font(.headline)
                    Text(curator.profile.voice.label + " · " + curator.profile.personality).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                }
                Spacer()
                Button { editingProfile = true } label: { Image(systemName: "slider.horizontal.3") }.buttonStyle(.borderless).help("Name, personality, voice, notebooks to leave out")
            }
            .padding(12)
            HStack(spacing: 8) {
                Button { Task { await run(.brief) } } label: { Label("Brief", systemImage: "sunrise") }
                Button { Task { await run(.weekly) } } label: { Label("Week", systemImage: "calendar") }
                Button { Task { await run(.patterns) } } label: { Label("Patterns", systemImage: "point.3.connected.trianglepath.dotted") }
            }
            .controlSize(.small).disabled(curator.working).padding(.horizontal, 12)
            HStack(spacing: 6) {
                TextField("A thesis to argue against…", text: $thesis).textFieldStyle(.roundedBorder).onSubmit { Task { await run(.devilsAdvocate) } }
                Button("Devil's advocate") { Task { await run(.devilsAdvocate) } }.disabled(curator.working || thesis.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            .controlSize(.small).padding(12)
            if curator.working { HStack { ProgressView().controlSize(.small); Text(curator.status).font(.caption).foregroundStyle(.secondary).lineLimit(1) }.padding(.horizontal, 12).padding(.bottom, 8) }
            else if let e = curator.error { Text(e).font(.caption).foregroundStyle(.red).lineLimit(2).padding(.horizontal, 12).padding(.bottom, 8) }
            Divider()
            List(selection: $selected) {
                if !curator.connections.isEmpty { Text("Connections").tag("connections") }
                Text("Notebook digests").tag("digests")
                Section("Written") {
                    if curator.pieces.isEmpty { Text("Nothing yet. Ask for a brief.").foregroundStyle(.secondary) }
                    ForEach(curator.pieces) { p in
                        VStack(alignment: .leading, spacing: 2) {
                            HStack { Image(systemName: symbol(p.kind)).foregroundStyle(.secondary); Text(p.title).lineLimit(1) }
                            Text(p.created.formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundStyle(.secondary)
                        }
                        .tag(p.id)
                        .contextMenu { Button("Delete", role: .destructive) { curator.delete(p); if selected == p.id { selected = nil } } }
                    }
                }
            }
            .listStyle(.sidebar)
        }
    }

    @ViewBuilder private var detail: some View {
        if selected == "connections" { connectionsView }
        else if selected == "digests" { digestsView }
        else if let p = opened {
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    HStack {
                        Text(p.title).font(.title2.weight(.semibold))
                        Spacer()
                        Button { save(p) } label: { Label("Save as note", systemImage: "note.text.badge.plus") }.controlSize(.small)
                    }
                    MarkdownText(text: p.markdown).textSelection(.enabled)
                }
                .padding(20)
            }
        } else {
            VStack(spacing: 10) {
                Image(systemName: "sparkles").font(.system(size: 36)).foregroundStyle(.secondary)
                Text("\(curator.profile.name) reads every notebook").font(.title3)
                Text("A brief says what changed since the last one. The week in review looks back seven days. Patterns finds where two notebooks say the same thing. Devil's advocate argues against a claim from everything you have.")
                    .font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 460)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private var connectionsView: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Text("Connections across notebooks").font(.title2.weight(.semibold))
                ForEach(curator.connections) { c in
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            Text(c.label).font(.headline)
                            Spacer()
                            Text("\(Int(c.strength * 100)) %").font(.caption).foregroundStyle(.secondary)
                            if c.status == .approved { Image(systemName: "hand.thumbsup.fill").foregroundStyle(.green) }
                        }
                        Text("\(c.titleA) · \(c.fileA)  ↔  \(c.titleB) · \(c.fileB)").font(.caption).foregroundStyle(.secondary)
                        Text(c.passageA).font(.callout).lineLimit(3).padding(8).background(RoundedRectangle(cornerRadius: 6).fill(Color.accentColor.opacity(0.06)))
                        Text(c.passageB).font(.callout).lineLimit(3).padding(8).background(RoundedRectangle(cornerRadius: 6).fill(Color.accentColor.opacity(0.06)))
                        HStack {
                            Button("Useful") { curator.approve(c) }.disabled(c.status == .approved)
                            Button("Dismiss") { curator.dismiss(c) }
                            Button("Open \(c.titleA)") { model.selection = .notebook(c.notebookA) }
                            Button("Open \(c.titleB)") { model.selection = .notebook(c.notebookB) }
                        }
                        .controlSize(.small)
                    }
                    .padding(12).background(RoundedRectangle(cornerRadius: 10).fill(Color(nsColor: .controlBackgroundColor)))
                }
            }
            .padding(20)
        }
    }

    private var digestsView: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                HStack { Text("What \(curator.profile.name) understands").font(.title2.weight(.semibold)); Spacer()
                    Button("Refresh all") { Task { for nb in library.notebooks where !curator.profile.excludedNotebooks.contains(nb.id) { _ = await curator.digest(nb, library: library) } } }.disabled(curator.working).controlSize(.small) }
                ForEach(library.notebooks) { nb in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text(nb.title).font(.headline)
                            if curator.profile.excludedNotebooks.contains(nb.id) { Text("left out").font(.caption).foregroundStyle(.secondary) }
                            Spacer()
                            if let d = curator.state.digests[nb.id] { Text(d.created.formatted(date: .abbreviated, time: .shortened)).font(.caption).foregroundStyle(.secondary) }
                            Button("Digest") { Task { _ = await curator.digest(nb, library: library) } }.disabled(curator.working).controlSize(.small)
                        }
                        Text(curator.state.digests[nb.id]?.markdown ?? "No digest yet.").font(.callout).foregroundStyle(curator.state.digests[nb.id] == nil ? .secondary : .primary)
                    }
                    .padding(12).background(RoundedRectangle(cornerRadius: 10).fill(Color(nsColor: .controlBackgroundColor)))
                }
            }
            .padding(20)
        }
    }

    private func save(_ p: CuratorPiece) {
        NotificationCenter.default.post(name: .newNote, object: "# \(p.title)\n\n" + p.markdown)
    }
    private func symbol(_ k: CuratorPiece.Kind) -> String {
        switch k { case .brief: "sunrise"; case .weekly: "calendar"; case .patterns: "point.3.connected.trianglepath.dotted"; case .devilsAdvocate: "flame"; case .digest: "text.alignleft" }
    }
}

struct CuratorProfileSheet: View {
    let curator: CuratorEngine
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var profile: CuratorProfile

    init(curator: CuratorEngine) { self.curator = curator; _profile = State(initialValue: curator.profile) }

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("The Curator").font(.title2.weight(.semibold))
            Form {
                TextField("Name", text: $profile.name)
                TextField("Personality", text: $profile.personality, prompt: Text("thoughtful, well-read, direct"))
                Picker("Voice", selection: $profile.voice) { ForEach(CuratorProfile.Voice.allCases) { v in Text(v.label).tag(v) } }
                Text(profile.voice.description).font(.caption).foregroundStyle(.secondary)
                Section("Leave these notebooks out") {
                    ForEach(model.library.notebooks) { nb in
                        Toggle(nb.title, isOn: Binding(get: { profile.excludedNotebooks.contains(nb.id) },
                                                      set: { on in if on { profile.excludedNotebooks.append(nb.id) } else { profile.excludedNotebooks.removeAll { $0 == nb.id } } }))
                    }
                }
            }
            .formStyle(.grouped)
            HStack { Spacer(); Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction); Button("Save") { curator.setProfile(profile); dismiss() }.keyboardShortcut(.defaultAction) }
        }
        .padding(20).frame(width: 520, height: 520)
    }
}
