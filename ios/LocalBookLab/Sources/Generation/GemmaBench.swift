import Foundation
import HuggingFace
import MLX
import MLXHuggingFace
import MLXLLM
import MLXLMCommon
import Observation
import UIKit
import Tokenizers
import os

/// Phase 0 test 2: can the iPhone run the SAME model the Mac runs (Gemma 4 E4B, 4-bit MLX) with
/// room to spare? Pass bars, fixed before any numbers exist:
///   · it loads and finishes every run without being killed for memory
///   · decode ≥ 10 tokens/s
///   · first word ≤ 2 s on a short prompt, ≤ 8 s on a ~1,000-token prompt (model already loaded)
///   · the phone is not "serious" or "critical" thermally after all runs
struct GemmaBars: Codable, Sendable {
    var decodeTokensPerSecondMin = 10.0
    var firstWordShortSecondsMax = 2.0
    var firstWordLongSecondsMax = 8.0
}

struct GemmaRun: Codable, Sendable, Identifiable {
    var id: String { label }
    let label: String
    let promptTokens: Int
    let generatedTokens: Int
    let firstWordSeconds: Double
    let promptSeconds: Double
    let decodeTokensPerSecond: Double
    let peakFootprintMB: Double
    let thermalBefore: String
    let thermalAfter: String
    let stopReason: String
    var inBackground: Bool? = nil
}

struct LockMidwayResult: Codable, Sendable {
    let tokensGenerated: Int
    let secondsRunning: Double
    let stoppedWhenAppLeftForeground: Bool
    let secondsUntilStopped: Double?
    let finishedNormally: Bool
}

struct SearchModelMemory: Codable, Sendable {
    let footprintMB: Double
    let availableMB: Double
    /// Steady-state speed: the first call (which loads and compiles for the Neural Engine) is
    /// timed separately in `coldFirstEmbedSeconds` and left out of this.
    let embedMillisecondsPerToken: Double
    var coldFirstEmbedSeconds: Double? = nil
}

struct GemmaReport: Codable, Sendable {
    let createdAt: String
    let device: DeviceInfo
    let model: String
    let loadSeconds: Double
    let footprintAfterLoadMB: Double
    let availableMemoryAfterLoadMB: Double
    let runs: [GemmaRun]
    /// Informational, not pass/fail: a ~3,000-token prompt, then six back-to-back generations.
    var stressRuns: [GemmaRun]? = nil
    /// Memory once the search model (EmbeddingGemma on the Neural Engine) is loaded beside Gemma.
    var withSearchModel: SearchModelMemory? = nil
    /// Locking the phone mid-answer: did generation stop cleanly and the app stay responsive?
    var lockMidway: LockMidwayResult? = nil
    let bars: GemmaBars
    let verdict: Verdict
    struct Verdict: Codable, Sendable {
        let completed: Bool, decode: Bool, firstWordShort: Bool, firstWordLong: Bool, thermal: Bool
        var pass: Bool { completed && decode && firstWordShort && firstWordLong && thermal }
    }

    func json() -> Data {
        let e = JSONEncoder()
        e.outputFormatting = [.prettyPrinted, .sortedKeys]
        e.keyEncodingStrategy = .convertToSnakeCase
        return (try? e.encode(self)) ?? Data()
    }
}

/// Memory the OS charges this process (what jetsam looks at), in MB.
enum MemoryProbe {
    static func footprintMB() -> Double {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<natural_t>.size)
        let kr = withUnsafeMutablePointer(to: &info) {
            $0.withMemoryRebound(to: integer_t.self, capacity: Int(count)) {
                task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), $0, &count)
            }
        }
        return kr == KERN_SUCCESS ? Double(info.phys_footprint) / 1_048_576 : .nan
    }
    static func availableMB() -> Double { Double(os_proc_available_memory()) / 1_048_576 }
}

@MainActor
@Observable
final class GemmaBenchModel {
    enum State: Equatable { case notLoaded, downloading(Double), loading, ready, running(String), failed(String) }

    private(set) var state: State = .notLoaded
    private(set) var runs: [GemmaRun] = []
    private(set) var report: GemmaReport?
    private(set) var reportURL: URL?
    private(set) var stressRuns: [GemmaRun] = []
    private(set) var withSearchModel: SearchModelMemory?
    private(set) var lockMidway: LockMidwayResult?
    private var cancelFlag = CancelFlag()
    func stop() { cancelFlag.set() }
    private(set) var liveText = ""
    private(set) var loadSeconds = 0.0
    private(set) var footprintAfterLoadMB = 0.0
    private(set) var availableAfterLoadMB = 0.0
    private var container: ModelContainer?

    static let configuration = LLMRegistry.gemma4_e4b_it_4bit

    func load() async {
        guard container == nil else { return }
        state = .downloading(0)
        let started = Date()
        do {
            // Keep MLX's buffer cache small: on a phone it is memory the app cannot spare.
            Memory.cacheLimit = 64 * 1024 * 1024
            let c = try await #huggingFaceLoadModelContainer(configuration: Self.configuration) { [weak self] progress in
                let fraction = progress.fractionCompleted
                Task { @MainActor in
                    if case .downloading = self?.state { self?.state = .downloading(fraction) }
                }
            }
            state = .loading
            // Touch the weights once so the load cost is paid here, not in the first measured run.
            let warm = ChatSession(c, generateParameters: GenerateParameters(maxTokens: 4))
            _ = try await warm.respond(to: "Hi")
            container = c
            loadSeconds = Date().timeIntervalSince(started)
            footprintAfterLoadMB = MemoryProbe.footprintMB()
            availableAfterLoadMB = MemoryProbe.availableMB()
            state = .ready
        } catch {
            state = .failed("\(error.localizedDescription)")
        }
    }

    /// A prompt of roughly `approxTokens` tokens of ordinary prose, then a question about it.
    private static func prompt(approxTokens: Int) -> String {
        guard approxTokens > 0 else { return "In two sentences, why do people keep notes on what they read?" }
        let para = "The team reviewed the quarterly plan and agreed that onboarding should stay under two weeks. "
            + "They noted that local search quality depends on chunk size, that sync between devices must never "
            + "lose edits, and that private notes stay on the device. Several risks were raised about memory use "
            + "on phones, battery life during long tasks, and keeping answers consistent with the desktop app. "
        let count = max(1, approxTokens / 80)
        return String(repeating: para, count: count) + "\n\nIn three bullet points, summarise the risks above."
    }

    func runAll() async {
        guard let container else { return }
        runs = []; report = nil
        let plan: [(String, Int)] = [("short", 0), ("medium (~500 tokens)", 500), ("long (~1,000 tokens)", 1000),
                                     ("short, again", 0), ("long, again", 1000)]
        for (label, size) in plan {
            state = .running(label)
            if let run = await measure(label: label, text: Self.prompt(approxTokens: size), on: container) {
                runs.append(run)
            } else { return }
        }
        finish()
    }

    /// The questions the 5 runs cannot answer: does a long prompt blow the memory budget, and does
    /// speed hold when the phone keeps generating? (Informational, not pass/fail.)
    func runStress() async {
        guard let container else { return }
        stressRuns = []
        state = .running("very long (~3,000 tokens)")
        if let r = await measure(label: "very long (~3,000 tokens)", text: Self.prompt(approxTokens: 3500), on: container) {
            stressRuns.append(r)
        } else { return }
        for i in 1...6 {
            state = .running("sustained \(i) of 6")
            guard let r = await measure(label: "sustained \(i)", text: Self.prompt(approxTokens: 500), on: container,
                                        maxTokens: 220) else { return }
            stressRuns.append(r)
        }
        rewriteReport()
        state = .ready
    }

    /// What happens when the phone is locked in the middle of an answer? Gemma runs on the GPU and
    /// iOS refuses GPU work once the app leaves the foreground: a first test inside a background task
    /// failed ("Task failed") and then left the app frozen. So the app must stop generating the moment
    /// it is no longer active. This starts a long answer and cancels it on willResignActive; the
    /// result says how fast it stopped and whether the app stayed responsive.
    func runLockMidway() async {
        guard let container else { return }
        lockMidway = nil
        state = .running("Answering — lock the phone within 5 seconds")
        let session = ChatSession(container, generateParameters: GenerateParameters(maxTokens: 400))
        let prompt = Self.prompt(approxTokens: 500)
        let started = Date()
        var tokens = 0
        var stoppedAfter: Double?
        let task = Task { @MainActor in
            for try await item in session.streamDetails(to: prompt) {
                if case .chunk = item { tokens += 1 }
            }
        }
        let observer = NotificationCenter.default.addObserver(
            forName: UIApplication.willResignActiveNotification, object: nil, queue: .main) { _ in
            MainActor.assumeIsolated {
                if stoppedAfter == nil { stoppedAfter = Date().timeIntervalSince(started) }
                task.cancel()
            }
        }
        var finishedNormally = false
        do { try await task.value; finishedNormally = true } catch {}
        NotificationCenter.default.removeObserver(observer)
        lockMidway = LockMidwayResult(
            tokensGenerated: tokens, secondsRunning: Date().timeIntervalSince(started),
            stoppedWhenAppLeftForeground: stoppedAfter != nil, secondsUntilStopped: stoppedAfter,
            finishedNormally: finishedNormally)
        rewriteReport()
        state = .ready
    }

    /// Load the search model beside Gemma — the state the real app lives in — and re-measure memory.
    func loadSearchModelToo() async {
        guard container != nil else { return }
        state = .running("loading the search model")
        do {
            let location = try await EmbeddingModelStore.prepare(progress: { _ in })
            let engine = try EmbeddingGemma2Engine(location: location)
            let tokens = [2] + Array(repeating: 1000, count: 200) + [1]
            let c0 = Date()
            _ = try await engine.embed(tokenIds: tokens)                 // cold: loads + compiles
            let cold = Date().timeIntervalSince(c0)
            let t0 = Date()
            for _ in 0..<5 { _ = try await engine.embed(tokenIds: tokens) }
            let perToken = Date().timeIntervalSince(t0) / (5 * Double(tokens.count)) * 1000
            withSearchModel = SearchModelMemory(footprintMB: MemoryProbe.footprintMB(),
                                                availableMB: MemoryProbe.availableMB(),
                                                embedMillisecondsPerToken: perToken,
                                                coldFirstEmbedSeconds: cold)
            keepAlive = engine          // stay resident, as it would in the app
            rewriteReport()
            state = .ready
        } catch {
            state = .failed("search model: \(error.localizedDescription)")
        }
    }
    private var keepAlive: EmbeddingGemma2Engine?

    private func measure(label: String, text: String, on container: ModelContainer, maxTokens: Int = 160) async -> GemmaRun? {
        let session = ChatSession(container, generateParameters: GenerateParameters(maxTokens: maxTokens))
        let before = ProcessInfo.processInfo.thermalState.name
        var peak = MemoryProbe.footprintMB()
        let sampler = Task.detached {
            var p = 0.0
            while !Task.isCancelled { p = max(p, MemoryProbe.footprintMB()); try? await Task.sleep(for: .milliseconds(100)) }
            return p
        }
        let started = Date()
        var firstWord: Double?
        var info: GenerateCompletionInfo?
        liveText = ""
        do {
            for try await item in session.streamDetails(to: text) {
                switch item {
                case .chunk(let s):
                    if firstWord == nil { firstWord = Date().timeIntervalSince(started) }
                    liveText += s
                case .info(let i): info = i
                default: break
                }
            }
        } catch {
            sampler.cancel()
            state = .failed("\(label): \(error.localizedDescription)")
            return nil
        }
        sampler.cancel()
        peak = max(peak, await sampler.value)
        guard let info else { state = .failed("\(label): no completion info"); return nil }
        return GemmaRun(label: label, promptTokens: info.promptTokenCount, generatedTokens: info.generationTokenCount,
                        firstWordSeconds: firstWord ?? .nan, promptSeconds: info.promptTime,
                        decodeTokensPerSecond: info.tokensPerSecond, peakFootprintMB: peak,
                        thermalBefore: before, thermalAfter: ProcessInfo.processInfo.thermalState.name,
                        stopReason: "\(info.stopReason)")
    }

    private func rewriteReport() {
        guard var r = report else { return }
        r.stressRuns = stressRuns.isEmpty ? nil : stressRuns
        r.withSearchModel = withSearchModel
        r.lockMidway = lockMidway
        report = r
        let url = reportURL ?? FileManager.default.temporaryDirectory
            .appending(path: "gemma-report-\(Int(Date().timeIntervalSince1970)).json")
        try? r.json().write(to: url)
        try? r.json().write(to: URL.documentsDirectory.appending(path: url.lastPathComponent))
        reportURL = url
    }

    private func finish() {
        let bars = GemmaBars()
        let shorts = runs.filter { $0.label.hasPrefix("short") }
        let longs = runs.filter { $0.label.hasPrefix("long") }
        let last = ProcessInfo.processInfo.thermalState
        let r = GemmaReport(
            createdAt: ISO8601DateFormatter().string(from: Date()), device: DeviceInfo.current(),
            model: Self.configuration.name, loadSeconds: loadSeconds,
            footprintAfterLoadMB: footprintAfterLoadMB, availableMemoryAfterLoadMB: availableAfterLoadMB,
            runs: runs, bars: bars,
            verdict: .init(
                completed: runs.count == 5,
                decode: runs.allSatisfy { $0.decodeTokensPerSecond >= bars.decodeTokensPerSecondMin },
                firstWordShort: shorts.allSatisfy { $0.firstWordSeconds <= bars.firstWordShortSecondsMax },
                firstWordLong: longs.allSatisfy { $0.firstWordSeconds <= bars.firstWordLongSecondsMax },
                thermal: last != .serious && last != .critical))
        report = r
        let url = FileManager.default.temporaryDirectory
            .appending(path: "gemma-report-\(Int(Date().timeIntervalSince1970)).json")
        try? r.json().write(to: url)
        try? r.json().write(to: URL.documentsDirectory.appending(path: url.lastPathComponent))
        reportURL = url
        state = .ready
    }
}

extension ProcessInfo.ThermalState {
    var name: String {
        switch self {
        case .nominal: "nominal"
        case .fair: "fair"
        case .serious: "serious"
        case .critical: "critical"
        @unknown default: "unknown"
        }
    }
}
