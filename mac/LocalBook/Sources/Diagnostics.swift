import AppKit
import LocalBookCore
import LocalBookEngines
import Observation
import SwiftUI
import UniformTypeIdentifiers

/// The embedding parity test on this Mac: re-embed a fixture exported from the Python Mac and compare, at the phone's
/// bars. Phase 2's gate is "ParityRunner passes at the phone's bar", so it lives in Settings › Diagnostics.
/// The phone's `ParityModel` without the background run (the Mac has no locked-screen case).
@MainActor @Observable
final class ParityDiagnostics {
    enum ModelState: Equatable { case notReady, downloading(Double), compiling, ready(String), failed(String) }

    private(set) var fixture: ParityFixture?
    private(set) var fixtureName: String?
    private(set) var modelState: ModelState = .notReady
    private(set) var running = false
    private(set) var progressDone = 0
    private(set) var progressTotal = 0
    private(set) var report: ParityReport?
    private(set) var reportURL: URL?
    var errorMessage: String?
    var passes = 1
    var checkTokenizer = true
    var units: EmbeddingGemma2Engine.Units = .neuralEngine

    private var location: EmbeddingModelStore.Location?
    private var engine: EmbeddingGemma2Engine?
    private var engineUnits: EmbeddingGemma2Engine.Units?
    private var cancel = CancelFlag()

    static var folder: URL {
        let url = AppFolders.userFiles.appending(path: "Diagnostics", directoryHint: .isDirectory)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    func openFixture(at url: URL) {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        do {
            fixture = try ParityFixture.load(from: url)
            fixtureName = url.lastPathComponent
            report = nil
        } catch { errorMessage = error.localizedDescription }
    }

    var overCapCount: Int {
        guard let f = fixture else { return 0 }
        return (f.chunks + f.queries).filter { $0.tokenIds.count > EmbeddingGemma2Engine.maxTokens }.count
    }

    /// The library's own search engine when it is loaded with the wanted compute units; otherwise one of our own.
    func prepareModel(library: LibraryModel) async {
        if let shared = library.engine, await shared.unitsName == units.rawValue {
            engine = shared; engineUnits = units; modelState = .ready("the app's search model")
            return
        }
        do {
            modelState = .downloading(0)
            let loc = try await EmbeddingModelStore.prepare { fraction in
                Task { @MainActor [weak self] in if case .downloading = self?.modelState { self?.modelState = .downloading(fraction) } }
            }
            location = loc
            modelState = .compiling
            let e = try EmbeddingGemma2Engine(location: loc, units: units)
            let seconds = try await e.warmUp()
            engine = e; engineUnits = units
            modelState = .ready("warm-up \(seconds.formatted(.number.precision(.fractionLength(1)))) s")
        } catch { modelState = .failed(error.localizedDescription) }
    }

    var canRun: Bool {
        guard fixture != nil, !running, engineUnits == units else { return false }
        if case .ready = modelState { return true }
        return false
    }

    func run() async {
        guard let fixture, let engine else { return }
        let ids: [String: [Int]]?
        if checkTokenizer {
            do { ids = try await ParityTokenizer.ids(for: fixture) } catch { errorMessage = "Tokenizer check skipped: \(error.localizedDescription)"; ids = nil }
        } else { ids = nil }
        cancel = CancelFlag()
        running = true; report = nil; progressDone = 0; progressTotal = 0
        let flag = cancel
        let runner = ParityRunner(fixture: fixture, engine: engine, passes: passes)
        let result = await runner.run(tokenizerIds: ids, isCancelled: { flag.isSet }) { done, total in
            Task { @MainActor [weak self] in self?.progressDone = done; self?.progressTotal = total }
        }
        report = result
        running = false
        let url = Self.folder.appending(path: "parity-report-\(Int(Date().timeIntervalSince1970)).json")
        try? result.json().write(to: url)
        reportURL = url
    }

    func stop() { cancel.set() }
}

struct DiagnosticsSettings: View {
    @Environment(AppModel.self) private var model
    @State private var parity = ParityDiagnostics()
    @State private var diff = IngestionDiffDiagnostics()
    @State private var golden = VisualGoldenDiagnostics()
    @State private var gate = CitationGateDiagnostics()
    @State private var importingGate = false
    @State private var importing = false
    @State private var importingDiff = false

    private var library: LibraryModel { model.library }

    var body: some View {
        Form {
            fixtureSection
            modelSection
            runSection
            if parity.running || parity.progressTotal > 0 { progressSection }
            if let report = parity.report { ParityReportSection(report: report, url: parity.reportURL) }
            IngestionDiffSection(diff: diff, library: library, choose: { importingDiff = true })
            CitationGateSection(gate: gate, library: library, choose: { importingGate = true })
            VisualGoldenSection(golden: golden, library: library)
            Section("Logs") {
                HStack {
                    Text("The app's event trace (model loads, answers, memory).").font(.callout).foregroundStyle(.secondary)
                    Spacer()
                    Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([SoakTrace.url]) }
                }
            }
        }
        .formStyle(.grouped)
        .fileImporter(isPresented: $importing, allowedContentTypes: [.json]) { result in
            if case .success(let url) = result { parity.openFixture(at: url) }
        }
        .fileImporter(isPresented: $importingDiff, allowedContentTypes: [.folder, .json]) { result in
            if case .success(let url) = result { diff.open(url) }
        }
        .fileImporter(isPresented: $importingGate, allowedContentTypes: [.json]) { result in
            if case .success(let url) = result { gate.open(url) }
        }
        .alert("Citation gate", isPresented: Binding(get: { gate.errorMessage != nil }, set: { if !$0 { gate.errorMessage = nil } })) {
            Button("OK", role: .cancel) {}
        } message: { Text(gate.errorMessage ?? "") }
        .alert("Something went wrong", isPresented: Binding(get: { parity.errorMessage != nil }, set: { if !$0 { parity.errorMessage = nil } })) {
            Button("OK", role: .cancel) {}
        } message: { Text(parity.errorMessage ?? "") }
        .alert("Visual golden set", isPresented: Binding(get: { golden.errorMessage != nil }, set: { if !$0 { golden.errorMessage = nil } })) {
            Button("OK", role: .cancel) {}
        } message: { Text(golden.errorMessage ?? "") }
    }

    private var fixtureSection: some View {
        Section {
            if let f = parity.fixture {
                LabeledContent("File", value: parity.fixtureName ?? "")
                LabeledContent("Chunks", value: "\(f.chunks.count)")
                LabeledContent("Questions", value: "\(f.queries.count)")
                LabeledContent("Mac model", value: f.mac.modelId.split(separator: "/").last.map(String.init) ?? f.mac.modelId)
                LabeledContent("Over 512 tokens", value: "\(parity.overCapCount)").foregroundStyle(parity.overCapCount > 0 ? .orange : .primary)
            }
            Button(parity.fixture == nil ? "Open fixture…" : "Open a different fixture…") { importing = true }
        } header: { Text("Embedding parity · fixture from the Python Mac") } footer: {
            Text("Made by export_cortex_parity.py on the Mac that hosts your notebooks. This Mac re-embeds every chunk and question and compares, at the phone's bars.")
        }
    }

    private var modelSection: some View {
        Section("Search model") {
            switch parity.modelState {
            case .notReady: Button("Prepare EmbeddingGemma 2") { Task { await parity.prepareModel(library: library) } }
            case .downloading(let p): ProgressView(value: p) { Text("Downloading the text model (about 530 MB)") }
            case .compiling: HStack { ProgressView().controlSize(.small); Text("Compiling for \(parity.units.rawValue). The first time takes a while.").font(.footnote) }
            case .ready(let how): LabeledContent("Ready", value: how)
            case .failed(let why):
                Text(why).foregroundStyle(.red).font(.footnote)
                Button("Try again") { Task { await parity.prepareModel(library: library) } }
            }
            Picker("Compute", selection: Bindable(parity).units) {
                ForEach(EmbeddingGemma2Engine.Units.allCases, id: \.self) { Text($0.rawValue).tag($0) }
            }
            .onChange(of: parity.units) { _, _ in Task { await parity.prepareModel(library: library) } }
        }
    }

    private var runSection: some View {
        Section("Run") {
            Stepper("Passes: \(parity.passes)", value: Bindable(parity).passes, in: 1...10)
            Toggle("Also check this Mac's tokenizer", isOn: Bindable(parity).checkTokenizer)
            Button("Run now") { Task { await parity.run() } }.disabled(!parity.canRun)
        }
    }

    private var progressSection: some View {
        Section("Progress") {
            ProgressView(value: Double(parity.progressDone), total: Double(max(1, parity.progressTotal))) {
                Text(parity.running ? "Running" : "Done")
            } currentValueLabel: { Text("\(parity.progressDone) of \(parity.progressTotal)") }
            if parity.running { Button("Stop", role: .destructive) { parity.stop() } }
        }
    }
}

struct ParityReportSection: View {
    let report: ParityReport
    let url: URL?

    var body: some View {
        Section {
            verdictRow("Mean similarity ≥ \(report.bars.meanCosineMin.formatted())", value: fmt(report.cosine.mean), ok: report.verdict.meanCosine)
            verdictRow("Worst similarity ≥ \(report.bars.worstCosineMin.formatted())", value: fmt(report.cosine.min), ok: report.verdict.worstCosine)
            verdictRow("Best hit ≥ \(pct(report.bars.top1SameMin)) same, top-5 overlap ≥ \(pct(report.bars.top5OverlapMin))",
                       value: "\(pct(report.top1SameRate)) / \(pct(report.top5MeanOverlap))", ok: report.verdict.retrievalAgrees)
            verdictRow("No errors", value: "\(report.errors.count)", ok: report.verdict.noErrors)
            verdictRow("Nothing over the 512-token cap", value: "\(report.skippedOverPhoneCap) skipped", ok: report.verdict.noneOverCap)
        } header: {
            Label(report.verdict.pass ? "Pass" : "Not yet", systemImage: report.verdict.pass ? "checkmark.seal.fill" : "xmark.seal")
                .foregroundStyle(report.verdict.pass ? .green : .orange)
        }
        Section("Details") {
            LabeledContent("Compute", value: report.computeUnits)
            LabeledContent("Embedded", value: "\(report.itemsEmbedded) in \(report.seconds.formatted(.number.precision(.fractionLength(1)))) s")
            LabeledContent("Median ms per token", value: fmt(report.msPerToken, digits: 3))
            LabeledContent("Top-5 identical order", value: pct(report.top5IdenticalRate))
            if report.overCap.count > 0 {
                LabeledContent("Over 512, cut to 512", value: "\(report.overCap.count), longest \(report.overCap.longestTokens)")
                LabeledContent("Cut vs full similarity (mean/min)", value: "\(fmt(report.overCap.truncatedCosine.mean)) / \(fmt(report.overCap.truncatedCosine.min))")
            }
            if let t = report.tokenizer {
                LabeledContent("Tokenizer identical", value: "\(t.identical) of \(t.checked)")
                if let m = t.firstMismatch { Text(m).font(.caption.monospaced()).foregroundStyle(.secondary) }
            }
            ForEach(report.disagreements.prefix(3), id: \.question) { d in
                Text("“\(d.question)” — Mac \(d.mac.joined(separator: ", ")) · here \(d.phone.joined(separator: ", "))").font(.caption).foregroundStyle(.secondary)
            }
            LabeledContent("Device", value: "\(report.device.model), \(report.device.system)")
            ForEach(report.errors.prefix(3), id: \.self) { Text($0).font(.caption).foregroundStyle(.red) }
            if let url {
                HStack {
                    ShareLink("Share report", item: url)
                    Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([url]) }
                }
            }
        }
    }

    private func verdictRow(_ label: String, value: String, ok: Bool) -> some View {
        LabeledContent {
            HStack(spacing: 6) {
                Text(value).monospacedDigit()
                Image(systemName: ok ? "checkmark.circle.fill" : "xmark.circle.fill").foregroundStyle(ok ? .green : .red)
            }
        } label: { Text(label) }
    }

    private func fmt(_ v: Double, digits: Int = 5) -> String { v.isNaN ? "–" : v.formatted(.number.precision(.fractionLength(digits))) }
    private func pct(_ v: Double) -> String { v.isNaN ? "–" : v.formatted(.percent.precision(.fractionLength(0))) }
}

/// The Phase 3 gate's ingestion diff: the old app's `ingestion-fixture.json` beside a folder of documents, read again
/// here and compared file by file (`LibraryModel.runIngestionDiff`).
@MainActor @Observable
final class IngestionDiffDiagnostics {
    private(set) var opened: LibraryModel.IngestionDiffFixture?
    private(set) var running = false
    private(set) var done = 0
    private(set) var total = 0
    private(set) var current = ""
    private(set) var report: IngestionDiff.Report?
    private(set) var reportURL: URL?
    var errorMessage: String?
    private var task: Task<Void, Never>?

    func open(_ url: URL) {
        do {
            opened = try LibraryModel.openIngestionFixture(url)
            report = nil; reportURL = nil
        } catch { errorMessage = error.localizedDescription }
    }

    func run(library: LibraryModel) {
        guard let opened, !running else { return }
        running = true; report = nil; done = 0; total = opened.fixture.files.count
        task = Task { [weak self] in
            let result = await library.runIngestionDiff(opened) { done, total, name in
                self?.done = done; self?.total = total; self?.current = name
            }
            guard let self, !Task.isCancelled else { self?.running = false; return }
            report = result
            let stamp = Int(Date().timeIntervalSince1970)
            let json = ParityDiagnostics.folder.appending(path: "ingestion-diff-\(stamp).json")
            let md = ParityDiagnostics.folder.appending(path: "ingestion-diff-\(stamp).md")
            try? result.json().write(to: json)
            try? result.markdown().data(using: .utf8)?.write(to: md)
            reportURL = md
            running = false
        }
    }

    func stop() { task?.cancel(); running = false }
}

struct IngestionDiffSection: View {
    let diff: IngestionDiffDiagnostics
    let library: LibraryModel
    let choose: () -> Void

    var body: some View {
        Section {
            if let o = diff.opened {
                LabeledContent("Folder", value: o.folder.lastPathComponent)
                LabeledContent("Files", value: "\(o.fixture.files.count)")
                LabeledContent("Old app's token counter", value: o.fixture.tokenCounter)
                LabeledContent("Made", value: o.fixture.createdAt)
            }
            HStack {
                Button(diff.opened == nil ? "Open the fixture's folder…" : "Open a different folder…", action: choose)
                if diff.opened != nil {
                    Button(diff.running ? "Running…" : "Compare now") { diff.run(library: library) }.disabled(diff.running)
                    if diff.running { Button("Stop", role: .destructive) { diff.stop() } }
                }
            }
            if diff.running {
                ProgressView(value: Double(diff.done), total: Double(max(1, diff.total))) { Text(diff.current).lineLimit(1) }
            }
        } header: { Text("Ingestion diff · fixture from the Python Mac") } footer: {
            Text("Made by scripts/ingestion_diff_export.py on the Mac that runs the old app, over a folder of documents or a list of web addresses. Choose the folder that holds ingestion-fixture.json (choosing the file alone lets this app read nothing beside it). This Mac reads the same files from that folder (or the same addresses), cuts them as it would for a notebook, and compares text and chunks. Nothing is added to the library.")
        }
        if let r = diff.report {
            Section {
                let s = r.summary
                verdictRow("Identical chunks", value: "\(s.identical) of \(s.files)", ok: s.identical == s.files - s.failed - s.missing - s.unreadable)
                if s.sameText > 0 { LabeledContent("Same text, cut differently", value: "\(s.sameText)") }
                if s.close > 0 { LabeledContent("Close (≥ 95 % of lines)", value: "\(s.close)") }
                if s.differs > 0 { LabeledContent("Differ", value: "\(s.differs)").foregroundStyle(.red) }
                if s.failed > 0 { LabeledContent("Failed on one side", value: "\(s.failed)").foregroundStyle(.orange) }
                if s.missing > 0 { LabeledContent("Not beside the fixture", value: "\(s.missing)") }
                if s.unreadable > 0 { LabeledContent("No reader here yet", value: "\(s.unreadable)") }
                if !s.identicalRate.isNaN { LabeledContent("Identical rate", value: s.identicalRate.formatted(.percent.precision(.fractionLength(0)))) }
            } header: {
                Label(allIdentical(r) ? "Every file identical" : "Differences to explain", systemImage: allIdentical(r) ? "checkmark.seal.fill" : "doc.text.magnifyingglass")
                    .foregroundStyle(allIdentical(r) ? .green : .orange)
            }
            Section("Files, worst first") {
                ForEach(r.files.prefix(12)) { f in
                    LabeledContent {
                        Text(line(f)).font(.caption).monospacedDigit().foregroundStyle(f.verdict == .identical ? .secondary : .primary)
                    } label: { Text(f.name).lineLimit(1) }
                }
                if r.files.count > 12 { Text("…and \(r.files.count - 12) more in the report.").font(.caption).foregroundStyle(.secondary) }
                if let url = diff.reportURL {
                    HStack {
                        Button("Open report") { NSWorkspace.shared.open(url) }
                        Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([url]) }
                        ShareLink("Share", item: url)
                    }
                }
            }
        }
    }

    private func allIdentical(_ r: IngestionDiff.Report) -> Bool { r.summary.identical == r.summary.files }

    private func line(_ f: IngestionDiff.FileResult) -> String {
        switch f.verdict {
        case .identical: return "identical · \(f.pythonChunks) chunks"
        case .sameText: return "same text · \(f.pythonChunks) vs \(f.swiftChunks) chunks"
        case .close, .differs: return "\(f.textScore.formatted(.percent.precision(.fractionLength(0)))) lines · \(f.chunksMatched) of \(f.pythonChunks) chunks"
        case .failedHere: return "failed here"
        case .failedThere: return "failed in the old app"
        case .missing: return "file missing"
        case .unreadable: return "no reader here"
        }
    }

    private func verdictRow(_ label: String, value: String, ok: Bool) -> some View {
        LabeledContent {
            HStack(spacing: 6) {
                Text(value).monospacedDigit()
                Image(systemName: ok ? "checkmark.circle.fill" : "xmark.circle.fill").foregroundStyle(ok ? .green : .orange)
            }
        } label: { Text(label) }
    }
}
