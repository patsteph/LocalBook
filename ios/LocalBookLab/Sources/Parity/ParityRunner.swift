import Foundation
import UIKit

/// Runs test 1: re-embed the Mac's chunks and questions on this iPhone and compare.
///
/// Mode A (always): the phone embeds the Mac's OWN token ids, so any difference is the
/// model runtime alone (Neural Engine float16 vs MLX bf16).
/// Mode B (optional): the phone's tokenizer re-tokenises the same strings; ids are compared
/// one by one, which proves the phone will format and tokenise new sources identically.
struct ParityRunner: Sendable {
    let fixture: ParityFixture
    let engine: EmbeddingGemma2Engine
    var passes: Int = 1                 // >1 to keep a background run going long enough to lock the phone

    struct Bars: Codable, Sendable {
        var meanCosineMin = 0.999
        var worstCosineMin = 0.998
        // Exact top-5 order is not a fair bar: at cosine 0.9999 the phone and Mac still swap
        // near-tied neighbours. Retrieval must agree on the best hit and on the set of five.
        var top1SameMin = 0.95
        var top5OverlapMin = 0.90
    }

    /// Progress: (done, total). Called from the runner's task; hop to the main actor yourself.
    func run(tokenizerIds: [String: [Int]]? = nil,
             isCancelled: @escaping @Sendable () -> Bool,
             progress: @escaping @Sendable (Int, Int) -> Void) async -> ParityReport {
        let tracker = await AppStateTracker.shared
        let started = Date()
        let chunks = fixture.chunks.filter { $0.tokenIds.count <= EmbeddingGemma2Engine.maxTokens }
        let queries = fixture.queries.filter { $0.tokenIds.count <= EmbeddingGemma2Engine.maxTokens }
        let overCapItems = (fixture.chunks + fixture.queries).filter { $0.tokenIds.count > EmbeddingGemma2Engine.maxTokens }
        let skipped = overCapItems.count
        let total = (chunks.count + queries.count) * max(1, passes) + overCapItems.count

        var errors: [String] = []
        var done = 0
        var embeddedInBackground = 0
        var phoneChunks: [String: [Float]] = [:]
        var phoneQueries: [String: [Float]] = [:]
        var tokenSeconds: [Double] = []

        func embedAll(_ items: [ParityFixture.Item], into store: inout [String: [Float]]) async {
            for item in items {
                if isCancelled() { return }
                let t0 = Date()
                do {
                    store[item.id] = try await engine.embed(tokenIds: item.tokenIds)
                    let dt = Date().timeIntervalSince(t0)
                    tokenSeconds.append(dt / Double(item.tokenIds.count))
                    if await tracker.isInBackground { embeddedInBackground += 1 }
                } catch {
                    if errors.count < 20 { errors.append("\(item.id): \(error.localizedDescription)") }
                }
                done += 1
                progress(done, total)
            }
        }

        for _ in 0..<max(1, passes) {
            if isCancelled() { break }
            await embedAll(chunks, into: &phoneChunks)
            await embedAll(queries, into: &phoneQueries)
        }
        // Over-cap items are not part of the pass/fail comparison. We still embed them cut to the
        // phone's limit (as a 512-token contract would) so the cost of truncation is measured,
        // not guessed: cosine of the phone's truncated vector against the Mac's full-length one.
        var truncatedCos: [Double] = []
        for item in overCapItems {
            if isCancelled() { break }
            let cap = EmbeddingGemma2Engine.maxTokens
            let cut = Array(item.tokenIds.prefix(cap - 1)) + [item.tokenIds.last!]
            do {
                truncatedCos.append(VectorMath.cosine(try await engine.embed(tokenIds: cut), item.vector))
            } catch {
                if errors.count < 20 { errors.append("\(item.id) (truncated): \(error.localizedDescription)") }
            }
            done += 1
            progress(done, total)
        }
        let longestOverCap = overCapItems.map(\.tokenIds.count).max() ?? 0
        let seconds = Date().timeIntervalSince(started)
        // A stopped or expired run must never read as a pass on partial results.
        if isCancelled() { errors.append("Cancelled after \(done) of \(total) items") }

        // ── vector agreement ──────────────────────────────────────────────────
        let chunkCos = chunks.compactMap { c in phoneChunks[c.id].map { VectorMath.cosine($0, c.vector) } }
        let queryCos = queries.compactMap { q in phoneQueries[q.id].map { VectorMath.cosine($0, q.vector) } }
        let allCos = chunkCos + queryCos

        // ── retrieval agreement: rank the same eligible corpus both ways ─────
        let ranked = chunks.filter { phoneChunks[$0.id] != nil }
        let macCorpus = ranked.map(\.vector)
        let phoneCorpus = ranked.compactMap { phoneChunks[$0.id] }
        var identical = 0, top1Same = 0, overlapSum = 0.0, compared = 0
        var disagreements: [ParityReport.Disagreement] = []
        for q in queries {
            guard let pq = phoneQueries[q.id], !ranked.isEmpty else { continue }
            let mac = VectorMath.topK(q.vector, in: macCorpus, k: 5).map { ranked[$0].id }
            let phone = VectorMath.topK(pq, in: phoneCorpus, k: 5).map { ranked[$0].id }
            compared += 1
            if mac == phone { identical += 1 }
            if mac.first == phone.first { top1Same += 1 }
            overlapSum += Double(Set(mac).intersection(phone).count) / Double(max(1, mac.count))
            if mac != phone, disagreements.count < 10 {
                disagreements.append(.init(question: String(q.formattedText.prefix(120)), mac: mac, phone: phone))
            }
        }

        // ── tokenizer agreement (mode B) ──────────────────────────────────────
        var tokenizer: ParityReport.TokenizerCheck?
        if let tokenizerIds {
            let items = fixture.chunks + fixture.queries
            var same = 0, checked = 0
            var firstMismatch: String?
            for item in items {
                guard let phone = tokenizerIds[item.id] else { continue }
                checked += 1
                let macFull = item.tokenIds   // the Mac's ids, truncated at its max length
                // Truncation keeps the closing special token, so compare a truncated item on
                // everything but its last id, and on the untruncated length.
                let truncated = macFull.count < item.tokenCountFull
                let ok = truncated
                    ? phone.count == item.tokenCountFull
                        && phone.prefix(macFull.count - 1).elementsEqual(macFull.prefix(macFull.count - 1))
                    : phone == macFull
                if ok {
                    same += 1
                } else if firstMismatch == nil {
                    let common = min(phone.count, macFull.count)
                    let i = (0..<common).first { phone[$0] != macFull[$0] } ?? common
                    firstMismatch = "\(item.id) at token \(i): phone \(phone.dropFirst(max(0, i - 2)).prefix(5)) vs Mac \(macFull.dropFirst(max(0, i - 2)).prefix(5))"
                }
            }
            tokenizer = .init(checked: checked, identical: same, firstMismatch: firstMismatch)
        }

        let bars = Bars()
        let stats = ParityReport.Stats(values: allCos)
        let top5Rate = compared > 0 ? Double(identical) / Double(compared) : .nan
        let top1Rate = compared > 0 ? Double(top1Same) / Double(compared) : .nan
        let overlapRate = compared > 0 ? overlapSum / Double(compared) : .nan
        let device = await DeviceInfo.current()
        return ParityReport(
            createdAt: ISO8601DateFormatter().string(from: Date()),
            device: device,
            macModel: fixture.mac.modelId,
            computeUnits: await engine.unitsName,
            passes: max(1, passes),
            itemsEmbedded: phoneChunks.count + phoneQueries.count,
            skippedOverPhoneCap: skipped,
            embeddedWhileInBackground: embeddedInBackground,
            seconds: seconds,
            msPerToken: tokenSeconds.isEmpty ? .nan : tokenSeconds.sorted()[tokenSeconds.count / 2] * 1000,
            cosine: stats,
            chunkCosine: .init(values: chunkCos),
            queryCosine: .init(values: queryCos),
            questionsCompared: compared,
            top5IdenticalRate: top5Rate,
            top1SameRate: top1Rate,
            top5MeanOverlap: overlapRate,
            disagreements: disagreements,
            overCap: .init(count: overCapItems.count, longestTokens: longestOverCap,
                           truncatedCosine: .init(values: truncatedCos)),
            tokenizer: tokenizer,
            errors: errors,
            bars: bars,
            verdict: ParityReport.Verdict(
                meanCosine: stats.mean >= bars.meanCosineMin,
                worstCosine: stats.min >= bars.worstCosineMin,
                retrievalAgrees: top1Rate >= bars.top1SameMin && overlapRate >= bars.top5OverlapMin,
                noneOverCap: skipped == 0,
                noErrors: errors.isEmpty
            )
        )
    }
}

struct ParityReport: Codable, Sendable {
    struct Stats: Codable, Sendable {
        var n = 0, mean = Double.nan, min = Double.nan, p1 = Double.nan, p50 = Double.nan
        init(values: [Double]) {
            let s = values.filter { !$0.isNaN }.sorted()
            n = s.count
            guard !s.isEmpty else { return }
            mean = s.reduce(0, +) / Double(s.count)
            min = s[0]
            p1 = s[Int((Double(s.count - 1) * 0.01).rounded())]
            p50 = s[Int((Double(s.count - 1) * 0.5).rounded())]
        }
    }
    struct Disagreement: Codable, Sendable { let question: String; let mac: [String]; let phone: [String] }
    struct OverCap: Codable, Sendable { let count: Int; let longestTokens: Int; let truncatedCosine: Stats }
    struct TokenizerCheck: Codable, Sendable { let checked: Int; let identical: Int; let firstMismatch: String? }
    struct Verdict: Codable, Sendable {
        let meanCosine: Bool, worstCosine: Bool, retrievalAgrees: Bool, noneOverCap: Bool, noErrors: Bool
        var pass: Bool { meanCosine && worstCosine && retrievalAgrees && noErrors && noneOverCap }
    }

    let createdAt: String
    let device: DeviceInfo
    let macModel: String
    let computeUnits: String
    let passes: Int
    let itemsEmbedded: Int
    let skippedOverPhoneCap: Int
    let embeddedWhileInBackground: Int
    let seconds: Double
    let msPerToken: Double
    let cosine: Stats
    let chunkCosine: Stats
    let queryCosine: Stats
    let questionsCompared: Int
    let top5IdenticalRate: Double
    let top1SameRate: Double
    let top5MeanOverlap: Double
    let disagreements: [Disagreement]
    let overCap: OverCap
    let tokenizer: TokenizerCheck?
    let errors: [String]
    let bars: ParityRunner.Bars
    let verdict: Verdict

    func json() -> Data {
        let e = JSONEncoder()
        e.outputFormatting = [.prettyPrinted, .sortedKeys]
        e.keyEncodingStrategy = .convertToSnakeCase
        e.nonConformingFloatEncodingStrategy = .convertToString(positiveInfinity: "inf", negativeInfinity: "-inf", nan: "nan")
        return (try? e.encode(self)) ?? Data()
    }
}

struct DeviceInfo: Codable, Sendable {
    let model: String
    let system: String
    let memoryGB: Double
    let thermalState: String

    @MainActor static func current() -> DeviceInfo {
        var sys = utsname()
        uname(&sys)
        let machine = withUnsafeBytes(of: &sys.machine) { raw in
            String(decoding: raw.prefix { $0 != 0 }, as: UTF8.self)
        }
        let thermal: String = switch ProcessInfo.processInfo.thermalState {
        case .nominal: "nominal"
        case .fair: "fair"
        case .serious: "serious"
        case .critical: "critical"
        @unknown default: "unknown"
        }
        return DeviceInfo(model: machine,
                          system: "\(UIDevice.current.systemName) \(UIDevice.current.systemVersion)",
                          memoryGB: (Double(ProcessInfo.processInfo.physicalMemory) / 1_073_741_824 * 10).rounded() / 10,
                          thermalState: thermal)
    }
}

/// Whether the app is in the background right now, so the report can say how much work the
/// Neural Engine did behind a locked screen.
@MainActor final class AppStateTracker {
    static let shared = AppStateTracker()
    private(set) var isInBackground = false
    private var observers: [NSObjectProtocol] = []

    private init() {
        let nc = NotificationCenter.default
        observers.append(nc.addObserver(forName: UIApplication.didEnterBackgroundNotification, object: nil, queue: .main) { _ in
            MainActor.assumeIsolated { AppStateTracker.shared.isInBackground = true }
        })
        observers.append(nc.addObserver(forName: UIApplication.willEnterForegroundNotification, object: nil, queue: .main) { _ in
            MainActor.assumeIsolated { AppStateTracker.shared.isInBackground = false }
        })
    }
}
