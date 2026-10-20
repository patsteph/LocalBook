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
                switch model.searchState {
                case .notReady:
                    Button("Get the search model ready") { Task { await model.prepareSearch() } }
                case .preparing(let what):
                    HStack { ProgressView(); Text(what).font(.footnote) }
                case .failed(let why):
                    Text(why).font(.footnote).foregroundStyle(.red)
                    Button("Try again") { Task { await model.prepareSearch() } }
                case .ready:
                    TextField("Ask your library a question", text: $model.query)
                        .submitLabel(.search)
                        .onSubmit { Task { await model.search() } }
                    Picker("Notebook", selection: $model.scopeToNotebook) {
                        ForEach(model.notebooks) { Text($0.title).tag(String?.some($0.id)) }
                    }
                    Button("Ask (answer with Gemma from the passages)") { Task { await model.ask() } }
                        .disabled(model.answering)
                    Button("Search only (just the passages)") { Task { await model.search() } }
                    if !model.timing.isEmpty { Text(model.timing).font(.footnote).foregroundStyle(.secondary) }
                }
            } header: { Text("Search on the phone") } footer: {
                Text("The question is embedded on this phone and compared with the Mac's own vectors — the same match test 1 proved.")
            }

            if model.answering || !model.answerText.isEmpty || !model.answerStatus.isEmpty {
                Section {
                    if model.answering { Button("Stop", role: .destructive) { model.stopAnswer() } }
                    if !model.answerText.isEmpty { Text(model.answerText) }
                    if !model.answerStatus.isEmpty { Text(model.answerStatus).font(.footnote).foregroundStyle(.secondary) }
                } header: { Text("Answer") } footer: {
                    Text("Written on this phone by the same Gemma 4 model the Mac runs, from the passages below. It stops if you lock the phone.")
                }
            }

            if !model.hits.isEmpty {
                Section("Sources") {
                    ForEach(Array(model.hits.enumerated()), id: \.element.id) { i, h in
                        VStack(alignment: .leading, spacing: 3) {
                            Text("[\(i + 1)] \(h.notebookTitle) · \(h.filename)").font(.footnote.weight(.medium))
                            Text(h.text.prefix(240) + (h.text.count > 240 ? "…" : "")).font(.footnote)
                            Text(String(format: "similarity %.3f", h.score)).font(.caption2).foregroundStyle(.secondary)
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
