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

    /// The clause every prompt ends with: words never go through the image model (it misspells), the vector text
    /// carries them, and the title sits on the upper part of the picture under a dark veil.
    public static let noWords = "No text, no letters, no words, no numbers, no logos, no watermark. Calm composition with quiet open space across the upper third."

    /// The art direction of each theme: what kind of painting sits behind its cards.
    public static func direction(forTheme id: String?) -> String {
        switch id {
        case "editorial": "Flat editorial illustration in the manner of a quality magazine: limited muted palette, bold simple shapes, subtle paper grain, confident negative space."
        case "deck": "Clean isometric illustration on a deep dark background: glowing accent colours, soft volumetric light, crisp geometric forms, a sense of depth."
        case "sketch": "Loose ink drawing with watercolour washes on cream paper: expressive hand-drawn lines, soft colour bleeds, airy and warm."
        default: "Modern flat vector illustration: soft gradients, rounded geometric shapes, a muted palette with one or two accent colours, light and airy."
        }
    }

    /// What the image model is asked for when the answer model writes no art direction: the title as a subject, the
    /// first points as hints, in the theme's style, with no words of its own.
    public static func prompt(for spec: VisualSpec) -> String {
        let subject = spec.title.trimmingCharacters(in: .whitespacesAndNewlines)
        let hints = spec.items.prefix(3).map(\.title).filter { !$0.isEmpty }.joined(separator: ", ")
        var p = "A wide illustration about \(subject.isEmpty ? "the subject" : subject)."
        if !hints.isEmpty { p += " It touches on \(hints)." }
        p += " " + direction(forTheme: spec.theme) + " " + noWords
        return p
    }

    /// The answer model writes the art direction: one rich sentence that turns the title into a wordless scene. A
    /// language model is far better than a template at finding the visual metaphor, and the image model follows a
    /// concrete description far better than an abstract subject.
    public static func enrichment(for spec: VisualSpec) -> (instructions: String, prompt: String, tokens: Int) {
        let subject = spec.title.trimmingCharacters(in: .whitespacesAndNewlines)
        let points = (spec.items + spec.stats).prefix(5).map { $0.title + ($0.detail.isEmpty ? "" : ": " + $0.detail) }.joined(separator: "; ")
        let instructions = "You are an art director briefing an illustrator. You describe one wordless picture in one paragraph of at most 60 words: the concrete scene or visual metaphor, its main objects, the composition, the light and the colours. Never mention text, letters, labels, charts or diagrams. Reply with the paragraph only."
        let prompt = "A wide backdrop illustration for a one-page summary titled “\(subject.isEmpty ? "Overview" : subject)”. The page covers: \(points.isEmpty ? "the subject" : points). Style: \(direction(forTheme: spec.theme))"
        return (instructions, prompt, 140)
    }

    /// The model's paragraph made into the image prompt: fences and quotes off, one paragraph, cut at a sentence
    /// before 500 characters, the theme's style and the no-words clause on the end. nil for an empty or odd reply.
    public static func enriched(_ reply: String, for spec: VisualSpec) -> String? {
        var t = reply.replacingOccurrences(of: "```", with: "").trimmingCharacters(in: .whitespacesAndNewlines)
        t = t.trimmingCharacters(in: CharacterSet(charactersIn: "\"“”'"))
        if let para = t.components(separatedBy: "\n\n").first { t = para.replacingOccurrences(of: "\n", with: " ") }
        guard t.count >= 20, !t.lowercased().hasPrefix("i can") else { return nil }
        if t.count > 500 {
            let head = String(t.prefix(500))
            if let dot = head.lastIndex(where: { ".!".contains($0) }) { t = String(head[...dot]) } else { t = head }
        }
        return t + " " + direction(forTheme: spec.theme) + " " + noWords
    }

    /// The second pass: the same picture painted again from itself, for finer detail and cleaner shapes.
    public static func refinePrompt(for spec: VisualSpec) -> String {
        "The same picture, redrawn with finer detail, cleaner shapes, richer lighting and smoother gradients. Keep the composition, the subject and the colours exactly. " + direction(forTheme: spec.theme) + " " + noWords
    }

    /// Up to three colours that carry the picture (the most common saturated, mid-light hues), as `#rrggbb`, for
    /// `VisualSpec.palette`. The picture is read at thumbnail size; a grey or near-black picture gives fewer, or none.
    public static func palette(from image: CGImage, count: Int = 3) -> [String] {
        let w = 64, h = 36
        var pixels = [UInt8](repeating: 0, count: w * h * 4)
        let drawn = pixels.withUnsafeMutableBytes { raw -> Bool in
            guard let ctx = CGContext(data: raw.baseAddress, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4,
                                      space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return false }
            ctx.interpolationQuality = .medium
            ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
            return true
        }
        guard drawn else { return [] }
        // 4 bits per channel: 4096 bins; each bin keeps a running mean so the colour returned is real, not the bin centre.
        var count4 = [Int](repeating: 0, count: 4096), sum = [Double](repeating: 0, count: 4096 * 3)
        for i in stride(from: 0, to: pixels.count, by: 4) {
            let r = Double(pixels[i]) / 255, g = Double(pixels[i + 1]) / 255, b = Double(pixels[i + 2]) / 255
            let mx = max(r, g, b), mn = min(r, g, b)
            let sat = mx == 0 ? 0 : (mx - mn) / mx
            guard sat > 0.25, mx > 0.2, (r + g + b) / 3 < 0.9 else { continue }      // not grey, not black, not white
            let bin = (Int(pixels[i]) >> 4) << 8 | (Int(pixels[i + 1]) >> 4) << 4 | (Int(pixels[i + 2]) >> 4)
            count4[bin] += 1; sum[bin * 3] += r; sum[bin * 3 + 1] += g; sum[bin * 3 + 2] += b
        }
        var picked: [VisualTheme.RGB] = []
        for bin in count4.indices.sorted(by: { count4[$0] > count4[$1] }) where count4[bin] >= 8 && picked.count < count {
            let n = Double(count4[bin])
            let c = VisualTheme.RGB(hex: String(format: "%02x%02x%02x", Int(sum[bin * 3] / n * 255), Int(sum[bin * 3 + 1] / n * 255), Int(sum[bin * 3 + 2] / n * 255)))!
            // Distinct from what is already picked: at least a visible hue or lightness step apart.
            if picked.allSatisfy({ abs($0.r - c.r) + abs($0.g - c.g) + abs($0.b - c.b) > 0.35 }) { picked.append(c) }
        }
        return picked.map(\.hex)
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
