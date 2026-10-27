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

    /// The questions in the app's own copy of the fixture, or the real reason there are none.
    func readQuestions() -> (questions: [String], problem: String?) {
        let url = URL.documentsDirectory.appending(path: "parity_fixture.json")
        guard FileManager.default.fileExists(atPath: url.path) else {
            return ([], "The app has no copy of parity_fixture.json yet. Tap “Choose parity_fixture.json…” and pick the file from the Mac.")
        }
        do {
            let fixture = try ParityFixture.load(from: url)
            let marker = "| query: "
            let qs = fixture.queries.compactMap { q -> String? in
                guard let r = q.formattedText.range(of: marker) else { return nil }
                return String(q.formattedText[r.upperBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
            }.filter { $0.count >= 8 }
            return qs.isEmpty ? ([], "The fixture loaded but holds no questions (\(fixture.queries.count) queries, none with a “| query:” part).") : (qs, nil)
        } catch {
            return ([], "The saved fixture cannot be read — probably an old export. Regenerate it on the Mac and choose it again. Details: \(Self.describe(error))")
        }
    }

    func loadQuestions() -> [String] { readQuestions().questions }

    private static func describe(_ error: Error) -> String {
        if let d = error as? DecodingError {
            switch d {
            case .keyNotFound(let k, let c): return "missing “\(k.stringValue)” at \(c.codingPath.map(\.stringValue).joined(separator: "."))"
            case .typeMismatch(_, let c), .valueNotFound(_, let c), .dataCorrupted(let c): return "\(c.debugDescription) at \(c.codingPath.map(\.stringValue).joined(separator: "."))"
            @unknown default: break
            }
        }
        return error.localizedDescription
    }

    func requestStop() { stop = true }

    var availableQuestions: Int { readQuestions().questions.count }
    var fixtureProblem: String? { readQuestions().problem }

    /// Copy a picked parity_fixture.json into the app's own folder, so the next run finds it by itself.
    func importFixture(_ url: URL) {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        do {
            let data = try Data(contentsOf: url)
            _ = try ParityFixture.decoder.decode(ParityFixture.self, from: data)       // must be a real fixture
            try data.write(to: URL.documentsDirectory.appending(path: "parity_fixture.json"), options: .atomic)
            status = fixtureProblem ?? "Loaded \(availableQuestions) questions from \(url.lastPathComponent)."
        } catch {
            status = "That file is not a parity fixture: \(error.localizedDescription)"
        }
    }

    func run(count: Int) async {
        let read = readQuestions()
        let questions = Array(read.questions.prefix(count))
        questionCount = questions.count
        guard !questions.isEmpty else { status = read.problem ?? "No questions found."; return }
        guard library.searchState == .ready else { status = "Get the phone's search model ready first (Library screen)."; return }
        guard MacLink.paired != nil else { status = "Pair with a Mac first (row 3)."; return }
        guard library.chunkCount > 0 else { status = "The phone has no library yet: sync it first (Library screen)."; return }
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
    @State private var importing = false
    init(library: LibraryModel) { _model = State(initialValue: AnswerParityModel(library: library)) }

    var body: some View {
        Form {
            Section {
                Stepper("Questions: \(count)", value: $count, in: 3...50, step: 1)
                if model.running {
                    HStack { ProgressView(); Text(model.status).font(.footnote) }
                    Button("Stop", role: .destructive) { model.requestStop() }
                } else {
                    Button("Choose parity_fixture.json…") { importing = true }
                    Button("Run: ask the Mac, then answer on the phone") { Task { await model.run(count: count) } }
                    if !model.status.isEmpty { Text(model.status).font(.footnote).foregroundStyle(.secondary) }
                }
            } header: { Text("Mac vs phone") } footer: {
                Text((model.fixtureProblem.map { "Questions: \($0)\n\n" } ?? "Questions: \(model.availableQuestions) found.\n\n")
                     + "Needs the Mac app running, the library synced and the search model ready (Library screen). Each question takes about a minute. Keep the phone plugged in and the screen on.")
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
        .fileImporter(isPresented: $importing, allowedContentTypes: [.json]) { result in
            if case .success(let url) = result { model.importFixture(url) }
        }
        .navigationTitle("Answer parity")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func pct(_ v: Double) -> String { v.isNaN ? "–" : "\(Int((v * 100).rounded()))%" }
}
