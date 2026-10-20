import SwiftUI

struct LibraryView: View {
    @State private var model = LibraryModel()

    var body: some View {
        Form {
            Section {
                switch model.syncState {
                case .idle:
                    Button("Sync the library from the Mac") { Task { await model.sync() } }
                case .syncing(let line):
                    HStack { ProgressView(); Text(line).font(.footnote) }
                case .failed(let why):
                    Text(why).font(.footnote).foregroundStyle(.red)
                    Button("Try again") { Task { await model.sync() } }
                }
                LabeledContent("On this phone", value: "\(model.notebooks.count) notebooks · \(model.chunkCount) chunks")
                Text(model.lastSyncLine).font(.footnote).foregroundStyle(.secondary)
                if let s = model.lastSummary {
                    Text("Last run: \(s.notebooksChanged) of \(s.notebooks) notebooks changed · \(s.sourcesFetched) sources, \(s.chunksFetched) chunks fetched · "
                         + "\(s.sourcesRemoved) removed · \(String(format: "%.1f", Double(s.bytes) / 1_000_000)) MB in \(String(format: "%.1f", s.seconds)) s")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            } header: { Text("Library") } footer: {
                Text("Needs the Mac app running with sync on, and this phone paired (row 3). Notebooks switched to “Keep off phones” on the Mac never arrive.")
            }

            Section("Notebooks") {
                ForEach(model.notebooks) { n in
                    LabeledContent(n.title, value: "\(n.chunks)")
                }
            }

            Section {
                Picker("Who answers", selection: $model.askMode) {
                    ForEach(LibraryModel.AskMode.allCases) { Text($0.rawValue).tag($0) }
                }
                .pickerStyle(.segmented)
                TextField("Ask your library a question", text: $model.query)
                    .submitLabel(.go)
                    .onSubmit { Task { await model.ask() } }
                Picker("Notebook", selection: $model.scopeToNotebook) {
                    ForEach(model.notebooks) { Text($0.title).tag(String?.some($0.id)) }
                }
                Button("Ask") { Task { await model.ask() } }
                    .disabled(model.answering || (model.askMode == .phone && model.searchState != .ready))
                // Searching on the phone (and answering on the phone) needs the phone's own search model.
                switch model.searchState {
                case .notReady:
                    Button("Get the phone's search model ready") { Task { await model.prepareSearch() } }
                case .preparing(let what):
                    HStack { ProgressView(); Text(what).font(.footnote) }
                case .failed(let why):
                    Text(why).font(.footnote).foregroundStyle(.red)
                    Button("Try again") { Task { await model.prepareSearch() } }
                case .ready:
                    Button("Search only (just the passages, on this phone)") { Task { await model.search() } }
                    if !model.timing.isEmpty { Text(model.timing).font(.footnote).foregroundStyle(.secondary) }
                }
            } header: { Text("Ask") } footer: {
                Text("“Ask the Mac” needs the Mac app running. “On this phone” works offline: it embeds your question here, matches the Mac's vectors and keywords, and Gemma writes the answer.")
            }

            if model.answering || !model.answerText.isEmpty || !model.answerStatus.isEmpty {
                Section {
                    if model.answering { Button("Stop", role: .destructive) { model.stopAnswer() } }
                    if !model.answerText.isEmpty { Text(model.answerText) }
                    if !model.answerStatus.isEmpty { Text(model.answerStatus).font(.footnote).foregroundStyle(.secondary) }
                    ForEach(model.followUps, id: \.self) { q in
                        Button(q) { model.query = q; Task { await model.ask() } }.font(.footnote)
                    }
                } header: { Text("Answer") } footer: {
                    Text(model.askMode == .mac
                         ? "Written by the Mac with its full search (query analysis, hybrid search, re-rank). Your question stays private to this phone: the Mac records nothing about it."
                         : "Offline mode: written on this phone by the same Gemma 4 model, from the best passages by vectors and keywords (no re-rank). It stops if you lock the phone.")
                }
            }

            if !model.hits.isEmpty {
                Section("Sources") {
                    ForEach(Array(model.hits.enumerated()), id: \.element.id) { i, h in
                        VStack(alignment: .leading, spacing: 3) {
                            Text("[\(i + 1)] \(h.notebookTitle) · \(h.filename)").font(.footnote.weight(.medium))
                            Text(h.text.prefix(240) + (h.text.count > 240 ? "…" : "")).font(.footnote)
                            Text(model.sourceScoreIsConfidence ? String(format: "confidence %.0f%%", Double(h.score) * 100)
                                                               : String(format: "similarity %.3f", h.score))
                                .font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
        .navigationTitle("Library")
        .navigationBarTitleDisplayMode(.inline)
        .task { await model.open() }
        .onDisappear { model.unloadAnswerModel() }
    }
}
