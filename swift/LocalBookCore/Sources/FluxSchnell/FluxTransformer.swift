// FLUX.1 schnell's transformer in Swift on MLX: a port, layer for layer, of the MIT-licensed mflux reference
// (Black Forest Labs' architecture: 19 joint blocks over image and text streams, 38 single blocks over both
// at once, adaptive layer norms driven by the timestep and the pooled CLIP embedding, 3-axis rotary positions).
// Module and parameter names are the checkpoint's keys, so the weights load with no renaming.
import Foundation
import MLX
import MLXFast
import MLXNN

let fluxDim = 3072
let fluxHeads = 24
let fluxHeadDim = 128

// MARK: - Adaptive norms

/// Scale-and-shift norm over the output stream, from the conditioning vector.
final class AdaLayerNormContinuous: Module {
    @ModuleInfo var linear: Linear
    @ModuleInfo var norm: LayerNorm

    override init() {
        _linear.wrappedValue = Linear(fluxDim, fluxDim * 2)
        _norm.wrappedValue = LayerNorm(dimensions: fluxDim, eps: 1e-6, affine: false)
    }

    func callAsFunction(_ x: MLXArray, conditioning: MLXArray) -> MLXArray {
        let e = linear(silu(conditioning))
        let scale = e[0..., 0 ..< fluxDim].expandedDimensions(axis: 1)
        let shift = e[0..., fluxDim ..< (2 * fluxDim)].expandedDimensions(axis: 1)
        return norm(x) * (1 + scale) + shift
    }
}

/// Six-way modulation for a joint block: shift, scale and gate for attention and for the feed-forward.
final class AdaLayerNormZero: Module {
    @ModuleInfo var linear: Linear
    @ModuleInfo var norm: LayerNorm

    override init() {
        _linear.wrappedValue = Linear(fluxDim, fluxDim * 6)
        _norm.wrappedValue = LayerNorm(dimensions: fluxDim, eps: 1e-6, affine: false)
    }

    /// Returns the normed, modulated stream and (gateAttention, shiftMLP, scaleMLP, gateMLP), each (B, 1, dim).
    func callAsFunction(_ x: MLXArray, conditioning: MLXArray) -> (MLXArray, MLXArray, MLXArray, MLXArray, MLXArray) {
        let parts = linear(silu(conditioning)).split(parts: 6, axis: -1).map { $0.expandedDimensions(axis: 1) }
        let normed = norm(x) * (1 + parts[1]) + parts[0]
        return (normed, parts[2], parts[3], parts[4], parts[5])
    }
}

/// Three-way modulation for a single block: shift, scale and one gate.
final class AdaLayerNormZeroSingle: Module {
    @ModuleInfo var linear: Linear
    @ModuleInfo var norm: LayerNorm

    override init() {
        _linear.wrappedValue = Linear(fluxDim, fluxDim * 3)
        _norm.wrappedValue = LayerNorm(dimensions: fluxDim, eps: 1e-6, affine: false)
    }

    func callAsFunction(_ x: MLXArray, conditioning: MLXArray) -> (MLXArray, MLXArray) {
        let parts = linear(silu(conditioning)).split(parts: 3, axis: -1).map { $0.expandedDimensions(axis: 1) }
        return (norm(x) * (1 + parts[1]) + parts[0], parts[2])
    }
}

// MARK: - Attention helpers

enum FluxAttention {
    /// (B, S, H·D) → (B, H, S, D)
    static func heads(_ x: MLXArray) -> MLXArray {
        x.reshaped(x.dim(0), x.dim(1), fluxHeads, fluxHeadDim).transposed(0, 2, 1, 3)
    }

    /// (B, H, S, D) → (B, S, H·D)
    static func merged(_ x: MLXArray) -> MLXArray {
        x.transposed(0, 2, 1, 3).reshaped(x.dim(0), x.dim(2), fluxHeads * fluxHeadDim)
    }

    /// Projects and head-splits queries, keys and values; the q/k RMS norms run in float32 as the reference does.
    static func qkv(_ x: MLXArray, q: Linear, k: Linear, v: Linear, normQ: RMSNorm, normK: RMSNorm) -> (MLXArray, MLXArray, MLXArray) {
        let dt = x.dtype
        let queries = normQ(heads(q(x)).asType(.float32)).asType(dt)
        let keys = normK(heads(k(x)).asType(.float32)).asType(dt)
        return (queries, keys, heads(v(x)))
    }

    /// Rotates the last axis in pairs by the precomputed frequencies (B, 1, S, D/2, 2, 2), in float32.
    static func rotated(_ x: MLXArray, by freqs: MLXArray) -> MLXArray {
        let dt = x.dtype
        let pairs = x.asType(.float32).reshaped(x.dim(0), x.dim(1), x.dim(2), -1, 1, 2)
        let out = freqs[.ellipsis, 0] * pairs[.ellipsis, 0] + freqs[.ellipsis, 1] * pairs[.ellipsis, 1]
        return out.reshaped(x.shape).asType(dt)
    }

    static func attend(_ q: MLXArray, _ k: MLXArray, _ v: MLXArray) -> MLXArray {
        merged(MLXFast.scaledDotProductAttention(queries: q, keys: k, values: v, scale: 1 / Float(fluxHeadDim).squareRoot(), mask: nil))
    }
}

/// Rotary positions over three axes (text index, latent row, latent column), 16 + 56 + 56 = 128 dims.
enum FluxRotary {
    static let axes = [16, 56, 56]
    static let theta: Float = 10000

    /// `ids`: (1, S, 3) positions. Returns (1, 1, S, 64, 2, 2) float32.
    static func frequencies(ids: MLXArray) -> MLXArray {
        var parts: [MLXArray] = []
        for (i, dim) in axes.enumerated() {
            let pos = ids[.ellipsis, i]                                           // (1, S)
            let scale = MLXArray(stride(from: 0, to: dim, by: 2).map { Float($0) / Float(dim) })
            let omega = 1 / MLX.pow(MLXArray(theta), scale)                       // (dim/2)
            let out = pos.expandedDimensions(axis: -1) * omega.expandedDimensions(axis: 0)   // (1, S, dim/2)
            let stacked = MLX.stacked([MLX.cos(out), -MLX.sin(out), MLX.sin(out), MLX.cos(out)], axis: -1)
            parts.append(stacked.reshaped(pos.dim(0), pos.dim(1), dim / 2, 2, 2))
        }
        return MLX.concatenated(parts, axis: -3).expandedDimensions(axis: 1)
    }

    /// Text tokens sit at position zero on every axis; latent patches carry their row and column.
    static func ids(textTokens: Int, latentHeight: Int, latentWidth: Int) -> MLXArray {
        var values = [Float](repeating: 0, count: textTokens * 3)
        values.reserveCapacity((textTokens + latentHeight * latentWidth) * 3)
        for row in 0 ..< latentHeight {
            for col in 0 ..< latentWidth {
                values.append(contentsOf: [0, Float(row), Float(col)])
            }
        }
        return MLXArray(values, [1, textTokens + latentHeight * latentWidth, 3])
    }
}

// MARK: - Conditioning

/// Sinusoidal timestep features (256) through two linears; the pooled CLIP vector through two more; summed.
final class TimeTextEmbed: Module {
    @ModuleInfo(key: "timestep_embedder") var timestep: TwoLinear
    @ModuleInfo(key: "text_embedder") var text: TwoLinear

    override init() {
        _timestep.wrappedValue = TwoLinear(256, fluxDim)
        _text.wrappedValue = TwoLinear(768, fluxDim)
    }

    func callAsFunction(timestep t: Float, pooled: MLXArray) -> MLXArray {
        (timestep(Self.project(t)) + text(pooled))
    }

    /// Diffusers' timestep projection: 128 frequencies, cos first (sin and cos computed, halves swapped).
    static func project(_ t: Float) -> MLXArray {
        let half = 128
        let exponent = MLXArray((0 ..< half).map { -logf(10000) * Float($0) / Float(half) })
        let angles = MLXArray([t]).expandedDimensions(axis: 1) * MLX.exp(exponent).expandedDimensions(axis: 0)
        return MLX.concatenated([MLX.cos(angles), MLX.sin(angles)], axis: -1)     // (1, 256)
    }
}

final class TwoLinear: Module {
    @ModuleInfo(key: "linear_1") var first: Linear
    @ModuleInfo(key: "linear_2") var second: Linear

    init(_ input: Int, _ output: Int) {
        _first.wrappedValue = Linear(input, output)
        _second.wrappedValue = Linear(output, output)
    }

    func callAsFunction(_ x: MLXArray) -> MLXArray { second(silu(first(x))) }
}

// MARK: - Blocks

final class FeedForward: Module {
    @ModuleInfo var linear1: Linear
    @ModuleInfo var linear2: Linear
    let approximate: Bool

    init(approximate: Bool) {
        self.approximate = approximate
        _linear1.wrappedValue = Linear(fluxDim, fluxDim * 4)
        _linear2.wrappedValue = Linear(fluxDim * 4, fluxDim)
    }

    func callAsFunction(_ x: MLXArray) -> MLXArray {
        linear2(approximate ? geluApproximate(linear1(x)) : gelu(linear1(x)))
    }
}

final class JointAttention: Module {
    @ModuleInfo(key: "to_q") var toQ: Linear
    @ModuleInfo(key: "to_k") var toK: Linear
    @ModuleInfo(key: "to_v") var toV: Linear
    @ModuleInfo(key: "to_out") var toOut: [Linear]
    @ModuleInfo(key: "add_q_proj") var addQ: Linear
    @ModuleInfo(key: "add_k_proj") var addK: Linear
    @ModuleInfo(key: "add_v_proj") var addV: Linear
    @ModuleInfo(key: "to_add_out") var toAddOut: Linear
    @ModuleInfo(key: "norm_q") var normQ: RMSNorm
    @ModuleInfo(key: "norm_k") var normK: RMSNorm
    @ModuleInfo(key: "norm_added_q") var normAddedQ: RMSNorm
    @ModuleInfo(key: "norm_added_k") var normAddedK: RMSNorm

    override init() {
        _toQ.wrappedValue = Linear(fluxDim, fluxDim); _toK.wrappedValue = Linear(fluxDim, fluxDim); _toV.wrappedValue = Linear(fluxDim, fluxDim)
        _toOut.wrappedValue = [Linear(fluxDim, fluxDim)]
        _addQ.wrappedValue = Linear(fluxDim, fluxDim); _addK.wrappedValue = Linear(fluxDim, fluxDim); _addV.wrappedValue = Linear(fluxDim, fluxDim)
        _toAddOut.wrappedValue = Linear(fluxDim, fluxDim)
        _normQ.wrappedValue = RMSNorm(dimensions: fluxHeadDim); _normK.wrappedValue = RMSNorm(dimensions: fluxHeadDim)
        _normAddedQ.wrappedValue = RMSNorm(dimensions: fluxHeadDim); _normAddedK.wrappedValue = RMSNorm(dimensions: fluxHeadDim)
    }

    /// Text tokens go first in the joint sequence, as in the reference.
    func callAsFunction(image: MLXArray, text: MLXArray, rotary: MLXArray) -> (image: MLXArray, text: MLXArray) {
        let (q, k, v) = FluxAttention.qkv(image, q: toQ, k: toK, v: toV, normQ: normQ, normK: normK)
        let (tq, tk, tv) = FluxAttention.qkv(text, q: addQ, k: addK, v: addV, normQ: normAddedQ, normK: normAddedK)
        let queries = FluxAttention.rotated(MLX.concatenated([tq, q], axis: 2), by: rotary)
        let keys = FluxAttention.rotated(MLX.concatenated([tk, k], axis: 2), by: rotary)
        let values = MLX.concatenated([tv, v], axis: 2)
        let out = FluxAttention.attend(queries, keys, values)
        let textLength = text.dim(1)
        return (toOut[0](out[0..., textLength...]), toAddOut(out[0..., ..<textLength]))
    }
}

final class JointTransformerBlock: Module {
    @ModuleInfo var norm1: AdaLayerNormZero
    @ModuleInfo(key: "norm1_context") var norm1Context: AdaLayerNormZero
    @ModuleInfo var attn: JointAttention
    @ModuleInfo var norm2: LayerNorm
    @ModuleInfo(key: "norm2_context") var norm2Context: LayerNorm
    @ModuleInfo var ff: FeedForward
    @ModuleInfo(key: "ff_context") var ffContext: FeedForward

    override init() {
        _norm1.wrappedValue = AdaLayerNormZero(); _norm1Context.wrappedValue = AdaLayerNormZero()
        _attn.wrappedValue = JointAttention()
        _norm2.wrappedValue = LayerNorm(dimensions: fluxDim, eps: 1e-6, affine: false)
        _norm2Context.wrappedValue = LayerNorm(dimensions: fluxDim, eps: 1e-6, affine: false)
        _ff.wrappedValue = FeedForward(approximate: false)
        _ffContext.wrappedValue = FeedForward(approximate: true)
    }

    func callAsFunction(image: MLXArray, text: MLXArray, conditioning: MLXArray, rotary: MLXArray) -> (image: MLXArray, text: MLXArray) {
        let (normedImage, gateAttn, shiftMLP, scaleMLP, gateMLP) = norm1(image, conditioning: conditioning)
        let (normedText, tGateAttn, tShiftMLP, tScaleMLP, tGateMLP) = norm1Context(text, conditioning: conditioning)
        let (attnImage, attnText) = attn(image: normedImage, text: normedText, rotary: rotary)
        let newImage = Self.residual(image, attention: attnImage, gateAttn: gateAttn, shift: shiftMLP, scale: scaleMLP, gateMLP: gateMLP, norm: norm2, ff: ff)
        let newText = Self.residual(text, attention: attnText, gateAttn: tGateAttn, shift: tShiftMLP, scale: tScaleMLP, gateMLP: tGateMLP, norm: norm2Context, ff: ffContext)
        return (newImage, newText)
    }

    private static func residual(_ x: MLXArray, attention: MLXArray, gateAttn: MLXArray, shift: MLXArray, scale: MLXArray, gateMLP: MLXArray, norm: LayerNorm, ff: FeedForward) -> MLXArray {
        let afterAttention = x + gateAttn * attention
        let normed = norm(afterAttention) * (1 + scale) + shift
        return afterAttention + gateMLP * ff(normed)
    }
}

final class SingleBlockAttention: Module {
    @ModuleInfo(key: "to_q") var toQ: Linear
    @ModuleInfo(key: "to_k") var toK: Linear
    @ModuleInfo(key: "to_v") var toV: Linear
    @ModuleInfo(key: "norm_q") var normQ: RMSNorm
    @ModuleInfo(key: "norm_k") var normK: RMSNorm

    override init() {
        _toQ.wrappedValue = Linear(fluxDim, fluxDim); _toK.wrappedValue = Linear(fluxDim, fluxDim); _toV.wrappedValue = Linear(fluxDim, fluxDim)
        _normQ.wrappedValue = RMSNorm(dimensions: fluxHeadDim); _normK.wrappedValue = RMSNorm(dimensions: fluxHeadDim)
    }

    func callAsFunction(_ x: MLXArray, rotary: MLXArray) -> MLXArray {
        let (q, k, v) = FluxAttention.qkv(x, q: toQ, k: toK, v: toV, normQ: normQ, normK: normK)
        return FluxAttention.attend(FluxAttention.rotated(q, by: rotary), FluxAttention.rotated(k, by: rotary), v)
    }
}

final class SingleTransformerBlock: Module {
    @ModuleInfo var norm: AdaLayerNormZeroSingle
    @ModuleInfo var attn: SingleBlockAttention
    @ModuleInfo(key: "proj_mlp") var projMLP: Linear
    @ModuleInfo(key: "proj_out") var projOut: Linear

    override init() {
        _norm.wrappedValue = AdaLayerNormZeroSingle()
        _attn.wrappedValue = SingleBlockAttention()
        _projMLP.wrappedValue = Linear(fluxDim, fluxDim * 4)
        _projOut.wrappedValue = Linear(fluxDim * 5, fluxDim)
    }

    func callAsFunction(_ x: MLXArray, conditioning: MLXArray, rotary: MLXArray) -> MLXArray {
        let (normed, gate) = norm(x, conditioning: conditioning)
        let attention = attn(normed, rotary: rotary)
        let mlp = geluApproximate(projMLP(normed))
        return x + gate * projOut(MLX.concatenated([attention, mlp], axis: 2))
    }
}

// MARK: - The transformer

/// The schnell transformer: 12 B parameters, 4-bit in the checkpoint this package downloads.
final class FluxTransformer: Module {
    @ModuleInfo(key: "x_embedder") var xEmbedder: Linear
    @ModuleInfo(key: "context_embedder") var contextEmbedder: Linear
    @ModuleInfo(key: "time_text_embed") var timeText: TimeTextEmbed
    @ModuleInfo(key: "transformer_blocks") var blocks: [JointTransformerBlock]
    @ModuleInfo(key: "single_transformer_blocks") var singleBlocks: [SingleTransformerBlock]
    @ModuleInfo(key: "norm_out") var normOut: AdaLayerNormContinuous
    @ModuleInfo(key: "proj_out") var projOut: Linear

    override init() {
        _xEmbedder.wrappedValue = Linear(64, fluxDim)
        _contextEmbedder.wrappedValue = Linear(4096, fluxDim)
        _timeText.wrappedValue = TimeTextEmbed()
        _blocks.wrappedValue = (0 ..< 19).map { _ in JointTransformerBlock() }
        _singleBlocks.wrappedValue = (0 ..< 38).map { _ in SingleTransformerBlock() }
        _normOut.wrappedValue = AdaLayerNormContinuous()
        _projOut.wrappedValue = Linear(fluxDim, 64)
    }

    /// Predicts the flow for packed latents (1, S, 64) at `sigma` (0…1), given the T5 sequence (1, T, 4096), the
    /// pooled CLIP vector (1, 768) and the rotary frequencies for T + S positions.
    func callAsFunction(latents: MLXArray, sigma: Float, prompt: MLXArray, pooled: MLXArray, rotary: MLXArray) -> MLXArray {
        var image = xEmbedder(latents)
        var text = contextEmbedder(prompt)
        let conditioning = timeText(timestep: sigma * 1000, pooled: pooled).asType(image.dtype)
        for block in blocks {
            (image, text) = block(image: image, text: text, conditioning: conditioning, rotary: rotary)
        }
        var joint = MLX.concatenated([text, image], axis: 1)
        for block in singleBlocks {
            joint = block(joint, conditioning: conditioning, rotary: rotary)
        }
        let out = joint[0..., text.dim(1)...]
        return projOut(normOut(out, conditioning: conditioning))
    }
}
