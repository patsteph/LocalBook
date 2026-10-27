import CoreML
import Foundation
import Tokenizers

/// The Mac's reranker on the phone: ms-marco-MiniLM-L-12-v2 (the same model the Mac's FlashRank runs),
/// converted to Core ML (tools/convert_reranker.py) and run on the Neural Engine. Same tokenizer files,
/// same 256-token limit, same sigmoid on the score. The Mac's copy is an int8-quantised ONNX file and this
/// one is half precision, so scores agree closely rather than exactly (measured on 600 real pairs).
actor RerankerEngine {
    static let batch = 12
    static let sequence = 256
    private let model: MLModel
    private let tokenizer: any Tokenizers.Tokenizer
    private let cls = 101, sep = 102

    static func resourceAvailable() -> Bool {
        Bundle.main.url(forResource: "RerankerL12", withExtension: "mlmodelc") != nil
    }

    init() async throws {
        guard let url = Bundle.main.url(forResource: "RerankerL12", withExtension: "mlmodelc") else {
            throw LabError.model("The reranker model is not in this build (run tools/convert_reranker.py, then rebuild).")
        }
        let config = MLModelConfiguration()
        config.computeUnits = .cpuAndNeuralEngine
        model = try MLModel(contentsOf: url, configuration: config)
        guard let folder = Bundle.main.url(forResource: "RerankerTokenizer", withExtension: nil) else {
            throw LabError.model("Reranker tokenizer files are missing from this build.")
        }
        tokenizer = try await AutoTokenizer.from(modelFolder: folder)
    }

    /// [CLS] query [SEP] passage [SEP], the longer side trimmed first until it fits (as the Mac's tokenizer does).
    func encode(query: String, passage: String) -> (ids: [Int32], types: [Int32], mask: [Int32]) {
        var q = tokenizer.encode(text: query, addSpecialTokens: false)
        var p = tokenizer.encode(text: passage, addSpecialTokens: false)
        while q.count + p.count > Self.sequence - 3 {
            if q.count > p.count { q.removeLast() } else { p.removeLast() }
        }
        var ids = [cls] + q + [sep] + p + [sep]
        var types = [Int](repeating: 0, count: q.count + 2) + [Int](repeating: 1, count: p.count + 1)
        var mask = [Int](repeating: 1, count: ids.count)
        let pad = Self.sequence - ids.count
        ids += [Int](repeating: 0, count: pad); types += [Int](repeating: 0, count: pad); mask += [Int](repeating: 0, count: pad)
        return (ids.map(Int32.init), types.map(Int32.init), mask.map(Int32.init))
    }

    /// Relevance 0…1 for each passage (sigmoid of the model's score), in the order given.
    func scores(query: String, passages: [String]) throws -> [Float] {
        var out: [Float] = []
        for start in stride(from: 0, to: passages.count, by: Self.batch) {
            let slice = Array(passages[start..<min(start + Self.batch, passages.count)])
            let padded = slice + Array(repeating: slice[0], count: Self.batch - slice.count)   // fixed shape: repeat, ignore
            let shape = [NSNumber(value: Self.batch), NSNumber(value: Self.sequence)]
            let idsA = try MLMultiArray(shape: shape, dataType: .int32)
            let maskA = try MLMultiArray(shape: shape, dataType: .int32)
            let typeA = try MLMultiArray(shape: shape, dataType: .int32)
            for (r, text) in padded.enumerated() {
                let e = encode(query: query, passage: text)
                for c in 0..<Self.sequence {
                    let i = r * Self.sequence + c
                    idsA[i] = NSNumber(value: e.ids[c]); maskA[i] = NSNumber(value: e.mask[c]); typeA[i] = NSNumber(value: e.types[c])
                }
            }
            let input = try MLDictionaryFeatureProvider(dictionary: ["input_ids": idsA, "attention_mask": maskA, "token_type_ids": typeA])
            let logits = try model.prediction(from: input).featureValue(for: "logits")?.multiArrayValue
            guard let logits else { throw LabError.model("the reranker returned no scores") }
            for r in 0..<slice.count {
                let z = logits[r].floatValue
                out.append(1 / (1 + exp(-z)))
            }
        }
        return out
    }

    // MARK: conformance against the Mac

    struct Check: Sendable {
        let encodingMatches: Int
        let encodingTotal: Int
        let rankCorrelation: Double
        let top5Overlap: Double
        let millisecondsPerBatch: Double
    }

    /// Re-run the phone's tokenizer and model on pairs the Mac scored with its own reranker.
    func check() async throws -> Check {
        guard let url = Bundle.main.url(forResource: "rerank_conformance", withExtension: "json"),
              let root = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any],
              let qs = root["questions"] as? [[String: Any]], let enc = root["encoding"] as? [[String: Any]] else {
            throw LabError.model("conformance file missing")
        }
        var match = 0
        for e in enc {
            let r = encode(query: e["question"] as! String, passage: e["passage"] as! String)
            if r.ids.map(Int.init) == (e["ids"] as! [Int]) && r.types.map(Int.init) == (e["type_ids"] as! [Int]) { match += 1 }
        }
        var rhos: [Double] = [], overlaps: [Double] = [], ms: [Double] = []
        for q in qs {
            let ps = q["passages"] as! [[String: Any]]
            let mac = ps.map { ($0["mac_score"] as! NSNumber).doubleValue }
            let t0 = Date()
            let mine = try scores(query: q["question"] as! String, passages: ps.map { $0["text"] as! String }).map(Double.init)
            ms.append(Date().timeIntervalSince(t0) * 1000)
            rhos.append(Self.spearman(mac, mine))
            let a = Set(mac.indices.sorted { mac[$0] > mac[$1] }.prefix(5)), b = Set(mine.indices.sorted { mine[$0] > mine[$1] }.prefix(5))
            overlaps.append(Double(a.intersection(b).count) / 5)
        }
        func mean(_ x: [Double]) -> Double { x.reduce(0, +) / Double(max(1, x.count)) }
        return Check(encodingMatches: match, encodingTotal: enc.count, rankCorrelation: mean(rhos), top5Overlap: mean(overlaps),
                     millisecondsPerBatch: mean(ms))
    }

    private static func spearman(_ a: [Double], _ b: [Double]) -> Double {
        func ranks(_ x: [Double]) -> [Double] {
            let order = x.indices.sorted { x[$0] < x[$1] }
            var r = [Double](repeating: 0, count: x.count)
            for (rank, i) in order.enumerated() { r[i] = Double(rank) }
            return r
        }
        let ra = ranks(a), rb = ranks(b), n = Double(a.count)
        let ma = ra.reduce(0, +) / n, mb = rb.reduce(0, +) / n
        let cov = zip(ra, rb).reduce(0) { $0 + ($1.0 - ma) * ($1.1 - mb) }
        let va = ra.reduce(0) { $0 + ($1 - ma) * ($1 - ma) }, vb = rb.reduce(0) { $0 + ($1 - mb) * ($1 - mb) }
        return cov / (va * vb).squareRoot()
    }
}
