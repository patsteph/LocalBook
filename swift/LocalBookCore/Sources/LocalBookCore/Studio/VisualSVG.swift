import CoreGraphics
import Foundation

/// Writes a scene as an SVG file: the same geometry the canvas draws, as vectors that open in a browser, Keynote
/// or Figma and can sit inline in an exported document. Lines are already broken, so the SVG names each line.
public enum VisualSVG {
    public static func render(_ scene: VisualScene, theme: VisualTheme = .clean, dark: Bool = false) -> String {
        func c(_ role: VisualRole) -> String { theme.color(role, dark: dark).hex }
        func f(_ v: CGFloat) -> String { v == v.rounded() ? String(Int(v)) : String(format: "%.1f", v) }
        func alpha(_ role: VisualRole) -> String { let a = theme.color(role, dark: dark).a; return a < 1 ? " fill-opacity='\(f(CGFloat(a)))'" : "" }
        func esc(_ s: String) -> String {
            s.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;").replacingOccurrences(of: ">", with: "&gt;")
        }
        let w = scene.size.width, h = scene.size.height
        var out = ["<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 \(f(w)) \(f(h))' width='\(f(w))' height='\(f(h))' font-family=\"\(theme.svgFontFamily)\">",
                   "<title>\(esc(scene.title))</title>",
                   "<rect width='\(f(w))' height='\(f(h))' fill='\(c(.background))'/>"]
        for node in scene.nodes {
            switch node {
            case .card(let r, let fill, let stroke, let radius, _):
                var s = "<rect x='\(f(r.minX))' y='\(f(r.minY))' width='\(f(r.width))' height='\(f(r.height))' rx='\(f(radius))' fill='\(c(fill))'\(alpha(fill))"
                if let stroke { s += " stroke='\(c(stroke))' stroke-width='2'" }
                out.append(s + "/>")
            case .text(let t):
                let weight = switch t.style.weight { case .regular: "400"; case .semibold: "600"; case .bold: "700" }
                let anchor: String, x: CGFloat
                switch t.style.align {
                case .leading: anchor = "start"; x = t.frame.minX
                case .center: anchor = "middle"; x = t.frame.midX
                case .trailing: anchor = "end"; x = t.frame.maxX
                }
                for (i, line) in t.lines.enumerated() {
                    let y = t.frame.minY + CGFloat(i) * t.lineHeight + t.ascent + (t.lineHeight - t.ascent) * 0.1
                    out.append("<text x='\(f(x))' y='\(f(y))' font-size='\(f(t.style.size))' font-weight='\(weight)' text-anchor='\(anchor)' fill='\(c(t.style.role))'>\(esc(line))</text>")
                }
            case .stroke(let segments, let role, let width, let dashed):
                var d = ""
                for seg in segments {
                    switch seg {
                    case .move(let p): d += "M\(f(p.x)) \(f(p.y))"
                    case .line(let p): d += "L\(f(p.x)) \(f(p.y))"
                    case .curve(let to, let c1, let c2): d += "C\(f(c1.x)) \(f(c1.y)) \(f(c2.x)) \(f(c2.y)) \(f(to.x)) \(f(to.y))"
                    }
                }
                out.append("<path d='\(d)' fill='none' stroke='\(c(role))' stroke-width='\(f(width))' stroke-linecap='round' stroke-linejoin='round'\(dashed ? " stroke-dasharray='6 6'" : "")/>")
            case .fill(let points, let role):
                out.append("<polygon points='\(points.map { "\(f($0.x)),\(f($0.y))" }.joined(separator: " "))' fill='\(c(role))'/>")
            case .dot(let p, let radius, let fill, let stroke):
                var s = "<circle cx='\(f(p.x))' cy='\(f(p.y))' r='\(f(radius))' fill='\(c(fill))'\(alpha(fill))"
                if let stroke { s += " stroke='\(c(stroke))' stroke-width='2'" }
                out.append(s + "/>")
            case .symbol(_, let r, let role):
                out.append("<circle cx='\(f(r.midX))' cy='\(f(r.midY))' r='\(f(r.width * 0.3))' fill='\(c(role))'/>")
            }
        }
        out.append("</svg>")
        return out.joined(separator: "\n")
    }
}
