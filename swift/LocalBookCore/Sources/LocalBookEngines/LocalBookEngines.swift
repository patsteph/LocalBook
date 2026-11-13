import Foundation
import LocalBookCore
import MLX

/// The model runtimes: Gemma (MLX), EmbeddingGemma 2, the Core ML reranker, Kokoro and Parakeet (FluidAudio).
///
/// Phase 1 moves `Generation/`, `Embedding/`, `Library/RerankerEngine`, `Library/StoppableModel`,
/// `Library/GPUGuard` and `Speech/` here from ios/LocalBookLab/Sources. Kept apart from `LocalBookCore` so the
/// pure parts build and test on CI without compiling MLX.
public enum LocalBookEngines {
    /// Waits (bounded) for everything queued on the GPU to finish. The iPhone's lock rule needs this: the GPU is
    /// withdrawn when the app leaves the foreground, and a step still queued then kills the process.
    public static func drainGPU(timeout: TimeInterval) -> Bool {
        let done = DispatchSemaphore(value: 0)
        DispatchQueue.global(qos: .userInitiated).async {
            Stream.gpu.synchronize()
            done.signal()
        }
        return done.wait(timeout: .now() + timeout) == .success
    }
}
