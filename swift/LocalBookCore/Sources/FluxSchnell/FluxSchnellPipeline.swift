// FLUX.1 schnell (Black Forest Labs, Apache 2.0) on Apple silicon through MLX, with no part from any other
// model family: the transformer, Google's T5-XXL encoder, OpenAI's CLIP text tower and the VAE, all from one
// 4-bit checkpoint of about 9.6 GB. Four steps paint a picture; a second pass can paint it again from itself.
import CoreGraphics
import Foundation
import Hub
import MLX
import MLXNN
import MLXRandom

/// The weights on disk: where they come from and whether every part is there.
public enum FluxSchnellStore {
    /// mflux's saved 4-bit FLUX.1 schnell (transformer, T5, CLIP, VAE and both tokenizers).
    public static let repo = "mflux-community/flux-1-schnell-mflux-q4"
    /// The tokenizers alone, should the saved repository ever lack them.
    public static let tokenizerRepo = "black-forest-labs/FLUX.1-schnell"
    public static let parts = ["transformer", "text_encoder", "text_encoder_2", "vae"]
    public static let approximateBytes: Int64 = 9_600_000_000

    /// The snapshot folder for `repo` under `root` (swift-transformers' layout).
    public static func snapshot(root: URL, repo: String = repo) -> URL {
        root.appending(path: "models/\(repo)", directoryHint: .isDirectory)
    }

    public static func hasWeights(root: URL) -> Bool {
        let folder = snapshot(root: root)
        return parts.allSatisfy { part in
            let files = (try? FileManager.default.contentsOfDirectory(atPath: folder.appending(path: part).path)) ?? []
            return files.contains { $0.hasSuffix(".safetensors") }
        }
    }

    /// Where the tokenizers are, if both are complete.
    public static func tokenizers(root: URL) -> URL? {
        for candidate in [snapshot(root: root), snapshot(root: root, repo: tokenizerRepo)] {
            let fm = FileManager.default
            if fm.fileExists(atPath: candidate.appending(path: "tokenizer/vocab.json").path),
               fm.fileExists(atPath: candidate.appending(path: "tokenizer/merges.txt").path),
               fm.fileExists(atPath: candidate.appending(path: "tokenizer_2/tokenizer.json").path) {
                return candidate
            }
        }
        return nil
    }

    public static func isDownloaded(root: URL) -> Bool { hasWeights(root: root) && tokenizers(root: root) != nil }

    /// Bytes on disk under `root`.
    public static func size(root: URL) -> Int64 {
        guard let e = FileManager.default.enumerator(at: root, includingPropertiesForKeys: [.fileSizeKey]) else { return 0 }
        var total: Int64 = 0
        for case let url as URL in e {
            total += Int64((try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize) ?? 0)
        }
        return total
    }

    /// Fetches what is missing. `progress` gets 0…1 and the part under way.
    public static func download(root: URL, progress: @escaping @Sendable (Double, String) -> Void) async throws {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let hub = HubApi(downloadBase: root)
        if !hasWeights(root: root) {
            _ = try await hub.snapshot(from: repo, matching: parts.map { "\($0)/*" } + ["tokenizer/*", "tokenizer_2/*"]) { p in
                progress(p.fractionCompleted * 0.98, "FLUX.1 schnell")
            }
        }
        if tokenizers(root: root) == nil {
            _ = try await hub.snapshot(from: tokenizerRepo, matching: ["tokenizer/*", "tokenizer_2/*"]) { p in
                progress(0.98 + p.fractionCompleted * 0.02, "tokenizers")
            }
        }
        guard isDownloaded(root: root) else { throw FluxError.download("the download finished without every part of FLUX.1 schnell") }
        progress(1, "done")
    }
}

/// Paints with FLUX.1 schnell. One instance owns the loaded parts; the text encoders are loaded for a prompt and
/// released, the transformer stays while `keepTransformerLoaded` (the default on a Mac with 32 GB or more).
public actor FluxSchnellPipeline {
    public let root: URL
    public var keepTransformerLoaded: Bool
    public var log: @Sendable (String) -> Void = { _ in }

    private var transformer: FluxTransformer?
    private var vae: FluxVAE?
    private var promptCache: [String: (MLXArray, MLXArray)] = [:]

    public init(root: URL, keepTransformerLoaded: Bool = ProcessInfo.processInfo.physicalMemory >= 32 << 30) {
        self.root = root
        self.keepTransformerLoaded = keepTransformerLoaded
    }

    public func setLog(_ log: @escaping @Sendable (String) -> Void) { self.log = log }

    /// Drops every loaded part.
    public func unload() {
        transformer = nil
        vae = nil
        promptCache = [:]
        Memory.clearCache()
    }

    // MARK: Prompt

    /// T5 sequence (1, 256, 4096) and pooled CLIP vector (1, 768), from the two encoders loaded for the call.
    private func encode(prompt: String) async throws -> (MLXArray, MLXArray) {
        if let cached = promptCache[prompt] { return cached }
        guard let tokenizers = FluxSchnellStore.tokenizers(root: root) else { throw FluxError.weights("the tokenizers are not downloaded") }
        let snapshot = FluxSchnellStore.snapshot(root: root)
        let started = Date()
        let t5Tokens = try await T5PromptTokenizer(folder: tokenizers.appending(path: "tokenizer_2", directoryHint: .isDirectory)).encode(prompt)
        let clipTokens = try CLIPTokenizer(folder: tokenizers.appending(path: "tokenizer", directoryHint: .isDirectory)).encode(prompt)

        let t5 = T5Encoder()
        try FluxWeights.load(t5, from: snapshot.appending(path: "text_encoder_2"), part: "T5")
        let sequence = t5(MLXArray(t5Tokens, [1, t5Tokens.count])).asType(.bfloat16)
        eval(sequence)

        let clip = CLIPEncoder()
        try FluxWeights.load(clip, from: snapshot.appending(path: "text_encoder"), part: "CLIP")
        let pooled = clip(MLXArray(clipTokens, [1, clipTokens.count])).asType(.bfloat16)
        eval(pooled)
        Memory.clearCache()
        log("schnell: prompt encoded in \(String(format: "%.1f", Date().timeIntervalSince(started))) s")

        if promptCache.count > 8 { promptCache = [:] }
        promptCache[prompt] = (sequence, pooled)
        return (sequence, pooled)
    }

    private func loadedTransformer() throws -> FluxTransformer {
        if let transformer { return transformer }
        let started = Date()
        let model = FluxTransformer()
        try FluxWeights.load(model, from: FluxSchnellStore.snapshot(root: root).appending(path: "transformer"), part: "transformer")
        log("schnell: transformer loaded in \(String(format: "%.1f", Date().timeIntervalSince(started))) s")
        transformer = model
        return model
    }

    private func loadedVAE() throws -> FluxVAE {
        if let vae { return vae }
        let model = FluxVAE()
        try FluxWeights.load(model, from: FluxSchnellStore.snapshot(root: root).appending(path: "vae"), part: "VAE")
        vae = model
        return model
    }

    // MARK: Painting

    /// Paints `prompt` at `width` × `height` (multiples of 16) in `steps` steps. With `picture`, paints it again
    /// from itself: `strength` (0…1) is how much of the way is redrawn, so 0.5 keeps the composition and renews
    /// the detail.
    public func generate(prompt: String, width: Int, height: Int, steps: Int = 4, seed: UInt64, picture: CGImage? = nil, strength: Float = 0.5) async throws -> CGImage {
        guard width % 16 == 0, height % 16 == 0 else { throw FluxError.picture("width and height must be multiples of 16") }
        let (sequence, pooled) = try await encode(prompt: prompt)
        let transformer = try loadedTransformer()
        let vae = try loadedVAE()
        let started = Date()

        let (lh, lw) = (height / 16, width / 16)
        var sigmas = (0 ..< steps).map { 1 - Float($0) * (1 - 1 / Float(steps)) / Float(max(steps - 1, 1)) }
        sigmas.append(0)
        let noise = MLXRandom.normal([1, lh * lw, 64], key: MLXRandom.key(seed))
        var latents: MLXArray
        var firstStep = 0
        if let picture {
            firstStep = max(1, min(steps - 1, Int(Float(steps) * strength)))
            let pixels = try Self.array(from: picture, width: width, height: height)
            let clean = Self.pack(vae.encode(pixels.asType(.bfloat16)).asType(.float32), latentHeight: lh, latentWidth: lw)
            let sigma = sigmas[firstStep]
            latents = (1 - sigma) * clean + sigma * noise
        } else {
            latents = noise
        }
        let rotary = FluxRotary.frequencies(ids: FluxRotary.ids(textTokens: sequence.dim(1), latentHeight: lh, latentWidth: lw))
        eval(latents, rotary)

        for t in firstStep ..< steps {
            let flow = transformer(latents: latents.asType(.bfloat16), sigma: sigmas[t], prompt: sequence, pooled: pooled, rotary: rotary)
            latents = latents + flow.asType(.float32) * (sigmas[t + 1] - sigmas[t])
            eval(latents)
        }

        let unpacked = Self.unpack(latents, latentHeight: lh, latentWidth: lw)
        let decoded = vae.decode(unpacked.asType(.bfloat16))
        let image = try Self.image(from: decoded)
        log("schnell: \(picture == nil ? "painted" : "repainted") \(width)×\(height) in \(steps - firstStep) steps, \(String(format: "%.1f", Date().timeIntervalSince(started))) s")
        if !keepTransformerLoaded { self.transformer = nil }
        Memory.clearCache()
        return image
    }

    // MARK: Latents

    /// (1, h, w, 16) channels-last latents → (1, (h/2)(w/2), 64) patches of 2×2.
    static func pack(_ latents: MLXArray, latentHeight: Int, latentWidth: Int) -> MLXArray {
        // Channels-first first, as the reference packs: (1, 16, 2h, 2w) → (1, 16, h, 2, w, 2) → (1, h, w, 16, 2, 2).
        let cf = latents.transposed(0, 3, 1, 2).reshaped(1, 16, latentHeight, 2, latentWidth, 2)
        return cf.transposed(0, 2, 4, 1, 3, 5).reshaped(1, latentHeight * latentWidth, 64)
    }

    /// The inverse of `pack`, back to channels-last (1, 2h, 2w, 16) for the VAE.
    static func unpack(_ latents: MLXArray, latentHeight: Int, latentWidth: Int) -> MLXArray {
        let cf = latents.reshaped(1, latentHeight, latentWidth, 16, 2, 2).transposed(0, 3, 1, 4, 2, 5).reshaped(1, 16, latentHeight * 2, latentWidth * 2)
        return cf.transposed(0, 2, 3, 1)
    }

    // MARK: Pixels

    /// A picture drawn at `width` × `height` as (1, H, W, 3) in −1…1.
    static func array(from image: CGImage, width: Int, height: Int) throws -> MLXArray {
        var bytes = [UInt8](repeating: 0, count: width * height * 4)
        let ok = bytes.withUnsafeMutableBytes { buffer -> Bool in
            guard let context = CGContext(data: buffer.baseAddress, width: width, height: height, bitsPerComponent: 8, bytesPerRow: width * 4, space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return false }
            context.interpolationQuality = .high
            context.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        guard ok else { throw FluxError.picture("the picture could not be read") }
        let rgba = MLXArray(bytes, [1, height, width, 4]).asType(.float32) / 255
        return rgba[0..., 0..., 0..., 0 ..< 3] * 2 - 1
    }

    /// (1, H, W, 3) in −1…1 → an RGB picture.
    static func image(from decoded: MLXArray) throws -> CGImage {
        let (height, width) = (decoded.dim(1), decoded.dim(2))
        let pixels = MLX.clip((decoded.asType(.float32) / 2 + 0.5) * 255, min: 0, max: 255).round().asType(.uint8).squeezed(axis: 0)
        eval(pixels)
        let data = pixels.asArray(UInt8.self)
        guard let provider = CGDataProvider(data: Data(data) as CFData),
              let image = CGImage(width: width, height: height, bitsPerComponent: 8, bitsPerPixel: 24, bytesPerRow: width * 3, space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue), provider: provider, decode: nil, shouldInterpolate: true, intent: .defaultIntent) else {
            throw FluxError.picture("the picture could not be made")
        }
        return image
    }
}
