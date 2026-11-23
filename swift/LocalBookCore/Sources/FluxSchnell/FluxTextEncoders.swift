// FLUX.1's two text encoders in Swift on MLX, ported from the MIT-licensed mflux reference: Google's T5-XXL
// encoder (the prompt's 4096-wide token sequence, 24 blocks with relative position buckets) and OpenAI's CLIP
// ViT-L/14 text tower (the pooled 768-wide vector). Neither is a chat model; both come from the same checkpoint
// as the transformer. The CLIP tokenizer is written here (byte-level BPE over vocab.json and merges.txt), the T5
// tokenizer comes from swift-transformers.
import Foundation
import MLX
import MLXFast
import MLXNN
import Tokenizers

// MARK: - T5

/// T5's RMS norm: variance in float32, a weight and no bias.
final class T5LayerNorm: Module {
    @ParameterInfo var weight: MLXArray

    override init() { weight = MLXArray.ones([4096]) }

    func callAsFunction(_ x: MLXArray) -> MLXArray {
        let variance = MLX.mean(MLX.square(x.asType(.float32)), axis: -1, keepDims: true)
        return weight * (x * MLX.rsqrt(variance + 1e-6)).asType(weight.dtype)
    }
}

final class T5SelfAttention: Module {
    @ModuleInfo var q: Linear
    @ModuleInfo var k: Linear
    @ModuleInfo var v: Linear
    @ModuleInfo var o: Linear
    @ModuleInfo(key: "relative_attention_bias") var relativeBias: Embedding

    override init() {
        q = Linear(4096, 4096, bias: false); k = Linear(4096, 4096, bias: false)
        v = Linear(4096, 4096, bias: false); o = Linear(4096, 4096, bias: false)
        relativeBias = Embedding(embeddingCount: 32, dimensions: 64)
    }

    /// `buckets`: (S, S) int32 relative-position buckets, shared by every block.
    func callAsFunction(_ x: MLXArray, buckets: MLXArray) -> MLXArray {
        func heads(_ a: MLXArray) -> MLXArray { a.reshaped(1, -1, 64, 64).transposed(0, 2, 1, 3) }
        let bias = relativeBias(buckets).transposed(2, 0, 1).expandedDimensions(axis: 0).asType(x.dtype)   // (1, 64, S, S)
        // T5 scores are not scaled; the position bias is added before the softmax.
        let out = MLXFast.scaledDotProductAttention(queries: heads(q(x)), keys: heads(k(x)), values: heads(v(x)), scale: 1, mask: bias)
        return o(out.transposed(0, 2, 1, 3).reshaped(1, -1, 4096))
    }

    /// Bidirectional buckets: 32 buckets, exact to 8, logarithmic to a distance of 128.
    static func buckets(length: Int) -> MLXArray {
        let numBuckets = 16, maxExact = 8, maxDistance = 128
        var values = [Int32](repeating: 0, count: length * length)
        for i in 0 ..< length {
            for j in 0 ..< length {
                let relative = j - i
                var bucket = relative > 0 ? numBuckets : 0
                let distance = abs(relative)
                if distance < maxExact {
                    bucket += distance
                } else {
                    let large = maxExact + Int(floor(log(Double(distance) / Double(maxExact)) / log(Double(maxDistance) / Double(maxExact)) * Double(numBuckets - maxExact)))
                    bucket += min(large, numBuckets - 1)
                }
                values[i * length + j] = Int32(bucket)
            }
        }
        return MLXArray(values, [length, length])
    }
}

final class T5Attention: Module {
    @ModuleInfo(key: "SelfAttention") var attention: T5SelfAttention
    @ModuleInfo(key: "layer_norm") var norm: T5LayerNorm

    override init() { attention = T5SelfAttention(); norm = T5LayerNorm() }

    func callAsFunction(_ x: MLXArray, buckets: MLXArray) -> MLXArray { x + attention(norm(x), buckets: buckets) }
}

final class T5DenseReluDense: Module {
    @ModuleInfo(key: "wi_0") var wi0: Linear
    @ModuleInfo(key: "wi_1") var wi1: Linear
    @ModuleInfo var wo: Linear

    override init() {
        wi0 = Linear(4096, 10240, bias: false); wi1 = Linear(4096, 10240, bias: false); wo = Linear(10240, 4096, bias: false)
    }

    func callAsFunction(_ x: MLXArray) -> MLXArray { wo(geluApproximate(wi0(x)) * wi1(x)) }
}

final class T5FeedForward: Module {
    @ModuleInfo(key: "layer_norm") var norm: T5LayerNorm
    @ModuleInfo(key: "DenseReluDense") var dense: T5DenseReluDense

    override init() { norm = T5LayerNorm(); dense = T5DenseReluDense() }

    func callAsFunction(_ x: MLXArray) -> MLXArray { x + dense(norm(x)) }
}

final class T5Block: Module {
    @ModuleInfo var attention: T5Attention
    @ModuleInfo var ff: T5FeedForward

    override init() { attention = T5Attention(); ff = T5FeedForward() }

    func callAsFunction(_ x: MLXArray, buckets: MLXArray) -> MLXArray { ff(attention(x, buckets: buckets)) }
}

/// T5-XXL encoder: 4.7 B parameters, 4-bit in the downloaded checkpoint. Output (1, T, 4096).
final class T5Encoder: Module {
    @ModuleInfo var shared: Embedding
    @ModuleInfo(key: "t5_blocks") var blocks: [T5Block]
    @ModuleInfo(key: "final_layer_norm") var finalNorm: T5LayerNorm

    override init() {
        shared = Embedding(embeddingCount: 32128, dimensions: 4096)
        blocks = (0 ..< 24).map { _ in T5Block() }
        finalNorm = T5LayerNorm()
    }

    func callAsFunction(_ tokens: MLXArray) -> MLXArray {
        let buckets = T5SelfAttention.buckets(length: tokens.dim(1))
        var x = shared(tokens)
        for block in blocks { x = block(x, buckets: buckets) }
        return finalNorm(x)
    }
}

// MARK: - CLIP

final class CLIPEmbeddings: Module {
    @ModuleInfo(key: "token_embedding") var token: Embedding
    @ModuleInfo(key: "position_embedding") var position: Embedding

    override init() {
        token = Embedding(embeddingCount: 49408, dimensions: 768)
        position = Embedding(embeddingCount: 77, dimensions: 768)
    }

    func callAsFunction(_ tokens: MLXArray) -> MLXArray {
        token(tokens) + position(MLXArray((0 ..< tokens.dim(-1)).map { Int32($0) }).reshaped(1, -1))
    }
}

final class CLIPAttention: Module {
    @ModuleInfo(key: "q_proj") var q: Linear
    @ModuleInfo(key: "k_proj") var k: Linear
    @ModuleInfo(key: "v_proj") var v: Linear
    @ModuleInfo(key: "out_proj") var out: Linear

    override init() {
        q = Linear(768, 768); k = Linear(768, 768); v = Linear(768, 768); out = Linear(768, 768)
    }

    func callAsFunction(_ x: MLXArray, mask: MLXArray) -> MLXArray {
        func heads(_ a: MLXArray) -> MLXArray { a.reshaped(1, -1, 12, 64).transposed(0, 2, 1, 3) }
        let attended = MLXFast.scaledDotProductAttention(queries: heads(q(x)), keys: heads(k(x)), values: heads(v(x)), scale: 1 / Float(64).squareRoot(), mask: mask.asType(x.dtype))
        return out(attended.transposed(0, 2, 1, 3).reshaped(1, -1, 768))
    }
}

final class CLIPMLP: Module {
    @ModuleInfo var fc1: Linear
    @ModuleInfo var fc2: Linear

    override init() { fc1 = Linear(768, 3072); fc2 = Linear(3072, 768) }

    /// CLIP's "quick" GELU.
    func callAsFunction(_ x: MLXArray) -> MLXArray {
        let h = fc1(x)
        return fc2(h * sigmoid(1.702 * h))
    }
}

final class CLIPEncoderLayer: Module {
    @ModuleInfo(key: "self_attn") var attention: CLIPAttention
    @ModuleInfo(key: "layer_norm1") var norm1: LayerNorm
    @ModuleInfo var mlp: CLIPMLP
    @ModuleInfo(key: "layer_norm2") var norm2: LayerNorm

    override init() {
        attention = CLIPAttention(); mlp = CLIPMLP()
        norm1 = LayerNorm(dimensions: 768); norm2 = LayerNorm(dimensions: 768)
    }

    func callAsFunction(_ x: MLXArray, mask: MLXArray) -> MLXArray {
        let afterAttention = x + attention(norm1(x), mask: mask)
        return afterAttention + mlp(norm2(afterAttention))
    }
}

final class CLIPEncoderStack: Module {
    @ModuleInfo var layers: [CLIPEncoderLayer]

    override init() { layers = (0 ..< 12).map { _ in CLIPEncoderLayer() } }

    func callAsFunction(_ x: MLXArray, mask: MLXArray) -> MLXArray {
        var h = x
        for layer in layers { h = layer(h, mask: mask) }
        return h
    }
}

final class CLIPTextModel: Module {
    @ModuleInfo var embeddings: CLIPEmbeddings
    @ModuleInfo var encoder: CLIPEncoderStack
    @ModuleInfo(key: "final_layer_norm") var finalNorm: LayerNorm

    override init() {
        embeddings = CLIPEmbeddings(); encoder = CLIPEncoderStack(); finalNorm = LayerNorm(dimensions: 768)
    }

    /// The pooled vector: the final hidden state at the end-of-text token (the highest id), (1, 768).
    func callAsFunction(_ tokens: MLXArray) -> MLXArray {
        let length = tokens.dim(-1)
        let causal = (1 - MLX.tril(MLXArray.ones([length, length]))) * -1e9
        let hidden = finalNorm(encoder(embeddings(tokens), mask: causal.reshaped(1, 1, length, length)))
        let end = MLX.argMax(tokens, axis: -1)          // (1)
        return hidden[0][end]                           // (1, 768)
    }
}

/// CLIP ViT-L/14's text tower, as the checkpoint's `text_encoder`.
final class CLIPEncoder: Module {
    @ModuleInfo(key: "text_model") var textModel: CLIPTextModel

    override init() { textModel = CLIPTextModel() }

    func callAsFunction(_ tokens: MLXArray) -> MLXArray { textModel(tokens) }
}

// MARK: - Tokenizers

/// CLIP's byte-level BPE (lower-cased, `</w>` closing each word), from the checkpoint's `tokenizer/` folder.
struct CLIPTokenizer: Sendable {
    static let start = 49406
    static let end = 49407
    static let length = 77

    private let vocab: [String: Int]
    private let ranks: [String: Int]           // "a b" → merge rank
    private let byteToUnicode: [UInt8: Character]

    init(folder: URL) throws {
        let vocabData = try Data(contentsOf: folder.appending(path: "vocab.json"))
        guard let v = try JSONSerialization.jsonObject(with: vocabData) as? [String: Int] else {
            throw FluxError.weights("tokenizer/vocab.json is not a token table")
        }
        vocab = v
        let merges = try String(contentsOf: folder.appending(path: "merges.txt"), encoding: .utf8)
        var r: [String: Int] = [:]
        var rank = 0
        for line in merges.split(separator: "\n", omittingEmptySubsequences: true) {
            if line.hasPrefix("#version") { continue }
            r[String(line).trimmingCharacters(in: .whitespaces)] = rank
            rank += 1
        }
        ranks = r
        byteToUnicode = Self.bytesToUnicode()
    }

    /// GPT-2's printable mapping of the 256 byte values.
    private static func bytesToUnicode() -> [UInt8: Character] {
        var bs: [Int] = Array(33 ... 126) + Array(161 ... 172) + Array(174 ... 255)
        var cs = bs
        var n = 0
        for b in 0 ..< 256 where !bs.contains(b) {
            bs.append(b)
            cs.append(256 + n)
            n += 1
        }
        var map: [UInt8: Character] = [:]
        for (b, c) in zip(bs, cs) { map[UInt8(b)] = Character(UnicodeScalar(c)!) }
        return map
    }

    /// Token ids padded to 77 with the end token, as the reference feeds the encoder.
    func encode(_ text: String) -> [Int32] {
        let cleaned = text.lowercased().split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
        var ids: [Int] = []
        let pattern = #"<\|startoftext\|>|<\|endoftext\|>|'s|'t|'re|'ve|'m|'ll|'d|[\p{L}]+|[\p{N}]|[^\s\p{L}\p{N}]+"#
        let regex = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive])
        let ns = cleaned as NSString
        let matches = regex?.matches(in: cleaned, range: NSRange(location: 0, length: ns.length)) ?? []
        for match in matches {
            let piece = ns.substring(with: match.range)
            let encoded = String(piece.utf8.map { byteToUnicode[$0]! })
            for token in bpe(encoded).split(separator: " ") {
                if let id = vocab[String(token)] { ids.append(id) }
            }
        }
        if ids.count > Self.length - 2 { ids = Array(ids.prefix(Self.length - 2)) }
        var out = [Self.start] + ids + [Self.end]
        while out.count < Self.length { out.append(Self.end) }
        return out.map { Int32($0) }
    }

    private func bpe(_ token: String) -> String {
        var word = token.map { String($0) }
        guard !word.isEmpty else { return token }
        word[word.count - 1] += "</w>"
        func pairs(_ w: [String]) -> Set<String> {
            var set = Set<String>()
            for i in 0 ..< max(0, w.count - 1) { set.insert(w[i] + " " + w[i + 1]) }
            return set
        }
        var current = pairs(word)
        while !current.isEmpty {
            guard let best = current.min(by: { (ranks[$0] ?? Int.max) < (ranks[$1] ?? Int.max) }), ranks[best] != nil else { break }
            let parts = best.split(separator: " ", maxSplits: 1).map(String.init)
            let (first, second) = (parts[0], parts[1])
            var merged: [String] = []
            var i = 0
            while i < word.count {
                if word[i] == first, i < word.count - 1, word[i + 1] == second {
                    merged.append(first + second)
                    i += 2
                } else {
                    merged.append(word[i])
                    i += 1
                }
            }
            word = merged
            if word.count == 1 { break }
            current = pairs(word)
        }
        return word.joined(separator: " ")
    }
}

/// T5's SentencePiece tokenizer through swift-transformers, padded with zeros to the schnell length (256) and
/// closed by the end token the way the reference's HF tokenizer does.
struct T5PromptTokenizer {
    static let length = 256
    private let tokenizer: any Tokenizer

    init(folder: URL) async throws {
        tokenizer = try await AutoTokenizer.from(modelFolder: folder)
    }

    func encode(_ text: String) -> [Int32] {
        var ids = tokenizer.encode(text: text)
        if ids.count > Self.length {
            ids = Array(ids.prefix(Self.length - 1)) + [ids.last ?? 1]
        }
        while ids.count < Self.length { ids.append(0) }
        return ids.map { Int32($0) }
    }
}
