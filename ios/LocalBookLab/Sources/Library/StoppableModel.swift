import Foundation
import MLX
import MLXLMCommon
import MLXNN
import UIKit

/// The reason the answer model's prompt processing could not simply be cancelled.
///
/// mlx-swift-lm reads a prompt in 512-token chunks and queues ALL of them on the GPU at once (`asyncEval`), without
/// ever checking whether the task was cancelled. If the screen locks during that stretch — several seconds on a long
/// prompt — iOS withdraws GPU access with a dozen chunks still queued, they fail, and MLX turns the failure into an
/// uncaught C++ exception ("Insufficient Permission (to submit GPU work from background)").
///
/// `StoppableLM` wraps the model and reads the prompt itself, in small chunks that each finish before the next is
/// queued, checking `GPUGate` between them. When the gate is closed (the app is about to leave the foreground) it
/// stops BEFORE queuing more work. Everything else is forwarded to the real model unchanged.
enum GPUGate {
    struct Closed: Error, LocalizedError { var errorDescription: String? { "Stopped because the screen is locking." } }

    private static let lock = NSLock()
    nonisolated(unsafe) private static var closed = false

    static var isClosed: Bool { lock.lock(); defer { lock.unlock() }; return closed }
    static func close() { lock.lock(); closed = true; lock.unlock() }
    static func open() { lock.lock(); closed = false; lock.unlock() }
    static func check() throws { if isClosed { throw Closed() } }

    nonisolated(unsafe) private static var installed = false

    /// Call once at start-up. When the app leaves the foreground the gate closes (prompt reading stops between its small
    /// steps) and the GPU is given a moment to finish what it has, because the GPU refuses work from the background and
    /// MLX turns that refusal into a crash. It opens again when the app is back in front.
    @MainActor static func installReopenOnActive() {
        guard !installed else { return }
        installed = true
        NotificationCenter.default.addObserver(forName: UIApplication.willResignActiveNotification, object: nil, queue: nil) { _ in
            close()
            _ = GPUGuard.drain(timeout: 1.0)                // finish what the GPU already has, while it is still allowed
            SoakTrace.log("GPUGate: closed")
        }
        NotificationCenter.default.addObserver(forName: UIApplication.didBecomeActiveNotification, object: nil, queue: nil) { _ in
            open()
            SoakTrace.log("GPUGate: open")
        }
    }

    /// Tokens per prompt chunk. Small enough that one chunk finishes in about half a second.
    static let prefillChunk = 256
}

final class StoppableLM: Module, LanguageModel, @unchecked Sendable {
    let inner: any LanguageModel

    init(_ inner: any LanguageModel) {
        self.inner = inner
        super.init()
    }

    func prepare(_ input: LMInput, cache: [KVCache], windowSize: Int?) throws -> PrepareResult {
        let step = min(windowSize ?? GPUGate.prefillChunk, GPUGate.prefillChunk)
        var y = input.text
        var stopped = false
        try GPUGate.check()
        withPreparedCache(cache, lengths: y.sequenceLengths) {
            var state: LMOutput.State?
            while y.tokens.size > step {
                if GPUGate.isClosed || Task.isCancelled { stopped = true; break }       // cancelled answers stop here even after the screen is back
                let chunk = y[.newAxis, ..<step]
                let output = inner(chunk, cache: cache.isEmpty ? nil : cache, state: state)
                state = output.state
                eval(cache)                          // finish this chunk before queuing the next
                y = y[step...]
            }
            if !stopped {
                if GPUGate.isClosed || Task.isCancelled { stopped = true } else { eval(cache) }
            }
        }
        if stopped {
            SoakTrace.log("StoppableLM: prompt processing stopped at the gate (\(y.tokens.size) tokens left)")
            throw GPUGate.Closed()
        }
        return .tokens(y)
    }

    func callAsFunction(_ input: LMInput.Text, cache: [KVCache]?, state: LMOutput.State?) -> LMOutput {
        inner(input, cache: cache, state: state)
    }

    func callAsFunction(_ inputs: MLXArray, cache: [KVCache]?) -> MLXArray {
        inner(inputs, cache: cache)
    }

    func newCache(parameters: GenerateParameters?) -> [KVCache] {
        inner.newCache(parameters: parameters)
    }

    /// The same model, with stoppable prompt processing.
    static func wrap(_ container: ModelContainer) async -> ModelContainer {
        await container.perform { context in
            ModelContainer(context: ModelContext(
                configuration: context.configuration, model: StoppableLM(context.model),
                processor: context.processor, tokenizer: context.tokenizer))
        }
    }
}
