import CoreML
import Foundation

/// EmbeddingGemma 2 text embeddings on the Neural Engine, from token ids.
///
/// How the FluidInference Core ML build is driven (from its model card):
///   1. Token ids already wrapped `<bos> … <eos>` (ids 2 and 1).
///   2. Each id's row from `embeddings.bf16` (262,144 × 512, raw bfloat16, little-endian),
///      widened to Float by shifting the 16 bits left, multiplied by √512, passed as Float16.
///   3. Right-padded to the smallest compiled length S ∈ {32, 48, 64, 128, 256, 512}
///      with `attention_mask` 1 for tokens, 0 for padding.
///   4. Function `embed_S` returns `embedding` [1, 768], mean-pooled and L2-normalised.
///
/// The embedding-table lookup happens here, on the CPU, because the 262k-row table would
/// not fit the Neural Engine's working set. The model itself never sees token ids.
actor EmbeddingGemma2Engine {
    static let buckets = [32, 48, 64, 128, 256, 512]
    static let hidden = 512
    static let dims = 768
    static let maxTokens = 512
    private static let scale = Float(512).squareRoot()   // 22.627417

    enum Units: String, CaseIterable, Sendable {
        case neuralEngine = "Neural Engine"
        case gpu = "GPU"
        case cpu = "CPU"

        var computeUnits: MLComputeUnits {
            switch self {
            case .neuralEngine: .cpuAndNeuralEngine
            case .gpu: .cpuAndGPU
            case .cpu: .cpuOnly
            }
        }
    }

    private let compiledModel: URL
    private let table: Data
    private let units: Units
    private var functions: [Int: MLModel] = [:]

    var unitsName: String { units.rawValue }

    init(location: EmbeddingModelStore.Location, units: Units = .neuralEngine) throws {
        compiledModel = location.compiledModel
        // Memory-mapped: 256 MB of table, paged in only for the rows actually used.
        table = try Data(contentsOf: location.tokenTable, options: .alwaysMapped)
        self.units = units
        let expected = 262_144 * Self.hidden * 2
        guard table.count == expected else {
            throw LabError.model("embeddings.bf16 is \(table.count) bytes, expected \(expected)")
        }
    }

    /// Load every length bucket up front. The first load on a device compiles each function
    /// for the Neural Engine (minutes, once); Core ML caches it afterwards.
    /// Questions are short, so only the small lengths are loaded at launch; longer ones (a pasted note, a long
    /// passage) load the first time they are needed.
    func warmUp(buckets: [Int] = [32, 48, 64]) throws -> TimeInterval {
        let start = Date()
        for s in buckets {
            let t = Date()
            _ = try function(for: s)
            SoakTrace.log("startup: search length \(s) loaded in \(String(format: "%.1f", Date().timeIntervalSince(t))) s")
        }
        return Date().timeIntervalSince(start)
    }

    private func function(for length: Int) throws -> MLModel {
        if let m = functions[length] { return m }
        let config = MLModelConfiguration()
        config.computeUnits = units.computeUnits
        config.functionName = "embed_\(length)"
        let m = try MLModel(contentsOf: compiledModel, configuration: config)
        functions[length] = m
        return m
    }

    /// One position of an input sequence: a vocabulary token (looked up in the table, scaled), or a vector that is
    /// already in the model's 512-wide space (the audio and image encoders produce these, used as they are).
    enum Entry: Sendable {
        case token(Int)
        case vector([Float16])
    }

    /// One text's token ids (including `<bos>` and `<eos>`) → a unit-length 768-d vector.
    func embed(tokenIds: [Int]) throws -> [Float] {
        try embed(entries: tokenIds.map { .token($0) })
    }

    /// A mixed sequence (e.g. `<bos> <|image> image tokens <image|> <eos>`) → a unit-length 768-d vector.
    func embed(entries: [Entry]) throws -> [Float] {
        let n = entries.count
        guard n > 0 else { throw LabError.model("empty token list") }
        guard n <= Self.maxTokens else { throw LabError.tooLong(n) }
        guard let s = Self.buckets.first(where: { $0 >= n }) else { throw LabError.tooLong(n) }
        let rows = table.count / (Self.hidden * 2)
        for e in entries {
            switch e {
            case .token(let id): if id < 0 || id >= rows { throw LabError.model("token id \(id) is outside the table") }
            case .vector(let v): if v.count != Self.hidden { throw LabError.model("a vector entry has \(v.count) values, expected \(Self.hidden)") }
            }
        }

        let model = try function(for: s)
        let embeds = try MLMultiArray(shape: [1, NSNumber(value: s), NSNumber(value: Self.hidden)],
                                      dataType: .float16)
        // The card says float16; read the compiled type anyway so a mismatch can't throw.
        let maskType = model.modelDescription.inputDescriptionsByName["attention_mask"]?
            .multiArrayConstraint?.dataType ?? .float16
        let mask = try MLMultiArray(shape: [1, NSNumber(value: s)], dataType: maskType)
        for t in 0..<s { mask[[0, NSNumber(value: t)]] = NSNumber(value: t < n ? 1 : 0) }

        table.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            embeds.withUnsafeMutableBufferPointer(ofType: Float16.self) { dst, strides in
                let tStride = strides[1], dStride = strides[2]
                for t in 0..<s {
                    if t < n {
                        switch entries[t] {
                        case .token(let id):
                            let rowStart = id * Self.hidden * 2
                            for d in 0..<Self.hidden {
                                let bits = raw.loadUnaligned(fromByteOffset: rowStart + d * 2, as: UInt16.self)
                                let value = Float(bitPattern: UInt32(UInt16(littleEndian: bits)) << 16) * Self.scale
                                dst[t * tStride + d * dStride] = Float16(value)
                            }
                        case .vector(let v):
                            for d in 0..<Self.hidden { dst[t * tStride + d * dStride] = v[d] }
                        }
                    } else {
                        for d in 0..<Self.hidden { dst[t * tStride + d * dStride] = 0 }
                    }
                }
            }
        }
        let input = try MLDictionaryFeatureProvider(dictionary: [
            "inputs_embeds": MLFeatureValue(multiArray: embeds),
            "attention_mask": MLFeatureValue(multiArray: mask),
        ])
        let output = try model.prediction(from: input)
        guard let out = output.featureValue(for: "embedding")?.multiArrayValue else {
            throw LabError.model("no `embedding` output from embed_\(s)")
        }
        guard out.count >= Self.dims else {
            throw LabError.model("embedding has \(out.count) values, expected \(Self.dims)")
        }
        var v = [Float](repeating: 0, count: Self.dims)
        for i in 0..<Self.dims { v[i] = out[i].floatValue }
        return VectorMath.normalized(v)
    }

    /// The scaled table row for a special token, as the model expects it in a mixed sequence.
    func tokenVector(_ id: Int) throws -> [Float16] {
        let rows = table.count / (Self.hidden * 2)
        guard id >= 0, id < rows else { throw LabError.model("token id \(id) is outside the table") }
        return table.withUnsafeBytes { raw in
            (0..<Self.hidden).map { d in
                let bits = raw.loadUnaligned(fromByteOffset: id * Self.hidden * 2 + d * 2, as: UInt16.self)
                return Float16(Float(bitPattern: UInt32(UInt16(littleEndian: bits)) << 16) * Self.scale)
            }
        }
    }
}

enum VectorMath {
    static func normalized(_ v: [Float]) -> [Float] {
        let n = v.reduce(0) { $0 + $1 * $1 }.squareRoot()
        return n > 0 ? v.map { $0 / n } : v
    }

    static func dot(_ a: [Float], _ b: [Float]) -> Float {
        precondition(a.count == b.count)
        var s: Float = 0
        for i in 0..<a.count { s += a[i] * b[i] }
        return s
    }

    /// Cosine of two vectors (both are unit length here, but normalise to be safe).
    static func cosine(_ a: [Float], _ b: [Float]) -> Double {
        guard a.count == b.count, !a.isEmpty else { return .nan }
        return Double(dot(normalized(a), normalized(b)))
    }

    /// Indices of the top-k corpus vectors for a query, best first.
    static func topK(_ query: [Float], in corpus: [[Float]], k: Int) -> [Int] {
        let q = normalized(query)
        let scored = corpus.enumerated().map { ($0.offset, dot(q, normalized($0.element))) }
        return scored.sorted { $0.1 > $1.1 }.prefix(k).map(\.0)
    }
}
