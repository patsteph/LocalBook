import Foundation
import LocalBookCore
import MLX
import MLXLMCommon
import MLXNN

/// The reason the answer model's prompt processing could not simply be cancelled.
///
/// mlx-swift-lm (3.31) read a prompt in 512-token chunks and queued ALL of them on the GPU at once (`asyncEval`),
/// without ever checking whether the task was cancelled. If the screen locks during that stretch — several seconds on a long
/// prompt — iOS withdraws GPU access with a dozen chunks still queued, they fail, and MLX turns the failure into an
/// uncaught C++ exception ("Insufficient Permission (to submit GPU work from background)").
///
/// `StoppableLM` wraps the model and reads the prompt itself, in small chunks that each finish before the next is
/// queued, checking `GPUGate` between them. When the gate is closed (the app is about to leave the foreground) it
/// stops BEFORE queuing more work. Everything else is forwarded to the real model unchanged.
public enum GPUGate {
    public struct Closed: Error, LocalizedError { public var errorDescription: String? { "Stopped because the screen is locking." } }

    private static let lock = NSLock()
    nonisolated(unsafe) private static var closed = false

    public static var isClosed: Bool { lock.lock(); defer { lock.unlock() }; return closed }
    public static func close() { lock.lock(); closed = true; lock.unlock() }
    static func open() { lock.lock(); closed = false; lock.unlock() }
    public static func check() throws { if isClosed { throw Closed() } }

    nonisolated(unsafe) private static var installed = false

    /// Call once at start-up. When the app leaves the foreground the gate closes (prompt reading stops between its small
    /// steps) and the GPU is given a moment to finish what it has, because the GPU refuses work from the background and
    /// MLX turns that refusal into a crash. It opens again when the app is back in front.
    @MainActor public static func installReopenOnActive() {
        guard !installed, AppLifecycle.backgroundWithdrawsGPU else { return }     // the Mac's gate never closes
        installed = true
        NotificationCenter.default.addObserver(forName: AppLifecycle.willResignActive, object: nil, queue: nil) { _ in
            close()
            _ = GPUGuard.drain(timeout: 1.0)                // finish what the GPU already has, while it is still allowed
            SoakTrace.log("GPUGate: closed")
        }
        NotificationCenter.default.addObserver(forName: AppLifecycle.didBecomeActive, object: nil, queue: nil) { _ in
            open()
            SoakTrace.log("GPUGate: open")
        }
    }

    /// Tokens per prompt chunk. Small enough that one chunk finishes in about half a second.
    public static let prefillChunk = 256
}

public final class StoppableLM: Module, LanguageModel, @unchecked Sendable {
    public let inner: any LanguageModel

    public init(_ inner: any LanguageModel) {
        self.inner = inner
        super.init()
    }

    /// Reads the prompt in chunks of `GPUGate.prefillChunk` tokens at most (the iterator's own step when it is
    /// smaller), each one finished before the next is queued, with the gate checked in between. The chunk progress
    /// the iterator asked for is reported the same way the library's own reading would.
    public func prepare(_ input: LMInput, cache: [KVCache], state: LMOutput.State?, prefill: PrefillParameters) throws -> PrepareResult {
        let step = Swift.min(prefill.stepSize ?? GPUGate.prefillChunk, GPUGate.prefillChunk)
        var y = input.text
        let total = y.tokens.size
        var stopped = false
        try GPUGate.check()
        withPreparedCache(cache, lengths: y.sequenceLengths) {
            var carried = state
            while y.tokens.size > step {
                if GPUGate.isClosed || Task.isCancelled { stopped = true; break }       // cancelled answers stop here even after the screen is back
                let chunk = y[.newAxis, ..<step]
                let output = inner(chunk, cache: cache.isEmpty ? nil : cache, state: carried)
                carried = output.state
                eval(cache)                          // finish this chunk before queuing the next
                y = y[step...]
                prefill.progress?(total - y.tokens.size, total)
                Memory.clearCache()
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

    /// The real model builds its derived state (the library calls this once after loading).
    public func prepare() throws { try inner.prepare() }

    public var capabilities: LMModelCapabilities { inner.capabilities }
    public var toolCallFormat: ToolCallFormat? { inner.toolCallFormat }
    public var reasoningConfig: ReasoningConfig? { inner.reasoningConfig }

    public func callAsFunction(_ input: LMInput.Text, cache: [KVCache]?, state: LMOutput.State?) -> LMOutput {
        inner(input, cache: cache, state: state)
    }

    public func callAsFunction(_ inputs: MLXArray, cache: [KVCache]?) -> MLXArray {
        inner(inputs, cache: cache)
    }

    public func newCache(parameters: GenerateParameters?) throws -> [KVCache] {
        try inner.newCache(parameters: parameters)
    }

    public func nextTokenLogits(_ input: LMInput.Text, cache: [KVCache]?, state: LMOutput.State?) -> LMOutput {
        inner.nextTokenLogits(input, cache: cache, state: state)
    }

    /// The same model, with stoppable prompt processing.
    public static func wrap(_ container: ModelContainer) async -> ModelContainer {
        await container.perform { context in
            ModelContainer(context: ModelContext(
                configuration: context.configuration, model: StoppableLM(context.model),
                processor: context.processor, tokenizer: context.tokenizer))
        }
    }
}
