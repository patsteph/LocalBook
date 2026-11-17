import AppKit
import LocalBookCore
import LocalBookEngines
import SwiftUI

/// Settings › Diagnostics › Citation gate: the old app's `citation-fixture.json` (`scripts/citation_gate_export.py`)
/// asked again of this Mac's migrated library (`LibraryModel.runCitationGate`), with the 95 % bar.
@MainActor @Observable
final class CitationGateDiagnostics {
    private(set) var fixture: CitationGate.Fixture?
    private(set) var fixtureName = ""
    private(set) var running = false
    private(set) var done = 0
    private(set) var total = 0
    private(set) var current = ""
    private(set) var report: CitationGate.Report?
    private(set) var reportURL: URL?
    var errorMessage: String?
    private var task: Task<Void, Never>?

    func open(_ url: URL) {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        do {
            fixture = try CitationGate.Fixture.load(try Data(contentsOf: url))
            fixtureName = url.lastPathComponent
            report = nil; reportURL = nil
        } catch { errorMessage = error.localizedDescription }
    }

    func run(library: LibraryModel) {
        guard let fixture, !running else { return }
        running = true; report = nil; done = 0; total = fixture.questions.count
        task = Task { [weak self] in
            let result = await library.runCitationGate(fixture) { done, total, question in
                self?.done = done; self?.total = total; self?.current = question
            }
            guard let self, !Task.isCancelled else { self?.running = false; return }
            report = result
            let stamp = Int(Date().timeIntervalSince1970)
            let json = ParityDiagnostics.folder.appending(path: "citation-gate-\(stamp).json")
            let md = ParityDiagnostics.folder.appending(path: "citation-gate-\(stamp).md")
            try? result.json().write(to: json)
            try? result.markdown().data(using: .utf8)?.write(to: md)
            reportURL = md
            running = false
        }
    }

    func stop() { task?.cancel(); running = false }
}

struct CitationGateSection: View {
    let gate: CitationGateDiagnostics
    let library: LibraryModel
    let choose: () -> Void

    var body: some View {
        Section {
            if let f = gate.fixture {
                LabeledContent("Fixture", value: gate.fixtureName)
                LabeledContent("Questions", value: "\(f.questions.count) over \(Set(f.questions.map(\.notebookId)).count) notebooks")
                LabeledContent("Old app", value: "\(f.mac.device) · \(f.mac.embedder) · reranker \(f.mac.reranker)")
                LabeledContent("Made", value: f.createdAt)
            }
            HStack {
                Button(gate.fixture == nil ? "Open fixture…" : "Open a different fixture…", action: choose)
                if gate.fixture != nil {
                    Button(gate.running ? "Asking…" : "Compare now") { gate.run(library: library) }.disabled(gate.running)
                    if gate.running { Button("Stop", role: .destructive) { gate.stop() } }
                }
            }
            if gate.running {
                ProgressView(value: Double(gate.done), total: Double(max(1, gate.total))) { Text(gate.current).lineLimit(1) }
            }
        } header: { Text("Citation gate · fixture from the Python Mac") } footer: {
            Text("Made by scripts/citation_gate_export.py on the Mac that runs the old app: the questions you asked in each notebook and what the old app cited for them. After Move My Library Here, this Mac asks the same questions of its own copy (retrieval only, no answer) and compares citation by citation. The Phase 3 bar is 95 % cited the same.")
        }
        if let r = gate.report {
            let s = r.summary
            Section {
                LabeledContent("Cited the same") {
                    Label(s.sameRate.isNaN ? "–" : s.sameRate.formatted(.percent.precision(.fractionLength(0))), systemImage: s.passes ? "checkmark.circle.fill" : "xmark.circle.fill")
                        .foregroundStyle(s.passes ? .green : .red)
                }
                LabeledContent("Identical order", value: "\(s.identical) of \(s.compared)")
                if s.sameSet > 0 { LabeledContent("Same set, other order", value: "\(s.sameSet)") }
                if s.overlap > 0 { LabeledContent("Overlap", value: "\(s.overlap)").foregroundStyle(.orange) }
                if s.different > 0 { LabeledContent("Different", value: "\(s.different)").foregroundStyle(.red) }
                if s.missing > 0 { LabeledContent("Notebook not on this Mac", value: "\(s.missing)") }
                if s.failed > 0 { LabeledContent("Failed here", value: "\(s.failed)").foregroundStyle(.orange) }
            } header: {
                Label(s.passes ? "Gate passes · bar \(Int(CitationGate.bar * 100)) %" : "Below the bar · \(Int(CitationGate.bar * 100)) %",
                      systemImage: s.passes ? "checkmark.seal.fill" : "doc.text.magnifyingglass")
                    .foregroundStyle(s.passes ? .green : .orange)
            }
            Section("Questions, worst first") {
                ForEach(r.results.prefix(12)) { q in
                    LabeledContent {
                        Text("\(q.verdict.rawValue) · \(q.theirs.count) there, \(q.ours.count) here, \(q.shared) shared")
                            .font(.caption).monospacedDigit().foregroundStyle(q.verdict == .identical || q.verdict == .sameSet ? .secondary : .primary)
                    } label: { Text("\(q.notebookTitle): \(q.question)").lineLimit(1) }
                }
                if r.results.count > 12 { Text("…and \(r.results.count - 12) more in the report.").font(.caption).foregroundStyle(.secondary) }
                if let url = gate.reportURL {
                    HStack {
                        Button("Open report") { NSWorkspace.shared.open(url) }
                        Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([url]) }
                    }
                }
            }
        }
    }
}
