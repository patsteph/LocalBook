import Foundation
#if canImport(UIKit)
import UIKit
#endif

/// The minimal haptic set the iOS design profile allows: light on Send, success when an answer completes,
/// warning on failure. The Mac has no haptics on most hardware, so the same calls do nothing there.
public enum Haptics {
    @MainActor public static func light() {
        #if canImport(UIKit)
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
        #endif
    }

    @MainActor public static func success() {
        #if canImport(UIKit)
        UINotificationFeedbackGenerator().notificationOccurred(.success)
        #endif
    }

    @MainActor public static func warning() {
        #if canImport(UIKit)
        UINotificationFeedbackGenerator().notificationOccurred(.warning)
        #endif
    }
}
