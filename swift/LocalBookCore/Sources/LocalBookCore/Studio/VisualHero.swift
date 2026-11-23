import CoreGraphics
import Foundation
import ImageIO

/// The painted backdrop some forms carry behind their title: a wide picture made by an image model from the
/// spec's words, saved beside the item and drawn under vector text (words never go through the image model).
/// Which forms want one, what the model is told, and where the file lives are all here; whether a picture can
/// be made at all is the app's business (`StudioEngine.imagery`), and a form without one draws a tinted band.
public enum VisualHero {
    /// The picture's size in pixels: 16:9, multiples of 16 as diffusion models want.
    public static let width = 1024
    public static let height = 576

    /// The forms that carry a backdrop. Others never ask for one.
    public static func wants(_ layout: VisualSpec.Layout) -> Bool { layout == .valueProposition || layout == .infographic }

    /// What the image model is asked for: the title as a subject, in a style that reads behind white text, with no
    /// words of its own (local image models misspell, so the vector text carries every word).
    public static func prompt(for spec: VisualSpec) -> String {
        let subject = spec.title.trimmingCharacters(in: .whitespacesAndNewlines)
        let hints = spec.items.prefix(3).map(\.title).filter { !$0.isEmpty }.joined(separator: ", ")
        var p = "A wide editorial illustration about \(subject.isEmpty ? "the subject" : subject)."
        if !hints.isEmpty { p += " It touches on \(hints)." }
        p += " Flat vector style, soft gradients, calm composition with open space, muted palette with one accent colour, no text, no letters, no words, no logos, no watermark."
        return p
    }

    /// A stable seed from the title, so the same ask paints the same backdrop on every Mac.
    public static func seed(for spec: VisualSpec) -> UInt64 {
        var h: UInt64 = 5381
        for b in spec.title.lowercased().utf8 { h = (h &* 33) &+ UInt64(b) }
        return h
    }

    /// The file an item's backdrop is saved as, in the Studio folder beside the item.
    public static func fileName(forItem id: String) -> String { id + "-hero.png" }
    public static func url(_ name: String) -> URL { StudioStore.folder.appendingPathComponent(name) }

    // NSCache is thread-safe; the compiler cannot see that.
    nonisolated(unsafe) private static let cache = NSCache<NSString, CGImage>()

    /// The saved backdrop, decoded once and kept; nil when the file is not on this device (the phone, before a
    /// sync) and the form draws its band instead.
    public static func image(named name: String?) -> CGImage? {
        guard let name else { return nil }
        if let hit = cache.object(forKey: name as NSString) { return hit }
        let url = url(name)
        guard FileManager.default.fileExists(atPath: url.path),
              let source = CGImageSourceCreateWithURL(url as CFURL, nil),
              let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else { return nil }
        cache.setObject(image, forKey: name as NSString)
        return image
    }

    /// Writes a backdrop as PNG beside the item; returns its size in bytes.
    @discardableResult
    public static func save(_ image: CGImage, as name: String) throws -> Int {
        let url = url(name)
        guard let dest = CGImageDestinationCreateWithURL(url as CFURL, "public.png" as CFString, 1, nil) else {
            throw CocoaError(.fileWriteUnknown)
        }
        CGImageDestinationAddImage(dest, image, nil)
        guard CGImageDestinationFinalize(dest) else { throw CocoaError(.fileWriteUnknown) }
        cache.setObject(image, forKey: name as NSString)
        return (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? Int) ?? 0
    }

    /// The PNG bytes of a saved backdrop, for the SVG's inline image.
    public static func data(named name: String?) -> Data? {
        guard let name else { return nil }
        return try? Data(contentsOf: url(name))
    }

    /// Removes an item's backdrop when the item goes.
    public static func remove(named name: String?) {
        guard let name else { return }
        cache.removeObject(forKey: name as NSString)
        try? FileManager.default.removeItem(at: url(name))
    }
}
