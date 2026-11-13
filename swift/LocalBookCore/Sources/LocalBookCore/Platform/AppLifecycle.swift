import Foundation
#if canImport(UIKit)
import UIKit
#elseif canImport(AppKit)
import AppKit
#endif

/// The app lifecycle events the engine cares about, with one name on both platforms.
///
/// On the iPhone these are the `UIApplication` notifications. The lock rule depends on them: the GPU is withdrawn
/// the moment the app leaves the foreground, so `willResignActive` must cancel generation (see `GPUGuard`).
/// On the Mac the GPU is never withdrawn, so the Mac maps the same names to `NSApplication`'s notifications and
/// the engine code stays identical; only the policy in the app differs (Phase 2 decides whether the Mac keeps
/// generating in the background, which it can).
public enum AppLifecycle {
    public static var willResignActive: Notification.Name {
        #if canImport(UIKit)
        return UIApplication.willResignActiveNotification
        #else
        return NSApplication.willResignActiveNotification
        #endif
    }

    public static var didBecomeActive: Notification.Name {
        #if canImport(UIKit)
        return UIApplication.didBecomeActiveNotification
        #else
        return NSApplication.didBecomeActiveNotification
        #endif
    }

    public static var didEnterBackground: Notification.Name {
        #if canImport(UIKit)
        return UIApplication.didEnterBackgroundNotification
        #else
        return NSApplication.didHideNotification
        #endif
    }

    public static var willEnterForeground: Notification.Name {
        #if canImport(UIKit)
        return UIApplication.willEnterForegroundNotification
        #else
        return NSApplication.didUnhideNotification
        #endif
    }

    /// iOS posts these around the lock screen (the data-protection keybag). The Mac has no equivalent, so the
    /// names are posted by nobody there and the observers simply never fire.
    public static var protectedDataWillBecomeUnavailable: Notification.Name {
        #if canImport(UIKit)
        return UIApplication.protectedDataWillBecomeUnavailableNotification
        #else
        return Notification.Name("LocalBookCore.protectedDataWillBecomeUnavailable")
        #endif
    }

    public static var protectedDataDidBecomeAvailable: Notification.Name {
        #if canImport(UIKit)
        return UIApplication.protectedDataDidBecomeAvailableNotification
        #else
        return Notification.Name("LocalBookCore.protectedDataDidBecomeAvailable")
        #endif
    }

    public static var didReceiveMemoryWarning: Notification.Name {
        #if canImport(UIKit)
        return UIApplication.didReceiveMemoryWarningNotification
        #else
        return Notification.Name("LocalBookCore.didReceiveMemoryWarning")
        #endif
    }

    /// Whether the app is in front. Used before starting GPU work on the iPhone.
    @MainActor public static var isActive: Bool {
        #if canImport(UIKit)
        return UIApplication.shared.applicationState == .active
        #else
        return NSApplication.shared.isActive
        #endif
    }

    /// Whether leaving the foreground withdraws the GPU. True on the iPhone, false on the Mac.
    public static var backgroundWithdrawsGPU: Bool {
        #if canImport(UIKit)
        return true
        #else
        return false
        #endif
    }
}
