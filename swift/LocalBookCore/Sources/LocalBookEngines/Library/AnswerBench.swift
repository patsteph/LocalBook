import Foundation
import LocalBookCore
import MLXLMCommon

/// A short speed test of the loaded answer model: a fixed prompt, a fixed answer length, the numbers that matter when
/// choosing a model (first word, words per second, memory). The Evaluator in the Mac's LLM Studio runs this and keeps
/// the results so two models can be compared on the same Mac.
public struct AnswerBenchResult: Codable, Sendable, Identifiable, Hashable {
    public var id = UUID()
    public let date: Date
    public let model: String
    public let device: String
    public let promptTokens: Int
    public let generatedTokens: Int
    public let firstWordSeconds: Double
    public let promptTokensPerSecond: Double
    public let tokensPerSecond: Double
    public let peakFootprintMB: Double
    public let stopReason: String
}

public enum AnswerBench {
    /// Roughly `approxTokens` tokens of ordinary prose, then a question about it: the shape of a real answer.
    public static func prompt(approxTokens: Int) -> String {
        guard approxTokens > 0 else { return "In two sentences, why do people keep notes on what they read?" }
        let para = "The team reviewed the quarterly plan and agreed that onboarding should stay under two weeks. "
            + "They noted that local search quality depends on chunk size, that sync between devices must never "
            + "lose edits, and that private notes stay on the device. Several risks were raised about memory use "
            + "on phones, battery life during long tasks, and keeping answers consistent with the desktop app. "
        return String(repeating: para, count: max(1, approxTokens / 80)) + "\n\nIn three bullet points, summarise the risks above."
    }

    /// One measured generation. `maxTokens` caps the answer so every run is comparable.
    public static func run(_ container: ModelContainer, model: String, promptTokens approx: Int = 500, maxTokens: Int = 160) async throws -> AnswerBenchResult {
        let session = ChatSession(container, generateParameters: GenerateParameters(maxTokens: maxTokens, temperature: 0))
        let text = prompt(approxTokens: approx)
        let peak = PeakMemory()
        let sampler = Task.detached { [peak] in
            while !Task.isCancelled { peak.sample(); try? await Task.sleep(for: .milliseconds(100)) }
        }
        defer { sampler.cancel() }
        let started = Date()
        var firstWord: Double?
        var info: GenerateCompletionInfo?
        for try await item in session.streamDetails(to: text) {
            switch item {
            case .chunk: if firstWord == nil { firstWord = Date().timeIntervalSince(started) }
            case .info(let i): info = i
            default: break
            }
        }
        guard let info else { throw LabError.model("the model produced no completion information") }
        let reason: String
        switch info.stopReason { case .stop: reason = "stop"; case .length: reason = "length"; case .cancelled: reason = "cancelled"; default: reason = "other" }
        return AnswerBenchResult(
            date: Date(), model: model, device: await DeviceInfo.current().model,
            promptTokens: info.promptTokenCount, generatedTokens: info.generationTokenCount,
            firstWordSeconds: firstWord ?? .nan,
            promptTokensPerSecond: info.promptTime > 0 ? Double(info.promptTokenCount) / info.promptTime : .nan,
            tokensPerSecond: info.generateTime > 0 ? Double(info.generationTokenCount) / info.generateTime : .nan,
            peakFootprintMB: peak.value, stopReason: reason)
    }

    // MARK: history

    private static var historyURL: URL {
        AppFolders.userFiles.appending(path: "Diagnostics", directoryHint: .isDirectory).appending(path: "answer-bench.json")
    }

    public static func history() -> [AnswerBenchResult] {
        guard let data = try? Data(contentsOf: historyURL) else { return [] }
        let d = JSONDecoder(); d.dateDecodingStrategy = .iso8601
        return (try? d.decode([AnswerBenchResult].self, from: data)) ?? []
    }

    public static func remember(_ result: AnswerBenchResult) {
        var all = history()
        all.insert(result, at: 0)
        all = Array(all.prefix(200))
        try? FileManager.default.createDirectory(at: historyURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        let e = JSONEncoder(); e.dateEncodingStrategy = .iso8601; e.outputFormatting = [.prettyPrinted, .sortedKeys]
        if let data = try? e.encode(all) { try? data.write(to: historyURL, options: .atomic) }
    }

    public static func forget(_ id: UUID) {
        let all = history().filter { $0.id != id }
        let e = JSONEncoder(); e.dateEncodingStrategy = .iso8601; e.outputFormatting = [.prettyPrinted, .sortedKeys]
        if let data = try? e.encode(all) { try? data.write(to: historyURL, options: .atomic) }
    }
}

/// The highest footprint seen while sampling.
final class PeakMemory: @unchecked Sendable {
    private let lock = NSLock()
    private var peak = MemoryProbe.footprintMB()
    func sample() { let now = MemoryProbe.footprintMB(); lock.withLock { peak = max(peak, now) } }
    var value: Double { lock.withLock { peak } }
}
