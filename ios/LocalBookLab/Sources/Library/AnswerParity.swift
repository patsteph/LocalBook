import Accelerate
import Foundation
import Observation
import SwiftUI

/// Phase 1: can the user trust the phone's answer as much as the Mac's? The phone is its own test rig.
/// For each real question (the parity fixture's) it asks the Mac, through the signed phone link in
/// phone-only mode, then answers on its own, and scores how much they agree:
///   · same best source           — the Mac's #1 source is the phone's #1 source
///   · top-5 overlap              — how many of the Mac's sources the phone also found
///   · answer agreement           — cosine of the two answers, embedded by the phone's own model
/// Everything is written to a JSON report; the answers are kept in full so a person (or the Mac's model)
/// can judge whether they say the same thing. Targets are agreed after the first baseline.
struct ParityQuestionResult: Codable, Sendable, Identifiable {
    var id: Int { index }
    let index: Int
    let question: String
    let notebookId: String
    let notebookTitle: String
    let macSources: [SourceRef]
    let phoneSources: [SourceRef]
    let macAnswer: String
    let phoneAnswer: String
    let macSeconds: Double
    let phoneSeconds: Double
    let sameBestSource: Bool
    let sameBestChunk: Bool
    let sourceOverlap: Double          // of the Mac's distinct sources, the share the phone also surfaced
    let chunkOverlap: Double           // same, counting exact chunks
    let answerSimilarity: Double?
    let macLowConfidence: Bool
    var failure: String? = nil

    struct SourceRef: Codable, Sendable, Hashable {
        let sourceId: String
        let chunkIndex: Int
        let filename: String
    }
}

struct ParityAnswersReport: Codable, Sendable {
    let createdAt: String
    let device: DeviceInfo
    let phonePrompt: String
    let results: [ParityQuestionResult]
    let sameBestSourceRate: Double
    let sameBestChunkRate: Double
    let meanSourceOverlap: Double
    let meanChunkOverlap: Double
    let meanAnswerSimilarity: Double
    let completed: Int
    let failed: Int

    func json() -> Data {
        let e = JSONEncoder()
        e.outputFormatting = [.prettyPrinted, .sortedKeys]
        e.keyEncodingStrategy = .convertToSnakeCase
        return (try? e.encode(self)) ?? Data()
    }
}

@MainActor
@Observable
final class AnswerParityModel {
    private(set) var running = false
    private(set) var status = ""
    private(set) var results: [ParityQuestionResult] = []
    private(set) var report: ParityAnswersReport?
    private(set) var reportURL: URL?
    private(set) var questionCount = 0
    private var stop = false

    let library: LibraryModel
    init(library: LibraryModel) { self.library = library }

    func loadQuestions() -> [String] {
        let url = URL.documentsDirectory.appending(path: "parity_fixture.json")
        guard let fixture = try? ParityFixture.load(from: url) else { return [] }
        let marker = "| query: "
        return fixture.queries.compactMap { q in
            guard let r = q.formattedText.range(of: marker) else { return nil }
            return String(q.formattedText[r.upperBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
        }.filter { $0.count >= 8 }
    }

    func requestStop() { stop = true }

    func run(count: Int) async {
        let questions = Array(loadQuestions().prefix(count))
        questionCount = questions.count
        guard !questions.isEmpty else { status = "Put parity_fixture.json in the app's Files folder first (Lab test 1 uses it)."; return }
        guard library.searchState == .ready else { status = "Get the phone's search model ready first."; return }
        running = true; stop = false; results = []; report = nil
        defer { running = false }
        for (i, q) in questions.enumerated() {
            if stop { break }
            status = "Question \(i + 1) of \(questions.count)…"
            results.append(await one(index: i, question: q))
            finish()
        }
        status = stop ? "Stopped after \(results.count)." : "Done: \(results.count) questions."
        finish()
    }

    private func ref(_ h: LibraryHit) -> ParityQuestionResult.SourceRef { .init(sourceId: h.sourceId, chunkIndex: h.chunkIndex, filename: h.filename) }

    private func one(index: Int, question: String) async -> ParityQuestionResult {
        func failed(_ why: String, nb: String = "", title: String = "") -> ParityQuestionResult {
            .init(index: index, question: question, notebookId: nb, notebookTitle: title, macSources: [], phoneSources: [],
                  macAnswer: "", phoneAnswer: "", macSeconds: 0, phoneSeconds: 0, sameBestSource: false, sameBestChunk: false,
                  sourceOverlap: 0, chunkOverlap: 0, answerSimilarity: nil, macLowConfidence: false, failure: why)
        }
        do {
            guard let nb = try await library.bestNotebook(for: question) else { return failed("no matching notebook on the phone") }
            let title = library.notebooks.first { $0.id == nb }?.title ?? nb
            status = "Question \(index + 1): asking the Mac…"
            let mac = try await library.macAnswer(question: question, notebook: nb)
            status = "Question \(index + 1): answering on the phone…"
            let phone = try await library.localAnswer(question: question, notebook: nb)

            let macRefs = mac.answer.citations.map { ParityQuestionResult.SourceRef(sourceId: $0.sourceId, chunkIndex: $0.chunkIndex ?? 0, filename: $0.filename ?? "") }
            let phoneRefs = phone.sources.map(ref)
            let macSources = Set(macRefs.map(\.sourceId)), phoneSources = Set(phoneRefs.map(\.sourceId))
            let macChunks = Set(macRefs.map { "\($0.sourceId)#\($0.chunkIndex)" }), phoneChunks = Set(phoneRefs.map { "\($0.sourceId)#\($0.chunkIndex)" })
            var similarity: Double?
            if !mac.answer.answer.isEmpty, !phone.answer.isEmpty {
                let a = try await library.documentVector(mac.answer.answer), b = try await library.documentVector(phone.answer)
                similarity = Double(zip(a, b).reduce(0) { $0 + $1.0 * $1.1 })
            }
            return .init(
                index: index, question: question, notebookId: nb, notebookTitle: title, macSources: macRefs, phoneSources: phoneRefs,
                macAnswer: mac.answer.answer, phoneAnswer: phone.answer, macSeconds: mac.seconds, phoneSeconds: phone.seconds,
                sameBestSource: macRefs.first?.sourceId != nil && macRefs.first?.sourceId == phoneRefs.first?.sourceId,
                sameBestChunk: macRefs.first.map { "\($0.sourceId)#\($0.chunkIndex)" } == phoneRefs.first.map { "\($0.sourceId)#\($0.chunkIndex)" },
                sourceOverlap: macSources.isEmpty ? 0 : Double(macSources.intersection(phoneSources).count) / Double(macSources.count),
                chunkOverlap: macChunks.isEmpty ? 0 : Double(macChunks.intersection(phoneChunks).count) / Double(macChunks.count),
                answerSimilarity: similarity, macLowConfidence: mac.answer.lowConfidence)
        } catch {
            return failed(error.localizedDescription)
        }
    }

    private func finish() {
        let ok = results.filter { $0.failure == nil }
        func mean(_ xs: [Double]) -> Double { xs.isEmpty ? .nan : xs.reduce(0, +) / Double(xs.count) }
        let r = ParityAnswersReport(
            createdAt: ISO8601DateFormatter().string(from: Date()), device: DeviceInfo.current(),
            phonePrompt: LibraryModel.localInstructions, results: results,
            sameBestSourceRate: mean(ok.map { $0.sameBestSource ? 1 : 0 }), sameBestChunkRate: mean(ok.map { $0.sameBestChunk ? 1 : 0 }),
            meanSourceOverlap: mean(ok.map(\.sourceOverlap)), meanChunkOverlap: mean(ok.map(\.chunkOverlap)),
            meanAnswerSimilarity: mean(ok.compactMap(\.answerSimilarity)), completed: ok.count, failed: results.count - ok.count)
        report = r
        let url = reportURL ?? FileManager.default.temporaryDirectory.appending(path: "answer-parity-\(Int(Date().timeIntervalSince1970)).json")
        try? r.json().write(to: url)
        try? r.json().write(to: URL.documentsDirectory.appending(path: url.lastPathComponent))
        reportURL = url
    }
}

struct AnswerParityView: View {
    @State private var model: AnswerParityModel
    @State private var count = 10
    init(library: LibraryModel) { _model = State(initialValue: AnswerParityModel(library: library)) }

    var body: some View {
        Form {
            Section {
                Stepper("Questions: \(count)", value: $count, in: 3...50, step: 1)
                if model.running {
                    HStack { ProgressView(); Text(model.status).font(.footnote) }
                    Button("Stop", role: .destructive) { model.requestStop() }
                } else {
                    Button("Run: ask the Mac, then answer on the phone") { Task { await model.run(count: count) } }
                    if !model.status.isEmpty { Text(model.status).font(.footnote).foregroundStyle(.secondary) }
                }
            } header: { Text("Mac vs phone") } footer: {
                Text("Needs: Mac app running, library synced and the search model ready (Library screen), and parity_fixture.json in the app's Files folder. Each question takes about a minute. Keep the phone plugged in and the screen on.")
            }
            if let r = model.report {
                Section("Agreement so far (\(r.completed) ok, \(r.failed) failed)") {
                    LabeledContent("Same best source", value: pct(r.sameBestSourceRate))
                    LabeledContent("Same best passage", value: pct(r.sameBestChunkRate))
                    LabeledContent("Mac's sources the phone found", value: pct(r.meanSourceOverlap))
                    LabeledContent("Mac's passages the phone found", value: pct(r.meanChunkOverlap))
                    LabeledContent("Answer similarity", value: String(format: "%.3f", r.meanAnswerSimilarity))
                    if let url = model.reportURL { ShareLink("Share report", item: url) }
                }
            }
            Section("Questions") {
                ForEach(model.results) { q in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(q.question).font(.footnote.weight(.medium)).lineLimit(2)
                        if let why = q.failure { Text(why).font(.caption).foregroundStyle(.red) }
                        else {
                            Text("\(q.notebookTitle) · best source \(q.sameBestSource ? "same" : "different") · sources \(pct(q.sourceOverlap)) · "
                                 + "answers \(q.answerSimilarity.map { String(format: "%.2f", $0) } ?? "–")")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
        .navigationTitle("Answer parity")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func pct(_ v: Double) -> String { v.isNaN ? "–" : "\(Int((v * 100).rounded()))%" }
}
