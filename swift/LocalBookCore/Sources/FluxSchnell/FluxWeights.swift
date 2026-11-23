// Loading a saved MLX checkpoint into the modules above. The downloaded repository is in mflux's saved layout:
// one folder per part (transformer/, text_encoder/, text_encoder_2/, vae/), safetensors shards whose keys are
// the module paths, with `.scales` and `.biases` beside every quantized `.weight`. The bits and group size are
// read from the stored shapes, so any 4-, 6- or 8-bit save of the same model loads unchanged.
import Foundation
import MLX
import MLXNN

public enum FluxError: Error, LocalizedError {
    case weights(String)
    case download(String)
    case picture(String)

    public var errorDescription: String? {
        switch self {
        case .weights(let s), .download(let s), .picture(let s): return s
        }
    }
}

enum FluxWeights {
    /// Every array in every `.safetensors` file of `folder`.
    static func read(folder: URL) throws -> [String: MLXArray] {
        let files = (try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)) ?? []
        let shards = files.filter { $0.pathExtension == "safetensors" }.sorted { $0.lastPathComponent < $1.lastPathComponent }
        guard !shards.isEmpty else { throw FluxError.weights("no weights in \(folder.lastPathComponent)/") }
        var all: [String: MLXArray] = [:]
        for shard in shards {
            for (key, value) in try loadArrays(url: shard) { all[key] = value }
        }
        return all
    }

    /// Quantizes the layers the checkpoint stored quantized, fits convolution weights saved channels-first, then
    /// sets every parameter. Throws, naming the first few, when the checkpoint lacks a parameter the model has.
    static func apply(_ stored: [String: MLXArray], to model: Module, part: String) throws {
        var weights = stored
        // Quantized layers: bits = packed width × 32 / input width; group size = input width / scales width.
        quantize(model: model) { path, module in
            guard let scales = weights["\(path).scales"], let packed = weights["\(path).weight"] else { return nil }
            let inputWidth: Int
            if let linear = module as? Linear { inputWidth = linear.weight.dim(1) }
            else if let embedding = module as? Embedding { inputWidth = embedding.weight.dim(1) }
            else { return nil }
            let bits = packed.dim(-1) * 32 / inputWidth
            let groupSize = inputWidth / scales.dim(-1)
            guard [2, 3, 4, 5, 6, 8].contains(bits), [32, 64, 128].contains(groupSize) else { return nil }
            return (groupSize, bits, .affine)
        }
        // A convolution saved as (out, in, kh, kw) becomes MLX's (out, kh, kw, in).
        let expected = Dictionary(uniqueKeysWithValues: model.parameters().flattened())
        for (key, value) in weights where value.ndim == 4 {
            guard let want = expected[key], want.ndim == 4, want.shape != value.shape else { continue }
            let turned = value.transposed(0, 2, 3, 1)
            if turned.shape == want.shape { weights[key] = turned }
        }
        let missing = expected.keys.filter { weights[$0] == nil }.sorted()
        guard missing.isEmpty else {
            throw FluxError.weights("\(part): \(missing.count) parameters missing from the checkpoint, e.g. \(missing.prefix(3).joined(separator: ", "))")
        }
        let used = weights.filter { expected[$0.key] != nil }
        try model.update(parameters: ModuleParameters.unflattened(used), verify: [.shapeMismatch])
        eval(model)
    }

    /// Reads and applies in one go.
    static func load(_ model: Module, from folder: URL, part: String) throws {
        try apply(try read(folder: folder), to: model, part: part)
    }
}
