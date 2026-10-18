import Foundation
import Observation
import Tokenizers

@MainActor @Observable
final class ParityModel {
    enum ModelState: Equatable {
        case notReady
        case downloading(Double)
        case compiling          // first load compiles every function for the Neural Engine
        case ready(warmUpSeconds: Double)
        case failed(String)
    }

    private(set) var fixture: ParityFixture?
    private(set) var fixtureName: String?
    private(set) var modelState: ModelState = .notReady
    private(set) var running = false
    private(set) var runningInBackground = false
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
    private var foregroundCancel = CancelFlag()

    // MARK: fixture

    /// A fixture copied into the app's Documents folder from Finder or the Files app.
    func loadFixtureFromDocuments() {
        let url = URL.documentsDirectory.appending(path: "parity_fixture.json")
        guard FileManager.default.fileExists(atPath: url.path) else { return }
        openFixture(at: url)
    }

    func openFixture(at url: URL) {
        do {
            fixture = try ParityFixture.load(from: url)
            fixtureName = url.lastPathComponent
            report = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    var overCapCount: Int {
        guard let f = fixture else { return 0 }
        return (f.chunks + f.queries).filter { $0.tokenIds.count > EmbeddingGemma2Engine.maxTokens }.count
    }

    // MARK: model

    func prepareModel() async {
        do {
            modelState = .downloading(0)
            let loc = try await EmbeddingModelStore.prepare { fraction in
                Task { @MainActor in
                    if case .downloading = self.modelState { self.modelState = .downloading(fraction) }
                }
            }
            location = loc
            try await makeEngine(at: loc)
        } catch {
            modelState = .failed(error.localizedDescription)
        }
    }

    private func makeEngine(at loc: EmbeddingModelStore.Location) async throws {
        modelState = .compiling
        let e = try EmbeddingGemma2Engine(location: loc, units: units)
        let seconds = try await e.warmUp()
        engine = e
        engineUnits = units
        modelState = .ready(warmUpSeconds: seconds)
    }

    /// Recreate the engine if the compute units changed since it was built.
    private func currentEngine() async throws -> EmbeddingGemma2Engine {
        if let engine, engineUnits == units { return engine }
        guard let location else { throw LabError.model("download the model first") }
        try await makeEngine(at: location)
        guard let engine else { throw LabError.model("engine unavailable") }
        return engine
    }

    // MARK: runs

    var canRun: Bool {
        guard fixture != nil, !running else { return false }
        if case .ready = modelState { return true }
        return false
    }

    func runNow() async {
        guard let fixture else { return }
        do {
            let engine = try await currentEngine()
            let ids = checkTokenizer ? await tokenize(fixture) : nil
            begin(background: false)
            let cancel = foregroundCancel
            let runner = ParityRunner(fixture: fixture, engine: engine, passes: passes)
            let result = await runner.run(tokenizerIds: ids, isCancelled: { cancel.isSet }) { done, total in
                Task { @MainActor in self.progressDone = done; self.progressTotal = total }
            }
            finish(result)
        } catch {
            errorMessage = error.localizedDescription
            running = false
        }
    }

    func runInBackground() async {
        guard let fixture else { return }
        do {
            let engine = try await currentEngine()
            let ids = checkTokenizer ? await tokenize(fixture) : nil
            begin(background: true)
            let runner = ParityRunner(fixture: fixture, engine: engine, passes: passes)
            try BackgroundParity.start(
                runner: runner,
                tokenizerIds: ids,
                progress: { done, total in
                    Task { @MainActor in self.progressDone = done; self.progressTotal = total }
                },
                finished: { report in
                    await MainActor.run { self.finish(report) }
                })
        } catch {
            errorMessage = error.localizedDescription
            running = false
            runningInBackground = false
        }
    }

    func cancel() { foregroundCancel.set() }

    private func begin(background: Bool) {
        foregroundCancel = CancelFlag()
        running = true
        runningInBackground = background
        report = nil
        progressDone = 0
        progressTotal = 0
        errorMessage = nil
    }

    private func finish(_ result: ParityReport) {
        report = result
        running = false
        runningInBackground = false
        let url = FileManager.default.temporaryDirectory
            .appending(path: "parity-report-\(Int(Date().timeIntervalSince1970)).json")
        try? result.json().write(to: url)
        // Keep a copy in Documents so Finder can pull it back to the Mac.
        try? result.json().write(to: URL.documentsDirectory.appending(path: url.lastPathComponent))
        reportURL = url
    }

    /// Mode B: tokenise every string with the phone's tokenizer — the same tokenizer files the
    /// Mac loads — so we can prove new sources will be tokenised identically on the phone.
    private func tokenize(_ fixture: ParityFixture) async -> [String: [Int]]? {
        do {
            let tokenizer = try await AutoTokenizer.from(pretrained: fixture.mac.tokenizerSource)
            var ids: [String: [Int]] = [:]
            for item in fixture.chunks + fixture.queries {
                ids[item.id] = tokenizer.encode(text: item.formattedText)
            }
            return ids
        } catch {
            errorMessage = "Tokenizer check skipped: \(error.localizedDescription)"
            return nil
        }
    }
}
