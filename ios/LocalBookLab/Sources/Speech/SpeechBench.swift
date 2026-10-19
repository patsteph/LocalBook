import AVFoundation
import BackgroundTasks
import FluidAudio
import Foundation
import Observation

/// Phase 0 test 4: speech while the screen is locked. Pass bars, fixed before any numbers:
///   · reading aloud: every chunk is ready before the previous one finishes (no silent gaps), and
///     the chunks voiced while the app was in the background are counted and complete
///   · speech generation runs at least 1.5× real time (so a locked phone can stay ahead of itself)
///   · transcription runs at least 10× real time, with ≤ 5% of words wrong against the text
///   · the same holds for passes that ran in the background under a continued-processing task
struct SpeechBars: Codable, Sendable {
    var speechRealtimeFactorMin = 1.5
    var transcribeRealtimeFactorMin = 10.0
    var wordErrorRateMax = 0.05
}

struct SpeechChunkRecord: Codable, Sendable, Identifiable {
    var id: Int { index }
    let index: Int
    let audioSeconds: Double
    let synthSeconds: Double
    let realtimeFactor: Double
    let leadSeconds: Double?        // playback only: audio still queued when this chunk arrived
    let inBackground: Bool
}

struct SpeechPhase: Codable, Sendable, Identifiable {
    var id: String { name }
    let name: String
    let kind: String               // "speak" | "transcribe"
    let audioSeconds: Double
    let workSeconds: Double
    let realtimeFactor: Double
    let chunks: [SpeechChunkRecord]
    let wordErrorRate: Double?
    let passes: Int
    let passesInBackground: Int
    let underruns: Int
    let peakFootprintMB: Double
    let thermalEnd: String
    let note: String
}

struct SpeechReport: Codable, Sendable {
    let createdAt: String
    let device: DeviceInfo
    let loadSeconds: Double
    let footprintAfterLoadMB: Double
    let availableAfterLoadMB: Double
    let phases: [SpeechPhase]
    let bars: SpeechBars
    let verdict: Verdict
    struct Verdict: Codable, Sendable {
        let speechFast: Bool, noGaps: Bool, backgroundWorked: Bool, transcribeFast: Bool, accurate: Bool
        var pass: Bool { speechFast && noGaps && backgroundWorked && transcribeFast && accurate }
    }
    func json() -> Data {
        let e = JSONEncoder()
        e.outputFormatting = [.prettyPrinted, .sortedKeys]
        e.keyEncodingStrategy = .convertToSnakeCase
        return (try? e.encode(self)) ?? Data()
    }
}

@MainActor
@Observable
final class SpeechBenchModel {
    enum State: Equatable { case notLoaded, loading(String), ready, running(String), failed(String) }

    private(set) var state: State = .notLoaded
    private(set) var phases: [SpeechPhase] = []
    private(set) var report: SpeechReport?
    private(set) var reportURL: URL?
    private(set) var heard = ""
    private(set) var progressNote = ""
    private(set) var hasAudio = false
    private var loadSeconds = 0.0
    private var footprintAfterLoad = 0.0
    private var availableAfterLoad = 0.0

    private var kokoro: KokoroAneManager?
    private var asr: AsrManager?
    private var audio: [Float] = []
    private var audioRate = KokoroAneConstants.sampleRate
    private let bars = SpeechBars()

    // MARK: load

    func load() async {
        guard kokoro == nil else { return }
        let started = Date()
        do {
            state = .loading("Speech models (Kokoro)…")
            let k = KokoroAneManager(version: .v3)
            try await k.initialize()
            state = .loading("Transcription model (Parakeet)…")
            let models = try await AsrModels.downloadAndLoad(version: .v3)
            let a = AsrManager(config: .default)
            try await a.loadModels(models)
            kokoro = k; asr = a
            loadSeconds = Date().timeIntervalSince(started)
            footprintAfterLoad = MemoryProbe.footprintMB()
            availableAfterLoad = MemoryProbe.availableMB()
            state = .ready
        } catch { state = .failed(error.localizedDescription) }
    }

    // MARK: 1. read aloud (screen may lock: audio background mode)

    func readAloud() async {
        guard let kokoro else { return }
        state = .running("Reading aloud — lock the screen now")
        let player = ChunkPlayer(sampleRate: Double(KokoroAneConstants.sampleRate))
        let sampler = MemorySampler()
        do {
            try player.start()
            var records: [SpeechChunkRecord] = []
            var all: [Float] = []
            var underruns = 0
            var backgroundChunks = 0
            for (i, text) in SpeechCorpus.chunks.enumerated() {
                progressNote = "Chunk \(i + 1) of \(SpeechCorpus.chunks.count)"
                let t0 = Date()
                let result = try await kokoro.synthesizeDetailed(text: text)
                let synth = Date().timeIntervalSince(t0)
                let seconds = Double(result.samples.count) / Double(result.sampleRate)
                let lead = player.schedule(result.samples)
                let bg = AppStateTracker.shared.isInBackground
                if bg { backgroundChunks += 1 }
                if i > 0 && lead <= 0.05 { underruns += 1 }
                audioRate = result.sampleRate
                all += result.samples
                records.append(.init(index: i, audioSeconds: seconds, synthSeconds: synth, realtimeFactor: seconds / synth,
                                     leadSeconds: lead, inBackground: bg))
            }
            // Let the last chunk finish so a stall near the end is not hidden.
            while player.remaining > 0.1 { try await Task.sleep(for: .milliseconds(200)) }
            player.stop()
            audio = all; hasAudio = true
            phases.removeAll { $0.name == "Read aloud" }
            phases.append(phase(name: "Read aloud", kind: "speak", chunks: records, underruns: underruns,
                                backgroundChunks: backgroundChunks, peak: await sampler.stop(),
                                note: "Played through the speaker; screen locked during \(backgroundChunks) chunk(s)."))
            rebuildReport()
            state = .ready
        } catch {
            player.stop(); _ = await sampler.stop()
            state = .failed("Reading aloud: \(error.localizedDescription)")
        }
    }

    // MARK: 2. speech generation under a continued-processing task (no sound)

    func speakInBackground(repeats: Int) async {
        guard let kokoro else { return }
        state = .running("Generating speech in the background — lock the screen")
        let sampler = MemorySampler()
        let box = ResultBox<[SpeechChunkRecord]>()
        let total = Int64(repeats * SpeechCorpus.chunks.count)
        do {
            try await SpeechBackground.run(title: "LocalBook speech test", subtitle: "Voicing text on the Neural Engine",
                                           totalUnits: total) { report, cancel in
                var done: Int64 = 0
                var records: [SpeechChunkRecord] = []
                for _ in 0..<repeats {
                    for text in SpeechCorpus.chunks {
                        if cancel.isSet { return }
                        let t0 = Date()
                        guard let r = try? await kokoro.synthesizeDetailed(text: text) else { return }
                        let synth = Date().timeIntervalSince(t0)
                        let seconds = Double(r.samples.count) / Double(r.sampleRate)
                        let bg = await MainActor.run { AppStateTracker.shared.isInBackground }
                        records.append(.init(index: records.count, audioSeconds: seconds, synthSeconds: synth,
                                             realtimeFactor: seconds / synth, leadSeconds: nil, inBackground: bg))
                        done += 1
                        report(done)
                    }
                }
                box.set(records)
            }
            let records = try await box.wait(timeout: 900)
            phases.removeAll { $0.name == "Speak in background" }
            phases.append(phase(name: "Speak in background", kind: "speak", chunks: records, underruns: 0,
                                backgroundChunks: records.filter(\.inBackground).count, peak: await sampler.stop(),
                                note: "Continued-processing task, \(repeats) pass(es) over the text, no playback."))
            rebuildReport()
            state = .ready
        } catch {
            _ = await sampler.stop()
            state = .failed("Background speech: \(error.localizedDescription)")
        }
    }

    // MARK: 3. transcription (foreground, then under a continued-processing task)

    func transcribe(background: Bool, repeats: Int) async {
        guard let asr, hasAudio else { state = .failed("Read aloud first — that makes the audio to transcribe."); return }
        let samples = SpeechMath.resampleTo16k(audio, from: audioRate)
        let audioSeconds = Double(samples.count) / 16_000
        let reference = SpeechCorpus.fullText
        let name = background ? "Transcribe in background" : "Transcribe"
        state = .running(background ? "Transcribing in the background — lock the screen" : "Transcribing…")
        let sampler = MemorySampler()
        let passes = background ? repeats : 1
        let box = ResultBox<TranscribeOutcome>()
        let work: @Sendable (@Sendable (Int64) -> Void, CancelFlag) async -> Void = { report, cancel in
            var outcome = TranscribeOutcome()
            for i in 0..<passes {
                if cancel.isSet { break }
                var decoder = TdtDecoderState.make()
                let t0 = Date()
                guard let result = try? await asr.transcribe(samples, decoderState: &decoder) else { break }
                let secs = Date().timeIntervalSince(t0)
                let bg = await MainActor.run { AppStateTracker.shared.isInBackground }
                outcome.seconds.append(secs)
                if bg { outcome.inBackground += 1 }
                outcome.text = result.text
                report(Int64(i + 1))
            }
            box.set(outcome)
        }
        do {
            if background {
                try await SpeechBackground.run(title: "LocalBook speech test", subtitle: "Transcribing on the Neural Engine",
                                               totalUnits: Int64(passes), work: work)
            } else {
                await work({ _ in }, CancelFlag())
            }
            let outcome = try await box.wait(timeout: 900)
            guard !outcome.seconds.isEmpty else { throw LabError.model("no pass finished") }
            heard = outcome.text
            let total = outcome.seconds.reduce(0, +)
            let wer = SpeechMath.wordErrorRate(reference: reference, heard: outcome.text)
            phases.removeAll { $0.name == name }
            phases.append(SpeechPhase(
                name: name, kind: "transcribe", audioSeconds: audioSeconds * Double(outcome.seconds.count),
                workSeconds: total, realtimeFactor: audioSeconds * Double(outcome.seconds.count) / total,
                chunks: [], wordErrorRate: wer, passes: outcome.seconds.count, passesInBackground: outcome.inBackground,
                underruns: 0, peakFootprintMB: await sampler.stop(), thermalEnd: ProcessInfo.processInfo.thermalState.name,
                note: "\(outcome.seconds.count) pass(es) over \(Int(audioSeconds)) s of audio."))
            rebuildReport()
            state = .ready
        } catch {
            _ = await sampler.stop()
            state = .failed("\(name): \(error.localizedDescription)")
        }
    }

    // MARK: report

    private func phase(name: String, kind: String, chunks: [SpeechChunkRecord], underruns: Int,
                       backgroundChunks: Int, peak: Double, note: String) -> SpeechPhase {
        let audioSeconds = chunks.map(\.audioSeconds).reduce(0, +)
        let work = chunks.map(\.synthSeconds).reduce(0, +)
        return SpeechPhase(name: name, kind: kind, audioSeconds: audioSeconds, workSeconds: work,
                           realtimeFactor: work > 0 ? audioSeconds / work : .nan, chunks: chunks, wordErrorRate: nil,
                           passes: 1, passesInBackground: backgroundChunks, underruns: underruns,
                           peakFootprintMB: peak, thermalEnd: ProcessInfo.processInfo.thermalState.name, note: note)
    }

    private func rebuildReport() {
        let speak = phases.filter { $0.kind == "speak" }
        let trans = phases.filter { $0.kind == "transcribe" }
        let bgPhases = phases.filter { $0.name.contains("background") }
        let verdict = SpeechReport.Verdict(
            speechFast: !speak.isEmpty && speak.allSatisfy { $0.realtimeFactor >= bars.speechRealtimeFactorMin },
            noGaps: speak.allSatisfy { $0.underruns == 0 },
            backgroundWorked: !bgPhases.isEmpty && bgPhases.allSatisfy { $0.passesInBackground > 0 }
                && (phases.first { $0.name == "Read aloud" }.map { $0.passesInBackground > 0 } ?? true),
            transcribeFast: !trans.isEmpty && trans.allSatisfy { $0.realtimeFactor >= bars.transcribeRealtimeFactorMin },
            accurate: !trans.isEmpty && trans.allSatisfy { ($0.wordErrorRate ?? 1) <= bars.wordErrorRateMax })
        let r = SpeechReport(createdAt: ISO8601DateFormatter().string(from: Date()), device: DeviceInfo.current(),
                             loadSeconds: loadSeconds, footprintAfterLoadMB: footprintAfterLoad,
                             availableAfterLoadMB: availableAfterLoad, phases: phases, bars: bars, verdict: verdict)
        report = r
        let url = reportURL ?? FileManager.default.temporaryDirectory
            .appending(path: "speech-report-\(Int(Date().timeIntervalSince1970)).json")
        try? r.json().write(to: url)
        try? r.json().write(to: URL.documentsDirectory.appending(path: url.lastPathComponent))
        reportURL = url
    }
}

struct TranscribeOutcome: Sendable {
    var seconds: [Double] = []
    var inBackground = 0
    var text = ""
}

/// Hands one result from a background task back to the caller that is waiting for it.
final class ResultBox<T: Sendable>: @unchecked Sendable {
    private let lock = NSLock()
    private var value: T?
    func set(_ v: T) { lock.withLock { value = v } }
    func wait(timeout: Double) async throws -> T {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if let v = lock.withLock({ value }) { return v }
            try await Task.sleep(for: .milliseconds(250))
        }
        throw LabError.background("timed out waiting for the background task")
    }
}

/// Samples the process memory footprint while a phase runs and reports the peak.
final class MemorySampler: @unchecked Sendable {
    private var task: Task<Double, Never>?
    init() {
        task = Task.detached {
            var peak = 0.0
            while !Task.isCancelled { peak = max(peak, MemoryProbe.footprintMB()); try? await Task.sleep(for: .milliseconds(150)) }
            return peak
        }
    }
    func stop() async -> Double {
        task?.cancel()
        return await task?.value ?? 0
    }
}

/// A user-started continued-processing task, like the parity test's, for any long job.
enum SpeechBackground {
    static let prefix = "com.patsteph.localbook.lab.speech"

    static func run(title: String, subtitle: String, totalUnits: Int64,
                    work: @escaping @Sendable (@Sendable (Int64) -> Void, CancelFlag) async -> Void) async throws {
        let identifier = "\(prefix).\(UUID().uuidString.prefix(8).lowercased())"
        let cancel = CancelFlag()
        let registered = BGTaskScheduler.shared.register(forTaskWithIdentifier: identifier, using: nil) { task in
            guard let continued = task as? BGContinuedProcessingTask else {
                task.setTaskCompleted(success: false)
                return
            }
            let box = UncheckedBox(continued)
            box.value.progress.totalUnitCount = max(1, totalUnits)
            box.value.progress.completedUnitCount = 0
            let job = Task {
                await work({ done in box.value.progress.completedUnitCount = done }, cancel)
                box.value.setTaskCompleted(success: !cancel.isSet)
            }
            box.value.expirationHandler = {
                cancel.set()
                job.cancel()
            }
        }
        guard registered else { throw LabError.background("could not register \(identifier)") }
        let request = BGContinuedProcessingTaskRequest(identifier: identifier, title: title, subtitle: subtitle)
        request.strategy = .fail
        do { try await BGTaskScheduler.shared.submitTaskRequest(request) }
        catch { throw LabError.background("submit failed: \(error.localizedDescription)") }
    }
}
