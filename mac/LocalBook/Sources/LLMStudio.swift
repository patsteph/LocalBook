import AppKit
import LocalBookCore
import LocalBookEngines
import Observation
import SwiftUI

/// LLM Studio, as Settings › Models: the Locker (models on this Mac, which one answers), Browse (Hugging Face, with
/// "will it fit"), the Evaluator (a speed test with history for comparing models) and Hardware. The Python app's
/// LLM Studio had the same four ideas; the Mac keeps them in one Settings tab, with the answer model loaded by the
/// package's `LibraryModel` and chosen through `AnswerModels`.
@MainActor @Observable
final class LLMStudioModel {
    private(set) var downloaded: [AnswerModels.Downloaded] = []
    private(set) var found: [AnswerModels.Found] = []
    private(set) var searching = false
    private(set) var searchProblem: String?
    /// 0…1 per model id while it downloads.
    private(set) var downloads: [String: Double] = [:]
    private(set) var downloadProblem: String?
    private(set) var history: [AnswerBenchResult] = AnswerBench.history()
    private(set) var benchRunning = false
    private(set) var benchProblem: String?
    private(set) var budget = AnswerModels.budget()
    var query = ""

    var chosenId: String { AnswerModels.chosenId }

    func reload() {
        downloaded = AnswerModels.downloaded()
        budget = AnswerModels.budget()
    }

    func choose(_ id: String, library: LibraryModel) async {
        guard id != AnswerModels.chosenId else { return }
        AnswerModels.choose(id)
        await library.reloadAnswerModel()
        reload()
    }

    func delete(_ id: String, library: LibraryModel) async {
        let wasChosen = id == AnswerModels.chosenId
        do { try AnswerModels.delete(id) } catch { downloadProblem = error.localizedDescription }
        if wasChosen { await library.reloadAnswerModel() }
        reload()
    }

    func search() async {
        searching = true; searchProblem = nil
        defer { searching = false }
        do { found = try await AnswerModels.search(query) } catch { searchProblem = error.localizedDescription; found = [] }
    }

    func download(_ id: String) async {
        guard downloads[id] == nil else { return }
        downloads[id] = 0; downloadProblem = nil
        do {
            try await AnswerModels.download(id) { fraction in Task { @MainActor [weak self] in self?.downloads[id] = fraction } }
        } catch { downloadProblem = "\(AnswerModels.shortName(id)): \(error.localizedDescription)" }
        downloads[id] = nil
        reload()
    }

    func bench(library: LibraryModel) async {
        guard let gemma = library.gemma, !benchRunning else { return }
        benchRunning = true; benchProblem = nil
        defer { benchRunning = false }
        do {
            let result = try await AnswerBench.run(gemma, model: AnswerModels.shortName(AnswerModels.chosenId))
            AnswerBench.remember(result)
            history = AnswerBench.history()
        } catch { benchProblem = error.localizedDescription }
    }

    func forget(_ ids: Set<UUID>) {
        for id in ids { AnswerBench.forget(id) }
        history = AnswerBench.history()
    }
}

struct LLMStudioView: View {
    @Environment(AppModel.self) private var model
    @State private var studio = LLMStudioModel()
    @State private var section = "locker"
    @AppStorage("searchUnits") private var searchUnits = "ane"
    @State private var confirmDelete: String?
    @State private var benchSelection: Set<UUID> = []

    private var library: LibraryModel { model.library }

    var body: some View {
        VStack(spacing: 0) {
            Picker("", selection: $section) {
                Text("Locker").tag("locker"); Text("Browse").tag("browse"); Text("Evaluator").tag("evaluator"); Text("Hardware").tag("hardware")
            }
            .pickerStyle(.segmented).labelsHidden().padding(.horizontal, 20).padding(.top, 8)
            Group {
                switch section {
                case "browse": browse
                case "evaluator": evaluator
                case "hardware": hardware
                default: locker
                }
            }
        }
        .task { studio.reload() }
        .confirmationDialog("Remove this model from this Mac?", isPresented: Binding(get: { confirmDelete != nil }, set: { if !$0 { confirmDelete = nil } }), titleVisibility: .visible) {
            Button("Remove", role: .destructive) {
                if let id = confirmDelete { Task { await studio.delete(id, library: library) } }
                confirmDelete = nil
            }
        } message: { Text("Its files are deleted. You can download it again from Browse.") }
    }

    // MARK: Locker

    private var locker: some View {
        Form {
            Section {
                LabeledContent("Answering with", value: AnswerModels.shortName(studio.chosenId))
                LabeledContent("State", value: answerLine)
                if case .failed = library.answerModelState { Button("Load again") { Task { await library.reloadAnswerModel() } } }
                LabeledContent("Search model", value: searchLine)
                Text(library.rerankerLine).font(.callout).foregroundStyle(.secondary)
                Text(library.recipeLine).font(.callout).foregroundStyle(.secondary)
            } header: { Text("Loaded on this Mac") }
            Section {
                if studio.downloaded.isEmpty {
                    Text("No models downloaded yet. The answer model is fetched the first time it is needed.").foregroundStyle(.secondary)
                }
                ForEach(studio.downloaded) { m in
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(AnswerModels.shortName(m.id)).font(.body)
                            Text("\(m.id.split(separator: "/").first.map(String.init) ?? "") · \(bytes(m.bytes)) · \(fitLabel(AnswerModels.fit(bytes: m.bytes, budget: studio.budget)))")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        if m.id == studio.chosenId {
                            Label("Answering", systemImage: "checkmark.circle.fill").foregroundStyle(.green).font(.callout)
                        } else {
                            Button("Use") { Task { await studio.choose(m.id, library: library) } }
                        }
                    }
                    .contextMenu {
                        Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([m.directory]) }
                        Button("Remove from this Mac…", role: .destructive) { confirmDelete = m.id }
                    }
                }
                if let p = studio.downloadProblem { Text(p).font(.footnote).foregroundStyle(.orange) }
            } header: { Text("On this Mac") } footer: {
                Text("“Use” switches the answer model now; it is reloaded while you wait. Right-click a model to remove it.")
            }
        }
        .formStyle(.grouped)
    }

    // MARK: Browse

    private var browse: some View {
        Form {
            Section {
                HStack {
                    TextField("Search Hugging Face for MLX models", text: Bindable(studio).query)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit { Task { await studio.search() } }
                    Button("Search") { Task { await studio.search() } }.disabled(studio.searching)
                }
                Text("Empty search: the trending MLX models. Sizes are the weights alone; “fits” allows for the context and the search model.")
                    .font(.footnote).foregroundStyle(.secondary)
                if let p = studio.searchProblem { Text(p).font(.footnote).foregroundStyle(.orange) }
                if let p = studio.downloadProblem { Text(p).font(.footnote).foregroundStyle(.orange) }
            }
            Section {
                if studio.searching { HStack { ProgressView().controlSize(.small); Text("Looking…").foregroundStyle(.secondary) } }
                if studio.found.isEmpty, !studio.searching { Text("Nothing found yet.").foregroundStyle(.secondary) }
                ForEach(studio.found) { f in foundRow(f) }
            } header: { Text("Models") }
        }
        .formStyle(.grouped)
        .task { if studio.found.isEmpty { await studio.search() } }
    }

    private func foundRow(_ f: AnswerModels.Found) -> some View {
        let have = studio.downloaded.contains { $0.id == f.id }
        let fit = f.bytes.map { AnswerModels.fit(bytes: $0, budget: studio.budget) }
        return HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: 2) {
                Text(AnswerModels.shortName(f.id)).font(.body)
                HStack(spacing: 6) {
                    Text(f.author)
                    Text("·"); Label(f.downloads.formatted(.number.notation(.compactName)), systemImage: "arrow.down.circle")
                    Text("·"); Label(f.likes.formatted(.number.notation(.compactName)), systemImage: "heart")
                    if let b = f.bytes { Text("·"); Text(bytes(b)) }
                    if let fit { Text("·"); Text(fitLabel(fit)).foregroundStyle(fit == .tooBig ? .red : fit == .tight ? .orange : .green) }
                    if f.gated { Text("·"); Text("needs a Hugging Face licence").foregroundStyle(.orange) }
                }
                .font(.caption).foregroundStyle(.secondary).labelStyle(.titleAndIcon)
            }
            Spacer()
            if have {
                Label("Downloaded", systemImage: "checkmark.circle").foregroundStyle(.green).font(.callout)
            } else if let p = studio.downloads[f.id] {
                ProgressView(value: p).frame(width: 90)
            } else {
                Button("Download") { Task { await studio.download(f.id) } }.disabled(f.gated)
            }
        }
    }

    // MARK: Evaluator

    private var evaluator: some View {
        VStack(spacing: 0) {
            Form {
                Section {
                    HStack {
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Speed test on \(AnswerModels.shortName(studio.chosenId))")
                            Text("A 500-token prompt and a 160-token answer; first word, words per second and memory are kept so models can be compared.")
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                        Spacer()
                        if studio.benchRunning { ProgressView().controlSize(.small) }
                        Button("Run") { Task { await studio.bench(library: library) } }
                            .disabled(studio.benchRunning || library.answerModelState != .ready || library.answering)
                    }
                    if library.answerModelState != .ready { Text("The answer model is not loaded yet.").font(.footnote).foregroundStyle(.secondary) }
                    if let p = studio.benchProblem { Text(p).font(.footnote).foregroundStyle(.orange) }
                }
            }
            .formStyle(.grouped).frame(height: 120)
            Table(studio.history, selection: $benchSelection) {
                TableColumn("Model") { Text($0.model).lineLimit(1) }.width(min: 150)
                TableColumn("When") { Text($0.date.formatted(date: .abbreviated, time: .shortened)) }.width(min: 120)
                TableColumn("First word") { Text(seconds($0.firstWordSeconds)) }.width(70)
                TableColumn("Tokens/s") { Text(number($0.tokensPerSecond, 1)) }.width(70)
                TableColumn("Prompt tok/s") { Text(number($0.promptTokensPerSecond, 0)) }.width(80)
                TableColumn("Peak memory") { Text("\(Int($0.peakFootprintMB)) MB") }.width(90)
            }
            .contextMenu(forSelectionType: UUID.self) { ids in
                Button("Remove", role: .destructive) { studio.forget(ids) }
            }
            if studio.history.isEmpty { Text("No runs yet.").font(.callout).foregroundStyle(.secondary).padding(.bottom, 8) }
        }
    }

    // MARK: Hardware

    private var hardware: some View {
        Form {
            Section("Memory") {
                LabeledContent("In this Mac", value: gb(studio.budget.physicalGB))
                LabeledContent("Available to models (GPU working set)", value: gb(studio.budget.workingSetGB))
                LabeledContent("LocalBook is using", value: gb(studio.budget.inUseGB))
                LabeledContent("Device", value: "\(DeviceDescription.model) · \(DeviceDescription.system)")
            }
            Section {
                Picker("Search runs on", selection: $searchUnits) {
                    Text("Neural Engine").tag("ane")
                    Text("GPU").tag("gpu")
                }
                Text("Takes effect the next time the app opens. Answers always run on the GPU (MLX).").font(.callout).foregroundStyle(.secondary)
            } header: { Text("Compute") }
        }
        .formStyle(.grouped)
        .task { studio.reload() }
    }

    // MARK: words

    private var searchLine: String {
        switch library.searchState {
        case .notReady: "Not loaded"
        case .preparing(let what): "Loading \(what)"
        case .ready: "Ready (EmbeddingGemma 2, Core ML)"
        case .failed(let why): "Failed: \(why)"
        }
    }

    private var answerLine: String {
        switch library.answerModelState {
        case .idle: "Not loaded"
        case .loading: "Loading… (downloads first if it is not on this Mac)"
        case .ready: "Ready (MLX)"
        case .failed(let why): "Failed: \(why)"
        }
    }

    private func fitLabel(_ fit: AnswerModels.Fit) -> String {
        switch fit { case .fits: "fits"; case .tight: "tight fit"; case .tooBig: "too big for this Mac" }
    }
    private func bytes(_ b: Int64) -> String { ByteCountFormatter.string(fromByteCount: b, countStyle: .file) }
    private func gb(_ v: Double) -> String { v.formatted(.number.precision(.fractionLength(1))) + " GB" }
    private func seconds(_ v: Double) -> String { v.isNaN ? "–" : v.formatted(.number.precision(.fractionLength(2))) + " s" }
    private func number(_ v: Double, _ digits: Int) -> String { v.isNaN ? "–" : v.formatted(.number.precision(.fractionLength(digits))) }
}
