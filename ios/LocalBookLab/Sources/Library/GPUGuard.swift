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
            onLock()
            task.cancel()                                   // 1. no new tokens
            SoakTrace.log("GPUGuard[\(label)]: cancelled, draining GPU")
            Stream().synchronize()                          // 2. finish the step already on the GPU, while that is still allowed
            Thread.sleep(forTimeInterval: 0.04)             // 3. a step the loop queued while it was being cancelled…
            Stream().synchronize()                          //    …is drained too
            SoakTrace.log("GPUGuard[\(label)]: GPU idle")
        }
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
