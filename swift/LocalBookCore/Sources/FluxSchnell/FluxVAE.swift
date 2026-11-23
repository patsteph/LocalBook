// FLUX.1's VAE in Swift on MLX (the reference's layout): 16 latent channels at an eighth of the picture's size,
// a ResNet decoder with one attention block in the middle, and the matching encoder for picture-to-picture
// passes. Channels last throughout (MLX's convolution layout); only the pipeline sees pixels.
import Foundation
import MLX
import MLXFast
import MLXNN

/// A 3×3 same-size convolution, named `conv2d` as the checkpoint names it.
final class NamedConv: Module {
    @ModuleInfo var conv2d: Conv2d
    init(_ input: Int, _ output: Int) { _conv2d.wrappedValue = Conv2d(inputChannels: input, outputChannels: output, kernelSize: 3, padding: 1) }
    func callAsFunction(_ x: MLXArray) -> MLXArray { conv2d(x) }
}

/// A group norm, named `norm` as the checkpoint names it, run in float32.
final class NamedNorm: Module {
    @ModuleInfo var norm: GroupNorm
    init(_ channels: Int) { _norm.wrappedValue = GroupNorm(groupCount: 32, dimensions: channels, eps: 1e-6, affine: true, pytorchCompatible: true) }
    func callAsFunction(_ x: MLXArray) -> MLXArray { norm(x.asType(.float32)).asType(x.dtype) }
}

final class ResnetBlock2D: Module {
    @ModuleInfo var norm1: GroupNorm
    @ModuleInfo var conv1: Conv2d
    @ModuleInfo var norm2: GroupNorm
    @ModuleInfo var conv2: Conv2d
    @ModuleInfo(key: "conv_shortcut") var shortcut: Conv2d?

    init(_ input: Int, _ output: Int) {
        _norm1.wrappedValue = GroupNorm(groupCount: 32, dimensions: input, eps: 1e-6, affine: true, pytorchCompatible: true)
        _conv1.wrappedValue = Conv2d(inputChannels: input, outputChannels: output, kernelSize: 3, padding: 1)
        _norm2.wrappedValue = GroupNorm(groupCount: 32, dimensions: output, eps: 1e-6, affine: true, pytorchCompatible: true)
        _conv2.wrappedValue = Conv2d(inputChannels: output, outputChannels: output, kernelSize: 3, padding: 1)
        _shortcut.wrappedValue = input == output ? nil : Conv2d(inputChannels: input, outputChannels: output, kernelSize: 1)
    }

    func callAsFunction(_ x: MLXArray) -> MLXArray {
        let dt = x.dtype
        var h = conv1(silu(norm1(x.asType(.float32)).asType(dt)))
        h = conv2(silu(norm2(h.asType(.float32)).asType(dt)))
        return (shortcut?(x) ?? x) + h
    }
}

final class VAEAttention: Module {
    @ModuleInfo(key: "group_norm") var groupNorm: GroupNorm
    @ModuleInfo(key: "to_q") var toQ: Linear
    @ModuleInfo(key: "to_k") var toK: Linear
    @ModuleInfo(key: "to_v") var toV: Linear
    @ModuleInfo(key: "to_out") var toOut: [Linear]

    override init() {
        _groupNorm.wrappedValue = GroupNorm(groupCount: 32, dimensions: 512, pytorchCompatible: true)
        _toQ.wrappedValue = Linear(512, 512); _toK.wrappedValue = Linear(512, 512); _toV.wrappedValue = Linear(512, 512); _toOut.wrappedValue = [Linear(512, 512)]
    }

    func callAsFunction(_ x: MLXArray) -> MLXArray {
        let (b, h, w, c) = (x.dim(0), x.dim(1), x.dim(2), x.dim(3))
        let y = groupNorm(x.asType(.float32)).asType(x.dtype)
        func seq(_ a: MLXArray) -> MLXArray { a.reshaped(b, h * w, 1, c).transposed(0, 2, 1, 3) }
        let attended = MLXFast.scaledDotProductAttention(queries: seq(toQ(y)), keys: seq(toK(y)), values: seq(toV(y)), scale: 1 / Float(c).squareRoot(), mask: nil)
        return x + toOut[0](attended.transposed(0, 2, 1, 3).reshaped(b, h, w, c))
    }
}

final class UnetMidBlock: Module {
    @ModuleInfo var attentions: [VAEAttention]
    @ModuleInfo var resnets: [ResnetBlock2D]

    override init() {
        _attentions.wrappedValue = [VAEAttention()]
        _resnets.wrappedValue = [ResnetBlock2D(512, 512), ResnetBlock2D(512, 512)]
    }

    func callAsFunction(_ x: MLXArray) -> MLXArray { resnets[1](attentions[0](resnets[0](x))) }
}

final class UpSampler: Module {
    @ModuleInfo var conv: Conv2d
    init(_ channels: Int) { _conv.wrappedValue = Conv2d(inputChannels: channels, outputChannels: channels, kernelSize: 3, padding: 1) }

    /// Nearest-neighbour ×2, then the convolution.
    func callAsFunction(_ x: MLXArray) -> MLXArray {
        let (b, h, w, c) = (x.dim(0), x.dim(1), x.dim(2), x.dim(3))
        let grown = MLX.broadcast(x[0..., 0..., .newAxis, 0..., .newAxis, 0...], to: [b, h, 2, w, 2, c]).reshaped(b, h * 2, w * 2, c)
        return conv(grown)
    }
}

final class UpBlock: Module {
    @ModuleInfo var resnets: [ResnetBlock2D]
    @ModuleInfo var upsamplers: [UpSampler]

    init(_ input: Int, _ output: Int, upsample: Bool) {
        _resnets.wrappedValue = [ResnetBlock2D(input, output), ResnetBlock2D(output, output), ResnetBlock2D(output, output)]
        _upsamplers.wrappedValue = upsample ? [UpSampler(output)] : []
    }

    func callAsFunction(_ x: MLXArray) -> MLXArray {
        var h = x
        for r in resnets { h = r(h) }
        if let up = upsamplers.first { h = up(h) }
        return h
    }
}

final class DownSampler: Module {
    @ModuleInfo var conv: Conv2d
    init(_ channels: Int) { _conv.wrappedValue = Conv2d(inputChannels: channels, outputChannels: channels, kernelSize: 3, stride: 2) }

    /// One row and column of zeros on the far sides, then a stride-2 convolution.
    func callAsFunction(_ x: MLXArray) -> MLXArray {
        conv(MLX.padded(x, widths: [IntOrPair(0), IntOrPair((0, 1)), IntOrPair((0, 1)), IntOrPair(0)]))
    }
}

final class DownBlock: Module {
    @ModuleInfo var resnets: [ResnetBlock2D]
    @ModuleInfo var downsamplers: [DownSampler]

    init(_ input: Int, _ output: Int, downsample: Bool) {
        _resnets.wrappedValue = [ResnetBlock2D(input, output), ResnetBlock2D(output, output)]
        _downsamplers.wrappedValue = downsample ? [DownSampler(output)] : []
    }

    func callAsFunction(_ x: MLXArray) -> MLXArray {
        var h = x
        for r in resnets { h = r(h) }
        if let down = downsamplers.first { h = down(h) }
        return h
    }
}

final class VAEDecoder: Module {
    @ModuleInfo(key: "conv_in") var convIn: NamedConv
    @ModuleInfo(key: "mid_block") var mid: UnetMidBlock
    @ModuleInfo(key: "up_blocks") var upBlocks: [UpBlock]
    @ModuleInfo(key: "conv_norm_out") var normOut: NamedNorm
    @ModuleInfo(key: "conv_out") var convOut: NamedConv

    override init() {
        _convIn.wrappedValue = NamedConv(16, 512)
        _mid.wrappedValue = UnetMidBlock()
        _upBlocks.wrappedValue = [UpBlock(512, 512, upsample: true), UpBlock(512, 512, upsample: true), UpBlock(512, 256, upsample: true), UpBlock(256, 128, upsample: false)]
        _normOut.wrappedValue = NamedNorm(128)
        _convOut.wrappedValue = NamedConv(128, 3)
    }

    /// Latents (1, h, w, 16) → picture (1, 8h, 8w, 3) in −1…1.
    func callAsFunction(_ latents: MLXArray) -> MLXArray {
        var h = mid(convIn(latents))
        for block in upBlocks { h = block(h) }
        return convOut(silu(normOut(h)))
    }
}

final class VAEEncoder: Module {
    @ModuleInfo(key: "conv_in") var convIn: NamedConv
    @ModuleInfo(key: "down_blocks") var downBlocks: [DownBlock]
    @ModuleInfo(key: "mid_block") var mid: UnetMidBlock
    @ModuleInfo(key: "conv_norm_out") var normOut: NamedNorm
    @ModuleInfo(key: "conv_out") var convOut: NamedConv

    override init() {
        _convIn.wrappedValue = NamedConv(3, 128)
        _downBlocks.wrappedValue = [DownBlock(128, 128, downsample: true), DownBlock(128, 256, downsample: true), DownBlock(256, 512, downsample: true), DownBlock(512, 512, downsample: false)]
        _mid.wrappedValue = UnetMidBlock()
        _normOut.wrappedValue = NamedNorm(512)
        _convOut.wrappedValue = NamedConv(512, 32)
    }

    /// Picture (1, H, W, 3) in −1…1 → mean and log-variance (1, H/8, W/8, 32).
    func callAsFunction(_ picture: MLXArray) -> MLXArray {
        var h = convIn(picture)
        for block in downBlocks { h = block(h) }
        h = mid(h)
        return convOut(silu(normOut(h)))
    }
}

/// The autoencoder with FLUX's latent scaling.
final class FluxVAE: Module {
    static let scaling: Float = 0.3611
    static let shift: Float = 0.1159

    @ModuleInfo var decoder: VAEDecoder
    @ModuleInfo var encoder: VAEEncoder

    override init() { _decoder.wrappedValue = VAEDecoder(); _encoder.wrappedValue = VAEEncoder() }

    /// Scaled latents (1, h, w, 16) → picture (1, 8h, 8w, 3) in −1…1.
    func decode(_ latents: MLXArray) -> MLXArray {
        decoder(latents / Self.scaling + Self.shift)
    }

    /// Picture (1, H, W, 3) in −1…1 → scaled latents (1, H/8, W/8, 16) (the distribution's mean).
    func encode(_ picture: MLXArray) -> MLXArray {
        let mean = encoder(picture).split(parts: 2, axis: -1)[0]
        return (mean - Self.shift) * Self.scaling
    }
}
