import CoreGraphics
import Foundation

/// Places a `VisualSpec` on the 1600×900 frame as a `VisualScene`. Deterministic Swift: the same spec always
/// gives the same picture. Words are measured with the real font, cards are as tall as their words, and when the
/// whole picture does not fit the frame the type is shrunk in steps (never below half) and laid out again, so the
/// shape never changes and nothing is squeezed. Each layout is one recipe over a few primitives (a row of cards, a
/// grid, an axis, a matrix, a radial fan); later idioms add recipes, not renderers.
public enum VisualLayout {
    public static func scene(for spec: VisualSpec, expanded: Set<Int> = []) -> VisualScene {
        var last = VisualScene()
        for s in [1.0, 0.9, 0.8, 0.7, 0.6, 0.5] as [CGFloat] {
            var b = Builder(spec: spec, expanded: expanded, s: s)
            b.build()
            last = b.scene
            if b.fits { break }
        }
        return last
    }

    /// Rows of at most `maxPerRow` indices, as even as possible (7 → 4 + 3, not 4 + 2 + 1).
    static func rows(_ count: Int, maxPerRow: Int) -> [[Int]] {
        guard count > 0 else { return [] }
        let rowCount = Int((Double(count) / Double(maxPerRow)).rounded(.up))
        let per = Int((Double(count) / Double(rowCount)).rounded(.up))
        return stride(from: 0, to: count, by: per).map { Array($0..<min($0 + per, count)) }
    }
}

struct Builder {
    let spec: VisualSpec
    let expanded: Set<Int>
    let s: CGFloat
    var scene = VisualScene()
    var fits = true

    let W = VisualScene.size.width
    let H = VisualScene.size.height
    let pad: CGFloat = 56

    // The type scale at this shrink.
    var titleStyle: VisualTextStyle { .init(size: 42 * s, weight: .bold) }
    var headingStyle: VisualTextStyle { .init(size: 25 * s, weight: .semibold) }
    var bodyStyle: VisualTextStyle { .init(size: 20 * s, role: .quiet) }
    func captionStyle(_ i: Int) -> VisualTextStyle { .init(size: 17 * s, weight: .semibold, role: .accent(i)) }
    var radius: CGFloat { 20 * s }
    var gap: CGFloat { 24 * s }
    var inner: CGFloat { 22 * s }

    init(spec: VisualSpec, expanded: Set<Int>, s: CGFloat) {
        self.spec = spec; self.expanded = expanded; self.s = s
        scene.title = spec.title
        scene.scale = s
    }

    mutating func build() {
        var y = pad
        if spec.layout != .mindmap, !spec.title.isEmpty {
            y += place(spec.title, titleStyle, x: pad, y: y, width: W - 2 * pad) + 30 * s
        }
        let bottom: CGFloat
        switch spec.layout {
        case .flowchart: bottom = flowchart(top: y)
        case .timeline: bottom = timeline(top: y)
        case .comparison: bottom = comparison(top: y)
        case .infographic: bottom = infographic(top: y, big: false)
        case .statCallouts: bottom = infographic(top: y, big: true)
        case .mindmap: bottom = mindmap()
        case .cycle: bottom = cycle(top: y)
        case .pyramid: bottom = pyramid(top: y)
        case .rankedList: bottom = rankedList(top: y)
        case .tree: bottom = tree(top: y)
        case .clusters: bottom = clusters(top: y)
        case .swimlane: bottom = swimlane(top: y)
        case .decisionTree: bottom = decisionTree(top: y)
        case .prosCons: bottom = twoColumns(top: y, marks: ("checkmark.circle.fill", "xmark.circle.fill"), arrow: false)
        case .beforeAfter: bottom = twoColumns(top: y, marks: ("circle.fill", "circle.fill"), arrow: true)
        case .valueProposition: bottom = valueProposition(top: y)
        case .quadrant: bottom = quadrant(top: y)
        }
        fits = fits && bottom <= H - pad
    }

    // MARK: words

    /// The block `text` makes in a box `width` wide at (x, y); nil for nothing to say.
    func block(_ text: String, _ style: VisualTextStyle, x: CGFloat, y: CGFloat, width: CGFloat, maxLines: Int? = nil) -> VisualTextBlock? {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return nil }
        let wrapped = VisualText.wrap(t, style: style, width: width)
        var lines = wrapped.lines
        let lh = wrapped.lineHeight, ascent = wrapped.ascent
        var truncated = false
        if let m = maxLines, lines.count > m {
            lines = Array(lines.prefix(m))
            lines[m - 1] += "…"
            truncated = true
        }
        return VisualTextBlock(frame: CGRect(x: x, y: y, width: width, height: CGFloat(lines.count) * lh), lines: lines, style: style,
                               lineHeight: lh, ascent: ascent, truncated: truncated)
    }

    /// Adds the block and returns its height (0 for nothing).
    @discardableResult mutating func place(_ text: String, _ style: VisualTextStyle, x: CGFloat, y: CGFloat, width: CGFloat, maxLines: Int? = nil) -> CGFloat {
        guard let b = block(text, style, x: x, y: y, width: width, maxLines: maxLines) else { return 0 }
        scene.add(.text(b))
        return b.frame.height
    }

    // MARK: connectors

    /// A straight arrow from `a` to `b` with its head at `b`.
    mutating func arrow(from a: CGPoint, to b: CGPoint, role: VisualRole = .rule) {
        let head = 12 * s
        let dx = b.x - a.x, dy = b.y - a.y
        let len = max(1, (dx * dx + dy * dy).squareRoot())
        let ux = dx / len, uy = dy / len
        let base = CGPoint(x: b.x - ux * head, y: b.y - uy * head)
        scene.add(.stroke([.move(a), .line(base)], role: role, width: 3 * s, dashed: false))
        scene.add(.fill([b, CGPoint(x: base.x - uy * head * 0.55, y: base.y + ux * head * 0.55),
                         CGPoint(x: base.x + uy * head * 0.55, y: base.y - ux * head * 0.55)], role: role))
    }

    /// An elbow from the bottom of one card to the top of another on the row below, through the gap between rows.
    mutating func elbow(from a: CGPoint, to b: CGPoint, via midY: CGFloat, role: VisualRole = .rule) {
        let head = 12 * s
        scene.add(.stroke([.move(a), .line(CGPoint(x: a.x, y: midY)), .line(CGPoint(x: b.x, y: midY)), .line(CGPoint(x: b.x, y: b.y - head))],
                          role: role, width: 3 * s, dashed: false))
        scene.add(.fill([b, CGPoint(x: b.x - head * 0.55, y: b.y - head), CGPoint(x: b.x + head * 0.55, y: b.y - head)], role: role))
    }

    // MARK: flowchart — numbered cards left to right, rows snaking down

    mutating func flowchart(top: CGFloat) -> CGFloat {
        let rows = VisualLayout.rows(spec.items.count, maxPerRow: 4)
        let per = rows.first?.count ?? 1
        let arrowGap = 48 * s, rowGap = 44 * s
        let cardW = (W - 2 * pad - CGFloat(per - 1) * arrowGap) / CGFloat(per)
        let badge = 18 * s
        var y = top
        var previous: CGPoint?                       // the bottom centre of the last card of the row above
        for row in rows {
            // Measure every card in the row first so they share one height.
            var cards: [(i: Int, title: VisualTextBlock?, detail: VisualTextBlock?, height: CGFloat)] = []
            for i in row {
                let textX = inner + badge * 2 + 12 * s
                let t = block(spec.items[i].title, headingStyle, x: 0, y: 0, width: cardW - textX - inner)
                let th = max(badge * 2, t?.frame.height ?? 0)
                let d = block(spec.items[i].detail, bodyStyle, x: 0, y: 0, width: cardW - 2 * inner)
                let dh = d.map { $0.frame.height + 10 * s } ?? 0
                cards.append((i, t, d, inner + th + dh + inner))
            }
            let h = cards.map(\.height).max() ?? 0
            var x = pad
            for (n, c) in cards.enumerated() {
                let frame = CGRect(x: x, y: y, width: cardW, height: h)
                scene.add(.card(frame, fill: .tint(c.i), stroke: nil, radius: radius, hit: nil))
                let centre = CGPoint(x: x + inner + badge, y: y + inner + badge)
                scene.add(.dot(centre, radius: badge, fill: .accent(c.i), stroke: nil))
                let numberStyle = VisualTextStyle(size: 19 * s, weight: .bold, role: .onAccent, align: .center)
                if let num = block("\(c.i + 1)", numberStyle, x: centre.x - badge, y: centre.y - 11 * s, width: badge * 2) { scene.add(.text(num)) }
                let textX = x + inner + badge * 2 + 12 * s
                var ty = y + inner
                if var t = c.title { t.frame.origin = CGPoint(x: textX, y: ty + max(0, (badge * 2 - t.frame.height) / 2)); scene.add(.text(t)) }
                ty += max(badge * 2, c.title?.frame.height ?? 0) + 10 * s
                if var d = c.detail { d.frame.origin = CGPoint(x: x + inner, y: ty); scene.add(.text(d)) }
                if n > 0 { arrow(from: CGPoint(x: x - arrowGap + 8 * s, y: centre.y), to: CGPoint(x: x - 8 * s, y: centre.y)) }
                if n == 0, let p = previous { elbow(from: p, to: CGPoint(x: x + cardW / 2, y: y - 6 * s), via: y - rowGap / 2) }
                if n == cards.count - 1 { previous = CGPoint(x: x + cardW / 2, y: y + h + 6 * s) }
                x += cardW + arrowGap
            }
            y += h + rowGap
        }
        return y - rowGap
    }

    // MARK: timeline — an axis per row, a marker per event, the words below it

    mutating func timeline(top: CGFloat) -> CGFloat {
        let rows = VisualLayout.rows(spec.items.count, maxPerRow: 5)
        let per = rows.first?.count ?? 1
        let colGap = 28 * s, rowGap = 48 * s
        let colW = (W - 2 * pad - CGFloat(per - 1) * colGap) / CGFloat(per)
        let r = 10 * s
        var y = top
        for row in rows {
            let axisY = y + r
            scene.add(.stroke([.move(CGPoint(x: pad, y: axisY)), .line(CGPoint(x: W - pad, y: axisY))], role: .rule, width: 3 * s, dashed: false))
            var x = pad
            var rowBottom = axisY
            for i in row {
                scene.add(.dot(CGPoint(x: x + r, y: axisY), radius: r, fill: .accent(i), stroke: nil))
                var ty = axisY + r + 14 * s
                let when = place(spec.items[i].value, captionStyle(i), x: x, y: ty, width: colW)
                if when > 0 { ty += when + 4 * s }
                let title = place(spec.items[i].title, headingStyle, x: x, y: ty, width: colW)
                if title > 0 { ty += title + 6 * s }
                ty += place(spec.items[i].detail, bodyStyle, x: x, y: ty, width: colW)
                rowBottom = max(rowBottom, ty)
                x += colW + colGap
            }
            y = rowBottom + rowGap
        }
        return y - rowGap
    }

    // MARK: comparison — a matrix with the options across and the aspects down

    mutating func comparison(top: CGFloat) -> CGFloat {
        let cols = max(1, spec.columns.count)
        let labelW = 230 * s
        let cellGap = 20 * s
        let cellW = (W - 2 * pad - labelW - CGFloat(cols) * cellGap) / CGFloat(cols)
        let labelStyle = VisualTextStyle(size: 19 * s, weight: .semibold, role: .quiet)
        let cellStyle = VisualTextStyle(size: 20 * s)
        var y = top
        // Header: the things compared, each in its own colour.
        var headerH: CGFloat = 0
        for (c, name) in spec.columns.enumerated() {
            let x = pad + labelW + cellGap + CGFloat(c) * (cellW + cellGap)
            headerH = max(headerH, place(name, VisualTextStyle(size: 24 * s, weight: .bold, role: .accent(c)), x: x, y: y, width: cellW))
        }
        y += headerH + 12 * s
        scene.add(.stroke([.move(CGPoint(x: pad, y: y)), .line(CGPoint(x: W - pad, y: y))], role: .rule, width: 2 * s, dashed: false))
        let rowPad = 14 * s
        for (r, row) in spec.rows.enumerated() {
            let label = block(row.label, labelStyle, x: pad + 12 * s, y: 0, width: labelW - 12 * s)
            var cells: [VisualTextBlock?] = []
            for c in 0..<cols {
                let x = pad + labelW + cellGap + CGFloat(c) * (cellW + cellGap)
                cells.append(block(c < row.cells.count ? row.cells[c] : "", cellStyle, x: x, y: 0, width: cellW))
            }
            let h = max(label?.frame.height ?? 0, cells.compactMap { $0?.frame.height }.max() ?? 0) + 2 * rowPad
            if r % 2 == 1 { scene.add(.card(CGRect(x: pad, y: y, width: W - 2 * pad, height: h), fill: .surface, stroke: nil, radius: 10 * s, hit: nil)) }
            if var l = label { l.frame.origin.y = y + rowPad; scene.add(.text(l)) }
            for cell in cells { if var cell { cell.frame.origin.y = y + rowPad; scene.add(.text(cell)) } }
            y += h
            scene.add(.stroke([.move(CGPoint(x: pad, y: y)), .line(CGPoint(x: W - pad, y: y))], role: .rule, width: 1.5 * s, dashed: false))
        }
        return y
    }

    // MARK: infographic — figures in a grid of cards, then the points in two columns

    mutating func infographic(top: CGFloat, big: Bool) -> CGFloat {
        var y = top
        if !spec.stats.isEmpty {
            let rows = VisualLayout.rows(spec.stats.count, maxPerRow: big ? 3 : 4)
            let per = max(2, rows.first?.count ?? 1)
            let cardW = (W - 2 * pad - CGFloat(per - 1) * gap) / CGFloat(per)
            let valueStyle = VisualTextStyle(size: (big ? 84 : 52) * s, weight: .bold, rounded: true, align: big ? .center : .leading)
            let labelStyle = VisualTextStyle(size: (big ? 22 : 18) * s, role: .quiet, align: big ? .center : .leading)
            for row in rows {
                var cards: [(i: Int, value: VisualTextBlock?, label: VisualTextBlock?, height: CGFloat)] = []
                for i in row {
                    var vs = valueStyle; vs.role = .accent(i)
                    let v = block(spec.stats[i].value, vs, x: 0, y: 0, width: cardW - 2 * inner, maxLines: 2)
                    let l = block(spec.stats[i].title, labelStyle, x: 0, y: 0, width: cardW - 2 * inner)
                    cards.append((i, v, l, inner + (v?.frame.height ?? 0) + 6 * s + (l?.frame.height ?? 0) + inner))
                }
                let h = max(cards.map(\.height).max() ?? 0, (big ? 200 : 110) * s)
                var x = pad
                for c in cards {
                    scene.add(.card(CGRect(x: x, y: y, width: cardW, height: h), fill: .tint(c.i), stroke: nil, radius: radius, hit: nil))
                    var ty = y + inner
                    if var v = c.value { v.frame.origin = CGPoint(x: x + inner, y: ty); scene.add(.text(v)); ty += v.frame.height + 6 * s }
                    if var l = c.label { l.frame.origin = CGPoint(x: x + inner, y: ty); scene.add(.text(l)) }
                    x += cardW + gap
                }
                y += h + gap
            }
            y += 10 * s
        }
        if !spec.items.isEmpty {
            let cols = spec.items.count == 1 ? 1 : 2
            let colGap = 40 * s
            let colW = (W - 2 * pad - CGFloat(cols - 1) * colGap) / CGFloat(cols)
            let mark = 26 * s
            var colBottom = [CGFloat](repeating: y, count: cols)
            for (n, item) in spec.items.enumerated() {
                let c = n % cols
                let x = pad + CGFloat(c) * (colW + colGap)
                var ty = colBottom[c]
                scene.add(.symbol("sparkle", CGRect(x: x, y: ty + 2 * s, width: mark, height: mark), role: .accent(n + 2)))
                let textX = x + mark + 14 * s
                let th = place(item.title, headingStyle, x: textX, y: ty, width: colW - mark - 14 * s)
                ty += th + (th > 0 ? 4 * s : 0)
                ty += place(item.detail, bodyStyle, x: textX, y: ty, width: colW - mark - 14 * s)
                colBottom[c] = ty + 18 * s
            }
            y = (colBottom.max() ?? y) - 18 * s
        }
        return y
    }

    // MARK: mind map — the topic in the middle, branches fanning out to both sides on curves

    mutating func mindmap() -> CGFloat {
        let cy = H / 2
        let r = 76 * s
        let reach = 70 * s                              // from the circle's edge to the nearest capsule
        let colW = W / 2 - pad - r - reach
        let leftX = pad, rightX = W / 2 + r + reach
        let capsuleStyle = VisualTextStyle(size: 21 * s, weight: .semibold, role: .onAccent, align: .center)
        let childStyle = VisualTextStyle(size: 17 * s, role: .quiet)
        let moreStyle = VisualTextStyle(size: 16 * s, weight: .semibold)

        /// One branch's blocks relative to its own top-left, and its height.
        struct Branch { var i: Int; var capsule: CGRect; var texts: [VisualTextBlock]; var height: CGFloat }
        func make(_ i: Int, leading: Bool) -> Branch {
            let item = spec.items[i]
            let open = expanded.contains(i)
            let align: VisualTextStyle.Align = leading ? .leading : .trailing
            var cs = capsuleStyle
            let capW = min(colW, VisualText.width(of: item.title, style: cs) + 34 * s)
            cs.align = .center
            var texts: [VisualTextBlock] = []
            var y: CGFloat = 0
            let title = block(item.title, cs, x: leading ? 0 : colW - capW, y: 0, width: capW, maxLines: 2)
            let capH = (title?.frame.height ?? 0) + 16 * s
            if var t = title { t.frame.origin.y = 8 * s; texts.append(t) }
            let capsule = CGRect(x: leading ? 0 : colW - capW, y: 0, width: capW, height: capH)
            y = capH + 8 * s
            let shown = open ? item.children : Array(item.children.prefix(3))
            var child = childStyle; child.align = align
            if open { child.role = .ink }
            for c in shown {
                if let b = block(c, child, x: 0, y: y, width: colW, maxLines: open ? nil : 2) { texts.append(b); y += b.frame.height + 3 * s }
            }
            if open, !item.detail.isEmpty, let b = block(item.detail, child, x: 0, y: y, width: colW) { texts.append(b); y += b.frame.height + 3 * s }
            let hidden = item.children.count - shown.count
            if !open, hidden > 0 {
                var ms = moreStyle; ms.align = align; ms.role = .accent(i)
                if let b = block("+\(hidden) more", ms, x: 0, y: y, width: colW) { texts.append(b); y += b.frame.height }
            }
            return Branch(i: i, capsule: capsule, texts: texts, height: y)
        }

        let right = spec.items.indices.filter { $0 % 2 == 0 }.map { make($0, leading: true) }
        let left = spec.items.indices.filter { $0 % 2 == 1 }.map { make($0, leading: false) }
        let branchGap = 26 * s
        func height(_ side: [Branch]) -> CGFloat { side.map(\.height).reduce(0, +) + CGFloat(max(0, side.count - 1)) * branchGap }
        let tallest = max(height(left), height(right))
        if tallest > H - 2 * pad { fits = false }

        // The topic.
        scene.add(.dot(CGPoint(x: W / 2, y: cy), radius: r, fill: .accent(4), stroke: nil))
        if let t = block(spec.title, VisualTextStyle(size: 20 * s, weight: .bold, role: .onAccent, align: .center), x: W / 2 - r + 10 * s, y: 0, width: 2 * r - 20 * s, maxLines: 3) {
            var tt = t; tt.frame.origin.y = cy - t.frame.height / 2; scene.add(.text(tt))
        }

        for (side, leading) in [(right, true), (left, false)] {
            var y = max(pad, cy - height(side) / 2)
            let x0 = leading ? rightX : leftX
            for b in side {
                let cap = b.capsule.offsetBy(dx: x0, dy: y)
                scene.add(.card(cap, fill: .accent(b.i), stroke: nil, radius: cap.height / 2, hit: b.i))
                for t in b.texts { var tt = t; tt.frame.origin.x += x0; tt.frame.origin.y += y; scene.add(.text(tt)) }
                let from = CGPoint(x: leading ? W / 2 + r : W / 2 - r, y: cy)
                let to = CGPoint(x: leading ? cap.minX : cap.maxX, y: cap.midY)
                let mid = (from.x + to.x) / 2
                scene.add(.stroke([.move(from), .curve(to: to, control1: CGPoint(x: mid, y: from.y), control2: CGPoint(x: mid, y: to.y))],
                                  role: .accent(b.i), width: 4 * s, dashed: false))
                y += b.height + branchGap
            }
        }
        return min(H - pad, pad + tallest)
    }
}
