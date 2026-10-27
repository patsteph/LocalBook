import MLX
import UIKit

/// iOS withdraws GPU access the moment the app leaves the foreground, and a GPU step that is still queued then
/// FAILS — MLX turns that into an uncaught C++ exception and the app dies ("Insufficient Permission (to submit
/// GPU work from background)"). Cancelling alone is too slow: the generation loop has already queued the next
/// step. So when the screen is about to lock, stop the work AND wait for the GPU to finish what is queued while
/// we are still allowed to use it.
enum GPUGuard {
    static func protect<T: Sendable>(_ task: Task<T, any Error>, label: String = "answer", onLock: @escaping @Sendable () -> Void = {}) -> NSObjectProtocol {
        NotificationCenter.default.addObserver(forName: UIApplication.willResignActiveNotification, object: nil, queue: nil) { _ in
            GPUGate.close()                                 // 0. prompt processing queues nothing more
            onLock()
            task.cancel()                                   // 1. no new tokens
            SoakTrace.log("GPUGuard[\(label)]: cancelled, draining GPU")
            // 2. let the GPU finish what is queued while we are still allowed to use it — but never wait forever: a
            //    GPU that has already stopped answering must not take the main thread (and the whole app) with it.
            let drained = drain(timeout: 2.0)
            SoakTrace.log("GPUGuard[\(label)]: GPU \(drained ? "idle" : "STILL BUSY after 2 s — not waiting longer")")
            if !drained { GPUGuard.suspect = true }
        }
    }

    /// Set when the GPU did not drain in time after a lock: the next answer reloads the model first.
    nonisolated(unsafe) static var suspect = false

    /// Wait (bounded) for everything queued on the GPU to complete. Runs the wait off the calling thread.
    static func drain(timeout: TimeInterval) -> Bool {
        let done = DispatchSemaphore(value: 0)
        DispatchQueue.global(qos: .userInitiated).async {
            Stream.gpu.synchronize()                        // the GPU's own stream, whatever the default device is
            Thread.sleep(forTimeInterval: 0.04)             // a step queued while the loop was being cancelled…
            Stream.gpu.synchronize()                        // …is drained too
            done.signal()
        }
        return done.wait(timeout: .now() + timeout) == .success
    }

    /// Don't start GPU work unless the app is in front.
    @MainActor static var canStart: Bool { UIApplication.shared.applicationState == .active }
}

/// Set from a notification callback, read from the awaiting code.
final class LockFlag: @unchecked Sendable {
    private let lock = NSLock()
    private var value = false
    func set() { lock.lock(); value = true; lock.unlock() }
    var isSet: Bool { lock.lock(); defer { lock.unlock() }; return value }
}
