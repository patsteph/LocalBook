import CoreGraphics
import FluxSchnell
import Foundation
import LocalBookCore
import LocalBookEngines
import Observation

/// The Mac's image model for Macs that cannot carry Qwen: FLUX.1 schnell (Black Forest Labs, Apache 2.0) with
/// its own T5 and CLIP text encoders, in Swift on MLX from the package's FluxSchnell target. About 9.6 GB,
/// fetched after launch once the answer model is warm, into this app's own folder. Every part is from the FLUX
/// checkpoint itself; nothing from any other model family is downloaded.
@Observable @MainActor
final class SchnellImagery: VisualImagery {
    let name = "FLUX.1 schnell"
    /// What Settings says beside the choice, so a scan holds no surprises.
    static let note = "Black Forest Labs' FLUX.1 schnell (Germany, Apache 2.0) with its own T5 (Google) and CLIP (OpenAI) text encoders. About 9.6 GB. No part from any other model family; suits managed work Macs."
    private(set) var state: VisualImageryState = .absent
    private var pipeline: FluxSchnellPipeline?

    static let folder = AppFolders.models.appending(path: "flux-schnell", directoryHint: .isDirectory)

    init() {
        try? FileManager.default.createDirectory(at: Self.folder, withIntermediateDirectories: true)
        if Self.downloaded { state = .ready }
    }

    static var downloaded: Bool { FluxSchnellStore.isDownloaded(root: folder) }

    /// Something is on disk (for the "remove" button).
    static var anythingOnDisk: Bool { FluxSchnellStore.size(root: folder) > 0 }

    /// Removes the weights (when the Mac is switched to another model) and forgets the loaded pipeline.
    func deleteWeights() {
        if case .downloading = state { return }
        pipeline = nil
        try? FileManager.default.removeItem(at: Self.folder)
        try? FileManager.default.createDirectory(at: Self.folder, withIntermediateDirectories: true)
        state = .absent
        SoakTrace.log("imagery: schnell weights removed")
    }

    func prepare() async {
        if case .downloading = state { return }
        if Self.downloaded { state = .ready; return }
        state = .downloading(0, "starting")
        do {
            try await FluxSchnellStore.download(root: Self.folder) { p, part in
                Task { @MainActor in self.state = .downloading(p, part) }
            }
            state = .ready
            let gb = Double(FluxSchnellStore.size(root: Self.folder)) / 1_000_000_000
            SoakTrace.log("imagery: FLUX.1 schnell ready, \(String(format: "%.1f", gb)) GB on disk")
        } catch {
            state = .failed(error.localizedDescription)
            SoakTrace.log("imagery: schnell download failed: \(error.localizedDescription)")
        }
    }

    private func loaded() async -> FluxSchnellPipeline {
        if let pipeline { return pipeline }
        let p = FluxSchnellPipeline(root: Self.folder)
        await p.setLog { line in SoakTrace.log("imagery: \(line)") }
        pipeline = p
        return p
    }

    /// Schnell is distilled for four steps.
    func hero(prompt: String, width: Int, height: Int, seed: UInt64) async throws -> CGImage {
        try await loaded().generate(prompt: prompt, width: width, height: height, steps: 4, seed: seed)
    }

    /// The detail pass: the picture goes back in at half strength over eight steps (four are run), so the
    /// composition holds and the model renews texture, edges and light.
    func refine(_ picture: CGImage, prompt: String, seed: UInt64) async throws -> CGImage {
        try await loaded().generate(prompt: prompt, width: picture.width, height: picture.height, steps: 8, seed: seed, picture: picture, strength: 0.5)
    }
}
