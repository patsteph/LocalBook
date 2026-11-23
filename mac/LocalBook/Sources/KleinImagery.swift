import CoreGraphics
import Flux2Core
import FluxTextEncoders
import Foundation
import LocalBookCore
import LocalBookEngines
import Observation

/// The Mac's image model for visual backdrops: FLUX.2 Klein 4B through MLX (flux-2-swift-mlx). The 8-bit
/// transformer, the 4-bit Qwen3 text encoder and the small decoder come down after launch, once the models that
/// answer are warm, into this app's own folder; nothing is optional and nothing is asked. The first picture loads the
/// weights (a minute); the transformer then stays loaded, and the text encoder is dropped after each prompt.
@Observable @MainActor
final class KleinImagery: VisualImagery {
    let name = "FLUX.2 Klein 4B"
    /// What Settings says beside the choice: the parts and where they come from, so nobody is surprised by a scan.
    static let note = "Black Forest Labs' FLUX.2 Klein 4B (Germany) with its Qwen3-4B text encoder (Alibaba). About 6 GB. Not for managed work Macs where Qwen is not allowed."
    private(set) var state: VisualImageryState = .absent
    private var pipeline: Flux2Pipeline?

    /// Where the weights live: `…/models/` because the library joins `models/<repo>` under the folder above it.
    static let folder = AppFolders.models.appending(path: "flux/models", directoryHint: .isDirectory)
    static let quantization = Flux2QuantizationConfig(textEncoder: .mlx4bit, transformer: .qint8)

    init() {
        try? FileManager.default.createDirectory(at: Self.folder, withIntermediateDirectories: true)
        ModelRegistry.customModelsDirectory = Self.folder
        TextEncoderModelDownloader.customModelsDirectory = Self.folder
        TextEncoderModelDownloader.reconfigureHubApi()
        if Self.downloaded { state = .ready }
    }

    /// Removes every downloaded part (when the Mac is switched to another model), and forgets the loaded pipeline.
    func deleteWeights() {
        if case .downloading = state { return }
        pipeline = nil
        try? FileManager.default.removeItem(at: Self.folder.deletingLastPathComponent())
        try? FileManager.default.createDirectory(at: Self.folder, withIntermediateDirectories: true)
        state = .absent
        SoakTrace.log("imagery: Klein weights removed")
    }

    /// Something is on disk (for the "remove" button).
    static var anythingOnDisk: Bool {
        ((try? FileManager.default.contentsOfDirectory(atPath: folder.path))?.isEmpty == false)
    }

    /// Every part is on disk.
    nonisolated static var downloaded: Bool {
        Flux2ModelDownloader.isDownloaded(.transformer(.klein4B_8bit))
            && Flux2ModelDownloader.isDownloaded(.vae(.smallDecoder))
            && TextEncoderModelDownloader.isQwen3ModelDownloaded(variant: .qwen3_4B_4bit)
    }

    func prepare() async {
        if case .downloading = state { return }
        if Self.downloaded { state = .ready; return }
        state = .downloading(0, "starting")
        // The three parts, weighted by their size (about 2, 4 and 0.3 GB), so the one figure moves evenly.
        func report(_ from: Double, _ span: Double) -> @Sendable (Double, String) -> Void {
            { p, part in Task { @MainActor in self.state = .downloading(from + min(max(p, 0), 1) * span, part) } }
        }
        do {
            let encoders = TextEncoderModelDownloader()
            _ = try await encoders.downloadQwen3(variant: .qwen3_4B_4bit, progress: report(0, 0.3))
            let pictures = Flux2ModelDownloader()
            _ = try await pictures.download(.transformer(.klein4B_8bit), progress: report(0.3, 0.65))
            _ = try await pictures.download(.vae(.smallDecoder), progress: report(0.95, 0.05))
            state = .ready
            SoakTrace.log("imagery: Klein 4B ready, \(Flux2ModelDownloader.formatSize(Flux2ModelDownloader.downloadedSize())) on disk")
        } catch {
            state = .failed(error.localizedDescription)
            SoakTrace.log("imagery: download failed: \(error.localizedDescription)")
        }
    }

    func hero(prompt: String, width: Int, height: Int, seed: UInt64) async throws -> CGImage {
        let p = pipeline ?? Flux2Pipeline(model: .klein4B, quantization: Self.quantization, vaeVariant: .smallDecoder)
        pipeline = p
        if !p.isLoaded { try await p.loadModels() }
        // Klein is distilled for four steps with the guidance folded in.
        return try await p.generateTextToImage(prompt: prompt, height: height, width: width, steps: 4, guidance: 1.0, seed: seed)
    }

    /// Klein edits natively: the first picture goes in as the reference and comes back redrawn with more detail.
    func refine(_ picture: CGImage, prompt: String, seed: UInt64) async throws -> CGImage {
        guard let p = pipeline, p.isLoaded else { return picture }
        return try await p.generateImageToImage(prompt: prompt, images: [picture], height: picture.height, width: picture.width, steps: 4, guidance: 1.0, seed: seed)
    }
}
