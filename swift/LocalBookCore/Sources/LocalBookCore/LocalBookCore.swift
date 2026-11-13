import Foundation

/// The engine LocalBook runs on the iPhone and, from Phase 2, on the Mac.
///
/// Everything in this module is platform-neutral Swift. Where the two apps differ (lifecycle notifications,
/// images, keeping the screen awake, where data lives) the code goes through the `Platform/` shims, never
/// through `UIKit` or `AppKit` directly. The two product apps and the Lab add their own UI on top.
public enum LocalBookCore {
    /// The package version. Bumped by hand when the on-disk formats the two apps share change.
    public static let version = "0.1.0"
}
