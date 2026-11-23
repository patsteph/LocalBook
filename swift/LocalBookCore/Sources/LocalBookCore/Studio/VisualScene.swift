import CoreGraphics
import Foundation

/// What a visual is made of once it is laid out: shapes with resolved positions, sizes and style roles, in a
/// 1600×900 frame. The model never sees this; `VisualLayout` builds it from a `VisualSpec`, and every output
/// (the SwiftUI canvas, the PNG, the SVG) is drawn from the same scene, so they cannot disagree. Colours are
/// roles, not values: `VisualTheme` turns a role into a colour for light or dark.
public struct VisualScene: Sendable, Equatable {
    public static let size = CGSize(width: 1600, height: 900)
    public var size: CGSize = VisualScene.size
    public var nodes: [VisualNode] = []
    /// The spec's title, for the picture's accessibility label and the SVG's `<title>`.
    public var title: String = ""
    /// The shrink the layout settled on (1 = the normal type size); the lint reports when it had to go small.
    public var scale: CGFloat = 1

    public init() {}
    public mutating func add(_ node: VisualNode) { nodes.append(node) }

    /// Every rectangle that carries words or a card, for the lint's overlap and overflow checks.
    public var frames: [CGRect] {
        nodes.compactMap { node -> CGRect? in
            switch node {
            case .card(let r, _, _, _, _): return r
            case .text(let t): return t.frame
            case .symbol(_, let r, _): return r
            default: return nil
            }
        }
    }
}

/// A colour's meaning in the picture. The theme gives each a value.
public enum VisualRole: Sendable, Hashable {
    case ink, quiet, surface, rule, background, onAccent
    /// The nth accent (wraps round the theme's palette) and its pale tint.
    case accent(Int), tint(Int)
}

public struct VisualTextStyle: Sendable, Equatable {
    public enum Weight: Sendable { case regular, semibold, bold }
    public enum Align: Sendable { case leading, center, trailing }
    public var size: CGFloat
    public var weight: Weight = .regular
    public var rounded = false
    public var role: VisualRole = .ink
    public var align: Align = .leading

    public init(size: CGFloat, weight: Weight = .regular, rounded: Bool = false, role: VisualRole = .ink, align: Align = .leading) {
        self.size = size; self.weight = weight; self.rounded = rounded; self.role = role; self.align = align
    }
}

/// Words wrapped into a box: the lines are already broken with the real font, so the renderers only place them.
public struct VisualTextBlock: Sendable, Equatable {
    public var frame: CGRect
    public var lines: [String]
    public var style: VisualTextStyle
    public var lineHeight: CGFloat
    public var ascent: CGFloat
    /// Lines were dropped because the box could not hold them; the lint reports it.
    public var truncated: Bool
}

public enum VisualSegment: Sendable, Equatable {
    case move(CGPoint)
    case line(CGPoint)
    case curve(to: CGPoint, control1: CGPoint, control2: CGPoint)
}

public enum VisualNode: Sendable, Equatable {
    /// A rounded box. `hit` is the branch index a tap on it toggles (mind-map branches open in place).
    case card(CGRect, fill: VisualRole, stroke: VisualRole?, radius: CGFloat, hit: Int?)
    case text(VisualTextBlock)
    case stroke([VisualSegment], role: VisualRole, width: CGFloat, dashed: Bool)
    /// A filled polygon (arrowheads).
    case fill([CGPoint], role: VisualRole)
    case dot(CGPoint, radius: CGFloat, fill: VisualRole, stroke: VisualRole?)
    /// An SF Symbol fitted in a box; the SVG draws a dot in its place.
    case symbol(String, CGRect, role: VisualRole)
}

/// The tokens a picture is dressed with. One theme for now (Clean); more are data, not code.
public struct VisualTheme: Sendable, Equatable {
    public struct RGB: Sendable, Equatable {
        public var r: Double, g: Double, b: Double, a: Double
        public init(_ hex: UInt32, alpha: Double = 1) {
            r = Double((hex >> 16) & 0xff) / 255; g = Double((hex >> 8) & 0xff) / 255; b = Double(hex & 0xff) / 255; a = alpha
        }
        /// `#rrggbb` for the SVG.
        public var hex: String { String(format: "#%02x%02x%02x", Int((r * 255).rounded()), Int((g * 255).rounded()), Int((b * 255).rounded())) }
    }
    public struct Palette: Sendable, Equatable {
        public var ink: RGB, quiet: RGB, surface: RGB, rule: RGB, background: RGB, onAccent: RGB
        public var accents: [RGB]
        /// How strong an accent's tint is on this background.
        public var tintAlpha: Double
    }

    public var name: String
    public var light: Palette
    public var dark: Palette
    /// The font family the SVG names; the canvas uses the system font, which is what these measure with.
    public var svgFontFamily = "-apple-system, 'SF Pro Text', 'Helvetica Neue', Helvetica, Arial, sans-serif"

    public func color(_ role: VisualRole, dark isDark: Bool) -> RGB {
        let set = isDark ? dark : light
        switch role {
        case .ink: return set.ink
        case .quiet: return set.quiet
        case .surface: return set.surface
        case .rule: return set.rule
        case .background: return set.background
        case .onAccent: return set.onAccent
        case .accent(let i): return set.accents[((i % set.accents.count) + set.accents.count) % set.accents.count]
        case .tint(let i):
            var c = set.accents[((i % set.accents.count) + set.accents.count) % set.accents.count]
            c.a = set.tintAlpha
            return c
        }
    }

    /// Crisp, quiet, one accent per item: the look the phone has shipped with, refined.
    public static let clean = VisualTheme(
        name: "Clean",
        light: Palette(ink: RGB(0x1d1d1f), quiet: RGB(0x6e6e73), surface: RGB(0xf5f5f7), rule: RGB(0xd2d2d7), background: RGB(0xffffff),
                   onAccent: RGB(0xffffff),
                   accents: [RGB(0x2f6fed), RGB(0xf28c28), RGB(0x2ea04a), RGB(0xe2487e), RGB(0x7c4dff), RGB(0x17a2b8)],
                   tintAlpha: 0.12),
        dark: Palette(ink: RGB(0xf5f5f7), quiet: RGB(0xa1a1a6), surface: RGB(0x2c2c2e), rule: RGB(0x48484a), background: RGB(0x1c1c1e),
                  onAccent: RGB(0xffffff),
                  accents: [RGB(0x5e93ff), RGB(0xffa14a), RGB(0x4cc764), RGB(0xff6b9d), RGB(0x9d7bff), RGB(0x3cc7dd)],
                  tintAlpha: 0.22)
    )
}
