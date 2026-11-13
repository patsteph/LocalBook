import Foundation
import SwiftUI
#if canImport(UIKit)
import UIKit
/// `UIImage` on iOS, `NSImage` on the Mac. Code in the package never names either directly.
public typealias PlatformImage = UIImage
public typealias PlatformColor = UIColor
#elseif canImport(AppKit)
import AppKit
public typealias PlatformImage = NSImage
public typealias PlatformColor = NSColor
#endif

extension PlatformImage {
    /// PNG bytes, the same call on both platforms (iOS has `pngData()`, AppKit needs a bitmap representation).
    public var platformPNGData: Data? {
        #if canImport(UIKit)
        return pngData()
        #else
        guard let tiff = tiffRepresentation, let rep = NSBitmapImageRep(data: tiff) else { return nil }
        return rep.representation(using: .png, properties: [:])
        #endif
    }

    /// JPEG bytes at the given quality (0 to 1).
    public func platformJPEGData(quality: CGFloat) -> Data? {
        #if canImport(UIKit)
        return jpegData(compressionQuality: quality)
        #else
        guard let tiff = tiffRepresentation, let rep = NSBitmapImageRep(data: tiff) else { return nil }
        return rep.representation(using: .jpeg, properties: [.compressionFactor: quality])
        #endif
    }

    /// A `CGImage` for Vision, Core Image and the video writer.
    public var platformCGImage: CGImage? {
        #if canImport(UIKit)
        return cgImage
        #else
        return cgImage(forProposedRect: nil, context: nil, hints: nil)
        #endif
    }
}

extension Image {
    /// A SwiftUI `Image` from a platform image, on both platforms.
    public init(platformImage: PlatformImage) {
        #if canImport(UIKit)
        self.init(uiImage: platformImage)
        #else
        self.init(nsImage: platformImage)
        #endif
    }
}

@MainActor
extension ImageRenderer {
    /// The rendered picture as a platform image (`uiImage` on iOS, `nsImage` on the Mac).
    public var platformImage: PlatformImage? {
        #if canImport(UIKit)
        return uiImage
        #else
        return nsImage
        #endif
    }
}

extension Color {
    /// The window background: `systemBackground` on iOS, `windowBackgroundColor` on the Mac.
    public static var platformBackground: Color {
        #if canImport(UIKit)
        return Color(uiColor: .systemBackground)
        #else
        return Color(nsColor: .windowBackgroundColor)
        #endif
    }
}
