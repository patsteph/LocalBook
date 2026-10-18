import SwiftUI
import UniformTypeIdentifiers

struct ParityTestView: View {
    @State private var model = ParityModel()
    @State private var importing = false

    var body: some View {
        Form {
            fixtureSection
            modelSection
            runSection
            if model.running || model.progressTotal > 0 { progressSection }
            if let report = model.report { ReportSection(report: report, url: model.reportURL) }
        }
        .navigationTitle("Embedding parity")
        .navigationBarTitleDisplayMode(.inline)
        .fileImporter(isPresented: $importing, allowedContentTypes: [.json]) { result in
            if case .success(let url) = result { model.openFixture(at: url) }
        }
        .alert("Something went wrong",
               isPresented: Binding(get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })) {
            Button("OK", role: .cancel) {}
        } message: {
            Text(model.errorMessage ?? "")
        }
        .task { model.loadFixtureFromDocuments() }
    }

    // MARK: sections

    private var fixtureSection: some View {
        Section {
            if let f = model.fixture {
                LabeledContent("Chunks", value: "\(f.chunks.count)")
                LabeledContent("Questions", value: "\(f.queries.count)")
                LabeledContent("Mac model", value: f.mac.modelId.split(separator: "/").last.map(String.init) ?? f.mac.modelId)
                LabeledContent("BOS / EOS", value: "\(f.mac.hasBos ? "yes" : "no") / \(f.mac.hasEos ? "yes" : "no")")
                LabeledContent("Over 512 tokens", value: "\(model.overCapCount)")
                    .foregroundStyle(model.overCapCount > 0 ? .orange : .primary)
            }
            Button(model.fixture == nil ? "Open fixture…" : "Open a different fixture…") { importing = true }
        } header: {
            Text("Fixture from the Mac")
        } footer: {
            Text("Made by export_cortex_parity.py. AirDrop it here, or copy parity_fixture.json into LocalBook Lab from Finder.")
        }
    }

    private var modelSection: some View {
        Section {
            switch model.modelState {
            case .notReady:
                Button("Download and prepare EmbeddingGemma 2") { Task { await model.prepareModel() } }
            case .downloading(let p):
                ProgressView(value: p) { Text("Downloading text model (about 530 MB)") }
            case .compiling:
                HStack {
                    ProgressView()
                    Text("Compiling for the Neural Engine. The first time can take several minutes.")
                        .font(.footnote)
                }
            case .ready(let s):
                LabeledContent("Ready", value: "warm-up \(s.formatted(.number.precision(.fractionLength(1)))) s")
            case .failed(let message):
                Text(message).foregroundStyle(.red).font(.footnote)
                Button("Try again") { Task { await model.prepareModel() } }
            }
            Picker("Compute", selection: $model.units) {
                ForEach(EmbeddingGemma2Engine.Units.allCases, id: \.self) { Text($0.rawValue).tag($0) }
            }
        } header: {
            Text("Model")
        } footer: {
            Text("Neural Engine is the configuration under test. GPU and CPU are there to isolate a failure.")
        }
    }

    private var runSection: some View {
        Section {
            Stepper("Passes: \(model.passes)", value: $model.passes, in: 1...10)
            Toggle("Also check the phone's tokenizer", isOn: $model.checkTokenizer)
            Button("Run now") { Task { await model.runNow() } }
                .disabled(!model.canRun)
            Button("Run in background, then lock the phone") { Task { await model.runInBackground() } }
                .disabled(!model.canRun)
        } header: {
            Text("Run")
        } footer: {
            Text("For the locked-screen run, set 3 or more passes, start it, then lock the phone. The report counts how many items finished while the app was in the background.")
        }
    }

    private var progressSection: some View {
        Section("Progress") {
            ProgressView(value: Double(model.progressDone), total: Double(max(1, model.progressTotal))) {
                Text(model.runningInBackground ? "Running as a background task" : "Running")
            } currentValueLabel: {
                Text("\(model.progressDone) of \(model.progressTotal)")
            }
            if model.running && !model.runningInBackground {
                Button("Stop", role: .destructive) { model.cancel() }
            }
        }
    }
}

private struct ReportSection: View {
    let report: ParityReport
    let url: URL?

    var body: some View {
        Section {
            verdictRow("Mean similarity ≥ \(report.bars.meanCosineMin.formatted())",
                       value: fmt(report.cosine.mean), ok: report.verdict.meanCosine)
            verdictRow("Worst similarity ≥ \(report.bars.worstCosineMin.formatted())",
                       value: fmt(report.cosine.min), ok: report.verdict.worstCosine)
            verdictRow("Same top-5 for every question",
                       value: pct(report.top5IdenticalRate), ok: report.verdict.top5Identical)
            verdictRow("No errors", value: "\(report.errors.count)", ok: report.verdict.noErrors)
            verdictRow("Nothing over the 512-token cap",
                       value: "\(report.skippedOverPhoneCap) skipped", ok: report.verdict.noneOverCap)
        } header: {
            Text(report.verdict.pass ? "Pass" : "Not yet")
        }
        Section("Details") {
            LabeledContent("Compute", value: report.computeUnits)
            LabeledContent("Embedded", value: "\(report.itemsEmbedded) in \(report.seconds.formatted(.number.precision(.fractionLength(1)))) s")
            LabeledContent("While in background", value: "\(report.embeddedWhileInBackground)")
            LabeledContent("Median ms per token", value: fmt(report.msPerToken, digits: 3))
            LabeledContent("Top-1 the same", value: pct(report.top1SameRate))
            LabeledContent("Top-5 overlap", value: pct(report.top5MeanOverlap))
            if let t = report.tokenizer {
                LabeledContent("Tokenizer identical", value: "\(t.identical) of \(t.checked)")
                if let m = t.firstMismatch { Text(m).font(.caption.monospaced()).foregroundStyle(.secondary) }
            }
            LabeledContent("Device", value: "\(report.device.model), \(report.device.system)")
            LabeledContent("Thermal state", value: report.device.thermalState)
            ForEach(report.errors.prefix(3), id: \.self) { Text($0).font(.caption).foregroundStyle(.red) }
            if let url { ShareLink("Share report", item: url) }
        }
    }

    private func verdictRow(_ label: String, value: String, ok: Bool) -> some View {
        LabeledContent {
            HStack(spacing: 6) {
                Text(value).monospacedDigit()
                Image(systemName: ok ? "checkmark.circle.fill" : "xmark.circle.fill")
                    .foregroundStyle(ok ? .green : .red)
            }
        } label: { Text(label) }
    }

    private func fmt(_ v: Double, digits: Int = 5) -> String {
        v.isNaN ? "–" : v.formatted(.number.precision(.fractionLength(digits)))
    }

    private func pct(_ v: Double) -> String {
        v.isNaN ? "–" : v.formatted(.percent.precision(.fractionLength(0)))
    }
}
