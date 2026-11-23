import CoreGraphics
import Foundation

/// Checks a laid-out scene for the defects a reader notices first: words cut off, words or cards past the edge,
/// cards on top of each other, nothing to say, and type shrunk until it is hard to read. It costs nothing and
/// runs on every picture; later slices send its findings back to the model for one more try.
public enum VisualLint {
    public struct Finding: Sendable, Equatable, CustomStringConvertible {
        public enum Kind: String, Sendable { case cut, offSlide, overlap, empty, tiny }
        public var kind: Kind
        public var message: String
        public var description: String { "\(kind.rawValue): \(message)" }
    }

    public static func check(_ scene: VisualScene) -> [Finding] {
        var out: [Finding] = []
        let frame = CGRect(origin: .zero, size: scene.size)
        var cards: [CGRect] = []
        var words = 0
        for node in scene.nodes {
            switch node {
            case .text(let t):
                words += t.lines.count
                if t.truncated { out.append(Finding(kind: .cut, message: "“\(t.lines.first ?? "")” lost lines")) }
                if !frame.contains(t.frame) { out.append(Finding(kind: .offSlide, message: "“\(t.lines.first ?? "")” runs past the edge")) }
            case .card(let r, _, _, _, _):
                if !frame.contains(r.insetBy(dx: -0.5, dy: -0.5)) { out.append(Finding(kind: .offSlide, message: "a card at \(Int(r.minX)),\(Int(r.minY)) runs past the edge")) }
                cards.append(r)
            default: break
            }
        }
        for (i, a) in cards.enumerated() {
            for b in cards[(i + 1)...] where a.intersects(b) && !a.contains(b) && !b.contains(a) {
                let x = a.intersection(b)
                if x.width > 2, x.height > 2 { out.append(Finding(kind: .overlap, message: "cards overlap at \(Int(x.minX)),\(Int(x.minY))")) }
            }
        }
        if words == 0 { out.append(Finding(kind: .empty, message: "no words on the slide")) }
        if scene.scale < 0.7 { out.append(Finding(kind: .tiny, message: "type shrunk to \(Int(scene.scale * 100)) % to fit")) }
        return out
    }
}
