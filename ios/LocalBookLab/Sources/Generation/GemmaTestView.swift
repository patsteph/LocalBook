import SwiftUI

struct GemmaTestView: View {
    @State private var model = GemmaBenchModel()

    var body: some View {
        Form {
            Section {
                switch model.state {
                case .notLoaded:
                    Button("Download and load Gemma 4 E4B") { Task { await model.load() } }
                case .downloading(let p):
                    ProgressView(value: p) { Text("Downloading (about 3 GB)…") }
                case .loading:
                    HStack { ProgressView(); Text("Loading into memory…") }
                case .ready:
                    LabeledContent("Loaded in", value: "\(fmt(model.loadSeconds)) s")
                    LabeledContent("Memory after load", value: "\(Int(model.footprintAfterLoadMB)) MB")
                    LabeledContent("Memory still available", value: "\(Int(model.availableAfterLoadMB)) MB")
                    Button("Run the 5 measurements") { Task { await model.runAll() } }
                case .running(let label):
                    HStack { ProgressView(); Text("Running: \(label)") }
                    if !model.liveText.isEmpty {
                        Text(model.liveText).font(.footnote).foregroundStyle(.secondary).lineLimit(6)
                    }
                case .failed(let why):
                    Text(why).foregroundStyle(.red)
                    Button("Try again") { Task { await model.load() } }
                }
            } header: {
                Text("Model")
            } footer: {
                Text("mlx-community/gemma-4-e4b-it-4bit — the same model the Mac runs. Keep the phone plugged in and the screen on.")
            }
            if !model.runs.isEmpty {
                Section("Runs") {
                    ForEach(model.runs) { r in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(r.label).font(.body.weight(.medium))
                            Text("First word \(fmt(r.firstWordSeconds)) s · \(fmt(r.decodeTokensPerSecond)) tok/s · "
                                 + "prompt \(r.promptTokens) tok · peak \(Int(r.peakFootprintMB)) MB · \(r.thermalAfter)")
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            if let report = model.report {
                Section {
                    row("Finished all 5 runs", report.verdict.completed, "\(report.runs.count) of 5")
                    row("Decode ≥ \(fmt(report.bars.decodeTokensPerSecondMin)) tok/s", report.verdict.decode,
                        "slowest \(fmt(report.runs.map(\.decodeTokensPerSecond).min() ?? .nan))")
                    row("First word ≤ \(fmt(report.bars.firstWordShortSecondsMax)) s (short)", report.verdict.firstWordShort,
                        fmt(report.runs.filter { $0.label.hasPrefix("short") }.map(\.firstWordSeconds).max() ?? .nan))
                    row("First word ≤ \(fmt(report.bars.firstWordLongSecondsMax)) s (~1,000 tokens)", report.verdict.firstWordLong,
                        fmt(report.runs.filter { $0.label.hasPrefix("long") }.map(\.firstWordSeconds).max() ?? .nan))
                    row("Not hot at the end", report.verdict.thermal, report.runs.last?.thermalAfter ?? "?")
                    if let url = model.reportURL { ShareLink("Share report", item: url) }
                } header: { Text(report.verdict.pass ? "Pass" : "Not yet") }
                Section {
                    Button("Stress: 3,000-token prompt, then 6 generations in a row") { Task { await model.runStress() } }
                    Button("Load the search model beside Gemma, re-measure memory") { Task { await model.loadSearchModelToo() } }
                } header: { Text("Next questions") } footer: {
                    Text("Run both, one after the other, then share the report again. Keep the phone plugged in.")
                }
                if !model.stressRuns.isEmpty {
                    Section("Stress runs") {
                        ForEach(model.stressRuns) { r in
                            Text("\(r.label): first word \(fmt(r.firstWordSeconds)) s · \(fmt(r.decodeTokensPerSecond)) tok/s · peak \(Int(r.peakFootprintMB)) MB · \(r.thermalAfter)")
                                .font(.footnote)
                        }
                    }
                }
                if let m = model.withSearchModel {
                    Section("With the search model loaded") {
                        LabeledContent("Memory used", value: "\(Int(m.footprintMB)) MB")
                        LabeledContent("Still available", value: "\(Int(m.availableMB)) MB")
                        LabeledContent("Embed speed", value: "\(fmt(m.embedMillisecondsPerToken)) ms/token")
                    }
                }
            }
        }
        .navigationTitle("Gemma 4 E4B")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func row(_ title: String, _ ok: Bool, _ value: String) -> some View {
        HStack {
            Image(systemName: ok ? "checkmark.circle.fill" : "xmark.circle.fill").foregroundStyle(ok ? .green : .red)
            Text(title); Spacer(); Text(value).foregroundStyle(.secondary)
        }
    }

    private func fmt(_ v: Double) -> String { v.isNaN ? "–" : v.formatted(.number.precision(.fractionLength(1))) }
}
