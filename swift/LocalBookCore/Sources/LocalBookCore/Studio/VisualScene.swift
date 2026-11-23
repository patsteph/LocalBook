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
    public enum Weight: Sendable { case regular, semibold, bold, heavy }
    public enum Align: Sendable { case leading, center, trailing }
    public var size: CGFloat
    public var weight: Weight = .regular
    /// Figures in the rounded face, whatever the theme (big numbers read better rounded).
    public var rounded = false
    public var role: VisualRole = .ink
    public var align: Align = .leading
    /// The theme's face; the layout sets it when it measures, so the renderers and the metrics agree.
    public var design: VisualTheme.Design = .system

    public init(size: CGFloat, weight: Weight = .regular, rounded: Bool = false, role: VisualRole = .ink, align: Align = .leading) {
        self.size = size; self.weight = weight; self.rounded = rounded; self.role = role; self.align = align
    }
}

/// What a symbol means in the picture; the theme says which SF Symbol draws it.
public enum VisualIcon: String, Sendable, Hashable, CaseIterable {
    case point, benefit, pro, con, before, after
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
    /// A symbol fitted in a box, drawn as the theme's SF Symbol for it; the SVG draws a dot in its place.
    case symbol(VisualIcon, CGRect, role: VisualRole)
}

/// The tokens a picture is dressed with: colours by role for light and dark, a type face, radii, strokes and the
/// symbols. Four themes to start; a theme is data, so a fifth is one more entry in `all`.
public struct VisualTheme: Sendable, Equatable, Identifiable {
    public struct RGB: Sendable, Equatable {
        public var r: Double, g: Double, b: Double, a: Double
        public init(_ hex: UInt32, alpha: Double = 1) {
            r = Double((hex >> 16) & 0xff) / 255; g = Double((hex >> 8) & 0xff) / 255; b = Double(hex & 0xff) / 255; a = alpha
        }
        /// `#rrggbb` for the SVG.
        public var hex: String { String(format: "#%02x%02x%02x", Int((r * 255).rounded()), Int((g * 255).rounded()), Int((b * 255).rounded())) }
        /// Relative luminance (WCAG), for the contrast check.
        public var luminance: Double {
            func lin(_ c: Double) -> Double { c <= 0.03928 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4) }
            return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)
        }
        /// WCAG contrast ratio between two opaque colours.
        public static func contrast(_ a: RGB, _ b: RGB) -> Double {
            let la = a.luminance, lb = b.luminance
            return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)
        }
    }
    public struct Palette: Sendable, Equatable {
        public var ink: RGB, quiet: RGB, surface: RGB, rule: RGB, background: RGB, onAccent: RGB
        public var accents: [RGB]
        /// How strong an accent's tint is on this background.
        public var tintAlpha: Double
    }
    /// The type face: the system face, a serif (Editorial) or the rounded face (Sketch).
    public enum Design: Sendable, Equatable { case system, serif, rounded }

    public var id: String
    public var name: String
    /// One line for the picker.
    public var tagline: String
    public var light: Palette
    public var dark: Palette
    public var design: Design = .system
    /// Headings one step heavier (Deck).
    public var heavyHeadings = false
    /// Corner radii relative to Clean (0.25 squares them, as Editorial does).
    public var radiusScale: CGFloat = 1
    /// Line widths relative to Clean.
    public var strokeScale: CGFloat = 1
    /// Cards drawn with an ink outline, as if ruled by hand (Sketch).
    public var outlinedCards = false
    /// Cards cast a soft shadow (Deck).
    public var shadows = false
    /// The font family the SVG names; the canvas uses the system faces, which is what these measure with.
    public var svgFontFamily = "-apple-system, 'SF Pro Text', 'Helvetica Neue', Helvetica, Arial, sans-serif"
    /// The SF Symbol for each meaning; anything missing falls back to `VisualTheme.defaultIcons`.
    public var icons: [VisualIcon: String] = [:]

    public static let defaultIcons: [VisualIcon: String] = [.point: "sparkle", .benefit: "star.fill", .pro: "checkmark.circle.fill", .con: "xmark.circle.fill", .before: "circle.fill", .after: "circle.fill"]
    public func symbol(for icon: VisualIcon) -> String { icons[icon] ?? Self.defaultIcons[icon] ?? "circle.fill" }

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

    /// The pairs that fall short of readable contrast: ink and quiet words on the background and surfaces need
    /// 4.5, words on an accent need 3 (WCAG). Empty for every shipped theme; the test holds it so.
    public func contrastProblems() -> [String] {
        var out: [String] = []
        for (name, set) in [("light", light), ("dark", dark)] {
            for (w, word) in [("ink", set.ink), ("quiet", set.quiet)] {
                for (g, ground) in [("background", set.background), ("surface", set.surface)] where RGB.contrast(word, ground) < 4.5 {
                    out.append("\(id) \(name): \(w) on \(g) is \(String(format: "%.1f", RGB.contrast(word, ground)))")
                }
            }
            for (i, a) in set.accents.enumerated() where RGB.contrast(set.onAccent, a) < 3 {
                out.append("\(id) \(name): words on accent \(i) are \(String(format: "%.1f", RGB.contrast(set.onAccent, a)))")
            }
        }
        return out
    }

    /// Every theme, in picker order; Clean is the house default until Patrick names another.
    public static let all: [VisualTheme] = [.clean, .editorial, .deck, .sketch]
    /// The theme with this id, or Clean for nil and for ids from a later version.
    public static func named(_ id: String?) -> VisualTheme { all.first { $0.id == id } ?? .clean }

    /// Crisp, quiet, one accent per item: the look the phone has shipped with, refined.
    public static let clean = VisualTheme(
        id: "clean", name: "Clean", tagline: "Quiet surfaces, one colour per item",
        light: Palette(ink: RGB(0x1d1d1f), quiet: RGB(0x6e6e73), surface: RGB(0xf5f5f7), rule: RGB(0xd2d2d7), background: RGB(0xffffff),
                   onAccent: RGB(0xffffff),
                   accents: [RGB(0x2f6fed), RGB(0xd4731a), RGB(0x2ea04a), RGB(0xe2487e), RGB(0x7c4dff), RGB(0x17a2b8)],
                   tintAlpha: 0.12),
        dark: Palette(ink: RGB(0xf5f5f7), quiet: RGB(0xa1a1a6), surface: RGB(0x2c2c2e), rule: RGB(0x48484a), background: RGB(0x1c1c1e),
                  onAccent: RGB(0x1c1c1e),
                  accents: [RGB(0x5e93ff), RGB(0xffa14a), RGB(0x4cc764), RGB(0xff6b9d), RGB(0x9d7bff), RGB(0x3cc7dd)],
                  tintAlpha: 0.22)
    )

    /// Serif headings, square corners, thin rules, restrained ink colours: a newspaper graphic.
    public static let editorial = VisualTheme(
        id: "editorial", name: "Editorial", tagline: "Serif type, rules and restrained colour",
        light: Palette(ink: RGB(0x141414), quiet: RGB(0x5b5b5b), surface: RGB(0xf4f1ea), rule: RGB(0xcfc9bd), background: RGB(0xfbfaf6),
                   onAccent: RGB(0xffffff),
                   accents: [RGB(0x9b2226), RGB(0x1f3a5f), RGB(0x4f6d3a), RGB(0x7a5c2e), RGB(0x3d3d6b), RGB(0x5c6b73)],
                   tintAlpha: 0.10),
        dark: Palette(ink: RGB(0xeae6dc), quiet: RGB(0xa8a39a), surface: RGB(0x2a2824), rule: RGB(0x4a463f), background: RGB(0x1b1a17),
                  onAccent: RGB(0x1b1a17),
                  accents: [RGB(0xd4595d), RGB(0x6f93c4), RGB(0x8fb06f), RGB(0xc99a55), RGB(0x8d8dc9), RGB(0x9aabb5)],
                  tintAlpha: 0.22),
        design: .serif, radiusScale: 0.25, strokeScale: 0.8,
        svgFontFamily: "'New York', Georgia, 'Times New Roman', serif",
        icons: [.point: "diamond.fill", .benefit: "seal.fill", .pro: "plus.circle.fill", .con: "minus.circle.fill"]
    )

    /// Dark in both appearances, heavy type, vivid accents and big numbers: a slide for a room.
    public static let deck = VisualTheme(
        id: "deck", name: "Deck", tagline: "Dark, bold, big numbers",
        light: Palette(ink: RGB(0xf7f7f8), quiet: RGB(0xb5b5bf), surface: RGB(0x26262e), rule: RGB(0x3f3f4a), background: RGB(0x15151b),
                   onAccent: RGB(0x15151b),
                   accents: [RGB(0xffb020), RGB(0x3ddc97), RGB(0x4da3ff), RGB(0xff5c8a), RGB(0xb388ff), RGB(0x2ee6d6)],
                   tintAlpha: 0.20),
        dark: Palette(ink: RGB(0xf7f7f8), quiet: RGB(0xb5b5bf), surface: RGB(0x26262e), rule: RGB(0x3f3f4a), background: RGB(0x15151b),
                  onAccent: RGB(0x15151b),
                  accents: [RGB(0xffb020), RGB(0x3ddc97), RGB(0x4da3ff), RGB(0xff5c8a), RGB(0xb388ff), RGB(0x2ee6d6)],
                  tintAlpha: 0.24),
        heavyHeadings: true, radiusScale: 0.8, strokeScale: 1.1, shadows: true,
        icons: [.point: "bolt.fill", .benefit: "star.fill"]
    )

    /// Rounded type, warm paper, outlined cards and thicker lines: drawn on a whiteboard.
    public static let sketch = VisualTheme(
        id: "sketch", name: "Sketch", tagline: "Warm, rounded, drawn by hand",
        light: Palette(ink: RGB(0x2b2118), quiet: RGB(0x6d5f55), surface: RGB(0xfff7e8), rule: RGB(0xd9c7ae), background: RGB(0xfffbf3),
                   onAccent: RGB(0x2b2118),
                   accents: [RGB(0xe76f51), RGB(0x2a9d8f), RGB(0xe9c46a), RGB(0x8ab17d), RGB(0x7b61ff), RGB(0xf4a261)],
                   tintAlpha: 0.16),
        dark: Palette(ink: RGB(0xf6ead8), quiet: RGB(0xbfae99), surface: RGB(0x2d2721), rule: RGB(0x56493d), background: RGB(0x1e1a16),
                  onAccent: RGB(0x1e1a16),
                  accents: [RGB(0xf08a6e), RGB(0x4db8aa), RGB(0xeccb7a), RGB(0x9fc48f), RGB(0x9d8bff), RGB(0xf7b377)],
                  tintAlpha: 0.26),
        design: .rounded, radiusScale: 1.3, strokeScale: 1.4, outlinedCards: true,
        svgFontFamily: "'SF Pro Rounded', -apple-system, 'Helvetica Neue', Helvetica, Arial, sans-serif",
        icons: [.point: "pencil.and.scribble", .benefit: "hand.thumbsup.fill", .pro: "hand.thumbsup", .con: "hand.thumbsdown"]
    )
}
