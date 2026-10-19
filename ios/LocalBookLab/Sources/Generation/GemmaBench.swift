import Foundation
import HuggingFace
import MLX
import MLXHuggingFace
import MLXLLM
import MLXLMCommon
import Observation
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
}

struct GemmaReport: Codable, Sendable {
    let createdAt: String
    let device: DeviceInfo
    let model: String
    let loadSeconds: Double
    let footprintAfterLoadMB: Double
    let availableMemoryAfterLoadMB: Double
    let runs: [GemmaRun]
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
            let c = try await #huggingFaceLoadModelContainer(configuration: Self.configuration) { progress in
                Task { @MainActor [weak self] in
                    if case .downloading = self?.state { self?.state = .downloading(progress.fractionCompleted) }
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

    private func measure(label: String, text: String, on container: ModelContainer) async -> GemmaRun? {
        let session = ChatSession(container, generateParameters: GenerateParameters(maxTokens: 160))
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
