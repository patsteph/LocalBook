import Foundation
#if canImport(UIKit)
import UIKit
#endif

/// Keeps the device from idling while an answer streams or Studio makes something.
///
/// iOS: the idle timer (the screen must not auto-lock mid-answer; the lock rule would cancel the work).
/// Mac: a `ProcessInfo` activity, which stops idle sleep and App Nap for the duration.
/// Use it as `let hold = KeepAwake.begin(reason:)`; the hold ends when it is released or `end()` is called.
public final class KeepAwake: @unchecked Sendable {
    private let lock = NSLock()
    private var ended = false
    #if !canImport(UIKit)
    private var activity: NSObjectProtocol?
    #endif

    private init() {}

    @MainActor public static func begin(reason: String) -> KeepAwake {
        let hold = KeepAwake()
        #if canImport(UIKit)
        UIApplication.shared.isIdleTimerDisabled = true
        #else
        hold.activity = ProcessInfo.processInfo.beginActivity(options: [.idleSystemSleepDisabled, .userInitiated], reason: reason)
        #endif
        return hold
    }

    public func end() {
        lock.lock()
        let already = ended
        ended = true
        lock.unlock()
        if already { return }
        #if canImport(UIKit)
        Task { @MainActor in UIApplication.shared.isIdleTimerDisabled = false }
        #else
        if let activity { ProcessInfo.processInfo.endActivity(activity) }
        #endif
    }

    deinit { end() }
}
