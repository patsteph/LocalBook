import CoreGraphics
import Foundation

/// The recipes for the forms added in slice 2 of the visuals engine, over the same primitives as the first five:
/// measured text blocks, cards, dots, strokes and arrowheads. Each one takes the content top and returns the bottom
/// it used, so `Builder.build` can tell whether the picture fits.
extension Builder {
    /// An arrowhead pointing along `direction` with its tip at `tip`.
    mutating func head(at tip: CGPoint, direction: CGPoint, role: VisualRole) {
        let head = 12 * s
        let len = max(1, (direction.x * direction.x + direction.y * direction.y).squareRoot())
        let ux = direction.x / len, uy = direction.y / len
        let base = CGPoint(x: tip.x - ux * head, y: tip.y - uy * head)
        scene.add(.fill([tip, CGPoint(x: base.x - uy * head * 0.55, y: base.y + ux * head * 0.55),
                         CGPoint(x: base.x + uy * head * 0.55, y: base.y - ux * head * 0.55)], role: role))
    }

    /// A curved arrow from `a` to `b` bowing away from `centre`.
    mutating func curvedArrow(from a: CGPoint, to b: CGPoint, awayFrom centre: CGPoint, role: VisualRole) {
        func out(_ p: CGPoint, _ k: CGFloat) -> CGPoint { CGPoint(x: centre.x + (p.x - centre.x) * k, y: centre.y + (p.y - centre.y) * k) }
        let c1 = out(CGPoint(x: a.x * 0.7 + b.x * 0.3, y: a.y * 0.7 + b.y * 0.3), 1.12)
        let c2 = out(CGPoint(x: a.x * 0.3 + b.x * 0.7, y: a.y * 0.3 + b.y * 0.7), 1.12)
        scene.add(.stroke([.move(a), .curve(to: b, control1: c1, control2: c2)], role: role, width: 3 * s, dashed: false))
        head(at: b, direction: CGPoint(x: b.x - c2.x, y: b.y - c2.y), role: role)
    }

    /// A card with a title and an optional detail, measured first: returns the blocks and the height for `width`.
    func cardContent(title: String, detail: String, width: CGFloat, titleStyle: VisualTextStyle? = nil, detailLines: Int? = nil) -> (title: VisualTextBlock?, detail: VisualTextBlock?, height: CGFloat) {
        let t = block(title, titleStyle ?? headingStyle, x: 0, y: 0, width: width - 2 * inner)
        let d = block(detail, bodyStyle, x: 0, y: 0, width: width - 2 * inner, maxLines: detailLines)
        let h = inner + (t?.frame.height ?? 0) + (t != nil && d != nil ? 8 * s : 0) + (d?.frame.height ?? 0) + inner
        return (t, d, h)
    }

    /// Places the card measured by `cardContent` at `frame` (its blocks stacked from the top inset).
    mutating func placeCard(_ c: (title: VisualTextBlock?, detail: VisualTextBlock?, height: CGFloat), in frame: CGRect, fill: VisualRole, hit: Int? = nil) {
        scene.add(.card(frame, fill: fill, stroke: nil, radius: radius, hit: hit))
        var ty = frame.minY + inner
        if var t = c.title { t.frame.origin = CGPoint(x: frame.minX + inner, y: ty); scene.add(.text(t)); ty += t.frame.height + 8 * s }
        if var d = c.detail { d.frame.origin = CGPoint(x: frame.minX + inner, y: ty); scene.add(.text(d)) }
    }

    // MARK: cycle — stages round an ellipse, the last leading back to the first

    mutating func cycle(top: CGFloat) -> CGFloat {
        let n = spec.items.count
        guard n > 0 else { return top }
        let cardW = 290 * s
        var cards: [(title: VisualTextBlock?, detail: VisualTextBlock?, height: CGFloat)] = []
        // Whole sentences: a card grows to its words and the shrink loop keeps the ring inside the frame.
        for item in spec.items { cards.append(cardContent(title: item.title, detail: item.detail, width: cardW)) }
        let cardH = cards.map(\.height).max() ?? 0
        let centre = CGPoint(x: W / 2, y: top + (H - pad - top) / 2)
        let rx = W / 2 - pad - cardW / 2, ry = (H - pad - top) / 2 - cardH / 2
        func at(_ angle: CGFloat) -> CGPoint { CGPoint(x: centre.x + rx * cos(angle), y: centre.y + ry * sin(angle)) }
        let step = 2 * CGFloat.pi / CGFloat(n)
        var frames: [CGRect] = []
        for i in 0..<n {
            let p = at(-CGFloat.pi / 2 + step * CGFloat(i))
            frames.append(CGRect(x: p.x - cardW / 2, y: p.y - cardH / 2, width: cardW, height: cardH))
        }
        // Arrows first so the cards sit on top of their ends.
        for i in 0..<n where n > 1 {
            let a = -CGFloat.pi / 2 + step * CGFloat(i), b = a + step
            curvedArrow(from: at(a + step * 0.34), to: at(b - step * 0.34), awayFrom: centre, role: .rule)
        }
        for (i, f) in frames.enumerated() {
            placeCard(cards[i], in: f, fill: .tint(i))
            let badge = 15 * s
            scene.add(.dot(CGPoint(x: f.minX, y: f.minY), radius: badge, fill: .accent(i), stroke: nil))
            if let num = block("\(i + 1)", VisualTextStyle(size: 16 * s, weight: .bold, role: .onAccent, align: .center), x: f.minX - badge, y: f.minY - 9 * s, width: badge * 2) { scene.add(.text(num)) }
        }
        if n > 1, !spec.title.isEmpty, let t = block(spec.title, VisualTextStyle(size: 24 * s, weight: .semibold, role: .quiet, align: .center), x: centre.x - 160 * s, y: 0, width: 320 * s, maxLines: 2) {
            var tt = t; tt.frame.origin.y = centre.y - t.frame.height / 2; scene.add(.text(tt))
        }
        // The arrows and cards stay inside the frame by construction; the cards must not meet.
        for i in 0..<n { for j in (i + 1)..<n where frames[i].intersects(frames[j]) { fits = false } }
        return H - pad
    }

    // MARK: layers — bands from the base up, each narrower than the one below

    mutating func pyramid(top: CGFloat) -> CGFloat {
        let n = spec.items.count
        guard n > 0 else { return top }
        let bandGap = 10 * s
        let bandH = min(120 * s, (H - pad - top - CGFloat(n - 1) * bandGap) / CGFloat(n))
        let maxW = (W - 2 * pad) * 0.58, minW = maxW * 0.38
        let sideX = pad + maxW + 36 * s, sideW = W - pad - sideX
        var y = H - pad - CGFloat(n) * bandH - CGFloat(n - 1) * bandGap
        if y < top { fits = false; y = top }
        func width(_ level: Int) -> CGFloat { n == 1 ? maxW : maxW - (maxW - minW) * CGFloat(level) / CGFloat(n - 1) }
        for level in 0..<n {
            let i = level                                            // item 0 is the base
            let yTop = H - pad - CGFloat(level + 1) * bandH - CGFloat(level) * bandGap
            let wBottom = width(level), wTop = level == n - 1 ? wBottom * 0.72 : width(level + 1)
            let cx = pad + maxW / 2
            scene.add(.fill([CGPoint(x: cx - wBottom / 2, y: yTop + bandH), CGPoint(x: cx + wBottom / 2, y: yTop + bandH),
                             CGPoint(x: cx + wTop / 2, y: yTop), CGPoint(x: cx - wTop / 2, y: yTop)], role: .accent(i)))
            if let t = block(spec.items[i].title, VisualTextStyle(size: 23 * s, weight: .semibold, role: .onAccent, align: .center), x: cx - wTop / 2 + 12 * s, y: 0, width: wTop - 24 * s, maxLines: 2) {
                var tt = t; tt.frame.origin.y = yTop + (bandH - t.frame.height) / 2; scene.add(.text(tt))
            }
            let mid = yTop + bandH / 2
            scene.add(.stroke([.move(CGPoint(x: cx + wBottom / 2 + 8 * s, y: mid)), .line(CGPoint(x: sideX - 10 * s, y: mid))], role: .rule, width: 1.5 * s, dashed: true))
            if let d = block(spec.items[i].detail, bodyStyle, x: sideX, y: 0, width: sideW, maxLines: max(1, Int(bandH / (26 * s)))) {
                var dd = d; dd.frame.origin.y = mid - d.frame.height / 2; scene.add(.text(dd))
            }
        }
        return H - pad
    }

    // MARK: ranked list — numbered cards, the first the strongest

    mutating func rankedList(top: CGFloat) -> CGFloat {
        let n = spec.items.count
        let cols = n > 5 ? 2 : 1
        let colGap = 32 * s
        let colW = (W - 2 * pad - CGFloat(cols - 1) * colGap) / CGFloat(cols)
        let perCol = Int((Double(n) / Double(cols)).rounded(.up))
        let badge = 24 * s
        var bottom = top
        var columnHeights: [CGFloat] = []
        for (i, item) in spec.items.enumerated() {
            let c = i / max(1, perCol), r = i % max(1, perCol)
            let x = pad + CGFloat(c) * (colW + colGap)
            var y = top
            // Stack below the cards already placed in this column.
            for j in 0..<r { y += columnHeights[c * perCol + j] + 14 * s }
            let textX = inner + badge * 2 + 14 * s
            let t = block(item.title, headingStyle, x: 0, y: 0, width: colW - textX - inner)
            let v = block(item.value, captionStyle(i), x: 0, y: 0, width: colW - textX - inner, maxLines: 1)
            let d = block(item.detail, bodyStyle, x: 0, y: 0, width: colW - textX - inner, maxLines: 3)
            let h = max(badge * 2, (t?.frame.height ?? 0) + (v.map { $0.frame.height + 4 * s } ?? 0) + (d.map { $0.frame.height + 6 * s } ?? 0)) + 2 * inner
            columnHeights.append(h)
            scene.add(.card(CGRect(x: x, y: y, width: colW, height: h), fill: i == 0 ? .tint(0) : .surface, stroke: nil, radius: radius, hit: nil))
            let centre = CGPoint(x: x + inner + badge, y: y + inner + badge)
            scene.add(.dot(centre, radius: badge, fill: .accent(i == 0 ? 0 : 4), stroke: nil))
            if let num = block("\(i + 1)", VisualTextStyle(size: 22 * s, weight: .bold, role: .onAccent, align: .center), x: centre.x - badge, y: centre.y - 13 * s, width: badge * 2) { scene.add(.text(num)) }
            var ty = y + inner
            if var t { t.frame.origin = CGPoint(x: x + textX, y: ty); scene.add(.text(t)); ty += t.frame.height + 4 * s }
            if var v { v.frame.origin = CGPoint(x: x + textX, y: ty); scene.add(.text(v)); ty += v.frame.height + 6 * s }
            if var d { d.frame.origin = CGPoint(x: x + textX, y: ty); scene.add(.text(d)) }
            bottom = max(bottom, y + h)
        }
        return bottom
    }

    // MARK: hierarchy — the whole on top, its parts across, their sub-parts beneath

    mutating func tree(top: CGFloat) -> CGFloat {
        let g = spec.items.count
        guard g > 0 else { return top }
        let colGap = 24 * s
        let colW = (W - 2 * pad - CGFloat(g - 1) * colGap) / CGFloat(g)
        var y = top
        // The root: the title again, as the trunk, when the title is short enough to read as a box.
        let rootW = min(W - 2 * pad, 420 * s)
        var rootBottom = y
        if let t = block(spec.title, VisualTextStyle(size: 24 * s, weight: .bold, role: .onAccent, align: .center), x: W / 2 - rootW / 2 + inner, y: 0, width: rootW - 2 * inner, maxLines: 2) {
            // The title already sits at the top of the slide; the trunk repeats it only as the anchor of the lines.
            let h = t.frame.height + 2 * 14 * s
            scene.add(.card(CGRect(x: W / 2 - rootW / 2, y: y, width: rootW, height: h), fill: .accent(4), stroke: nil, radius: radius, hit: nil))
            var tt = t; tt.frame.origin.y = y + 14 * s; scene.add(.text(tt))
            rootBottom = y + h
            y = rootBottom + 44 * s
        }
        let busY = rootBottom + 22 * s
        var bottom = y
        for (i, item) in spec.items.enumerated() {
            let x = pad + CGFloat(i) * (colW + colGap)
            let c = cardContent(title: item.title, detail: "", width: colW, titleStyle: VisualTextStyle(size: 22 * s, weight: .semibold, align: .center))
            let frame = CGRect(x: x, y: y, width: colW, height: c.height)
            placeCard(c, in: frame, fill: .tint(i))
            if rootBottom > top {
                scene.add(.stroke([.move(CGPoint(x: W / 2, y: rootBottom)), .line(CGPoint(x: W / 2, y: busY)), .line(CGPoint(x: frame.midX, y: busY)), .line(CGPoint(x: frame.midX, y: frame.minY))], role: .rule, width: 2 * s, dashed: false))
            }
            var cy = frame.maxY + 16 * s
            let spineX = frame.minX + 18 * s
            for child in item.children {
                guard let b = block(child, VisualTextStyle(size: 18 * s), x: spineX + 22 * s, y: cy, width: colW - 40 * s, maxLines: 2) else { continue }
                scene.add(.stroke([.move(CGPoint(x: spineX, y: cy + b.lineHeight / 2)), .line(CGPoint(x: spineX + 14 * s, y: cy + b.lineHeight / 2))], role: .accent(i), width: 2 * s, dashed: false))
                scene.add(.text(b))
                cy += b.frame.height + 8 * s
            }
            if !item.children.isEmpty {
                scene.add(.stroke([.move(CGPoint(x: spineX, y: frame.maxY)), .line(CGPoint(x: spineX, y: cy - 8 * s - (18 * s * 1.18 / 2)))], role: .accent(i), width: 2 * s, dashed: false))
            }
            bottom = max(bottom, cy)
        }
        return bottom
    }

    // MARK: concept clusters — a grid of themed cards, each with its concepts

    mutating func clusters(top: CGFloat) -> CGFloat {
        let rows = VisualLayout.rows(spec.items.count, maxPerRow: 3)
        let per = rows.first?.count ?? 1
        let colW = (W - 2 * pad - CGFloat(per - 1) * gap) / CGFloat(per)
        let header = 46 * s
        var y = top
        for row in rows {
            var blocks: [(i: Int, title: VisualTextBlock?, children: [VisualTextBlock], height: CGFloat)] = []
            for i in row {
                let t = block(spec.items[i].title, VisualTextStyle(size: 21 * s, weight: .semibold, role: .onAccent), x: 0, y: 0, width: colW - 2 * inner, maxLines: 1)
                var kids: [VisualTextBlock] = []
                var h = header + 12 * s
                for child in spec.items[i].children {
                    if let b = block(child, VisualTextStyle(size: 18 * s), x: 0, y: h, width: colW - 2 * inner - 22 * s, maxLines: 2) { kids.append(b); h += b.frame.height + 7 * s }
                }
                blocks.append((i, t, kids, h + inner - 7 * s))
            }
            let h = blocks.map(\.height).max() ?? 0
            var x = pad
            for b in blocks {
                scene.add(.card(CGRect(x: x, y: y, width: colW, height: h), fill: .surface, stroke: nil, radius: radius, hit: nil))
                scene.add(.card(CGRect(x: x, y: y, width: colW, height: header), fill: .accent(b.i), stroke: nil, radius: radius, hit: nil))
                scene.add(.card(CGRect(x: x, y: y + header - radius, width: colW, height: radius), fill: .accent(b.i), stroke: nil, radius: 0, hit: nil))
                if var t = b.title { t.frame.origin = CGPoint(x: x + inner, y: y + (header - t.frame.height) / 2); scene.add(.text(t)) }
                for var k in b.children {
                    scene.add(.dot(CGPoint(x: x + inner + 5 * s, y: y + k.frame.minY + k.lineHeight / 2), radius: 4 * s, fill: .accent(b.i), stroke: nil))
                    k.frame.origin.x = x + inner + 22 * s; k.frame.origin.y += y; scene.add(.text(k))
                }
                x += colW + gap
            }
            y += h + gap
        }
        return y - gap
    }

    // MARK: swimlanes — a band per actor, that actor's steps across it

    mutating func swimlane(top: CGFloat) -> CGFloat {
        let g = spec.items.count
        guard g > 0 else { return top }
        let labelW = 190 * s, arrowGap = 40 * s
        let laneGap = 12 * s
        let maxSteps = max(1, spec.items.map(\.children.count).max() ?? 1)
        let cardW = (W - pad - (pad + labelW) - CGFloat(maxSteps - 1) * arrowGap) / CGFloat(maxSteps)
        var y = top
        for (i, lane) in spec.items.enumerated() {
            var cards: [(title: VisualTextBlock?, detail: VisualTextBlock?, height: CGFloat)] = []
            for step in lane.children { cards.append(cardContent(title: step, detail: "", width: cardW, titleStyle: VisualTextStyle(size: 19 * s, weight: .semibold), detailLines: nil)) }
            let cardH = max(cards.map(\.height).max() ?? 0, 60 * s)
            let laneH = cardH + 2 * 16 * s
            scene.add(.card(CGRect(x: pad, y: y, width: W - 2 * pad, height: laneH), fill: .tint(i), stroke: nil, radius: 12 * s, hit: nil))
            if let t = block(lane.title, VisualTextStyle(size: 20 * s, weight: .bold, role: .accent(i)), x: pad + 16 * s, y: 0, width: labelW - 28 * s, maxLines: 2) {
                var tt = t; tt.frame.origin.y = y + (laneH - t.frame.height) / 2; scene.add(.text(tt))
            }
            var x = pad + labelW
            for (n, c) in cards.enumerated() {
                let frame = CGRect(x: x, y: y + 16 * s, width: cardW, height: cardH)
                placeCard(c, in: frame, fill: .background)
                if n > 0 { arrow(from: CGPoint(x: x - arrowGap + 6 * s, y: frame.midY), to: CGPoint(x: x - 6 * s, y: frame.midY)) }
                x += cardW + arrowGap
            }
            y += laneH + laneGap
        }
        return y - laneGap
    }

    // MARK: decision tree — a question card with a diamond mark, its outcomes to the right, the next question below

    mutating func decisionTree(top: CGFloat) -> CGFloat {
        let qW = 440 * s, qGap = 64 * s, mark = 26 * s
        let outW = W - pad - (pad + qW + qGap)
        var y = top
        for (i, q) in spec.items.enumerated() {
            // A rounded card, not a rhombus: three lines of question fit a card, where a flat diamond cut their ends off.
            let question = block(q.title, VisualTextStyle(size: 21 * s, weight: .semibold), x: pad + inner + mark + 14 * s, y: 0, width: qW - 2 * inner - mark - 14 * s, maxLines: 3)
            let qH = max(88 * s, (question?.frame.height ?? 0) + 2 * inner)
            // Outcomes first, to know the height.
            var outcomes: [(label: String, block: VisualTextBlock?, lead: CGFloat)] = []
            var oy = y
            for child in q.children {
                let parts = child.split(separator: ":", maxSplits: 1).map { $0.trimmingCharacters(in: .whitespaces) }
                let label = parts.count == 2 ? parts[0] : ""
                let text = parts.count == 2 ? parts[1] : child
                let lead = (label.isEmpty ? 12 : 30) * s      // room for the choice word above the outcome
                let b = block(text, VisualTextStyle(size: 19 * s), x: pad + qW + qGap + inner, y: oy + lead, width: outW - 2 * inner, maxLines: 2)
                outcomes.append((label, b, lead))
                oy += (b?.frame.height ?? 0) + lead + 12 * s + 10 * s
            }
            let blockH = max(qH, oy - y - 10 * s)
            let cx = pad + qW / 2, cy = y + blockH / 2
            scene.add(.card(CGRect(x: pad, y: cy - qH / 2, width: qW, height: qH), fill: .tint(i), stroke: .accent(i), radius: radius, hit: nil))
            let mx = pad + inner + mark / 2
            scene.add(.fill([CGPoint(x: mx, y: cy - mark / 2), CGPoint(x: mx + mark / 2, y: cy), CGPoint(x: mx, y: cy + mark / 2), CGPoint(x: mx - mark / 2, y: cy)], role: .accent(i)))
            if var qb = question { qb.frame.origin.y = cy - qb.frame.height / 2; scene.add(.text(qb)) }
            for o in outcomes {
                guard let b = o.block else { continue }
                let frame = CGRect(x: pad + qW + qGap, y: b.frame.minY - o.lead, width: outW, height: b.frame.height + o.lead + 12 * s)
                scene.add(.card(frame, fill: .surface, stroke: nil, radius: 12 * s, hit: nil))
                scene.add(.text(b))
                let from = CGPoint(x: pad + qW, y: cy), to = CGPoint(x: frame.minX, y: frame.midY)
                let midX = from.x + qGap / 2
                scene.add(.stroke([.move(from), .line(CGPoint(x: midX, y: from.y)), .line(CGPoint(x: midX, y: to.y)), .line(CGPoint(x: to.x - 10 * s, y: to.y))], role: .rule, width: 2 * s, dashed: false))
                head(at: to, direction: CGPoint(x: 1, y: 0), role: .rule)
                // The choice ("Yes", "No") sits inside the outcome card's left edge, where it cannot cross the connector.
                if !o.label.isEmpty, let l = block(o.label, VisualTextStyle(size: 14 * s, weight: .bold, role: .accent(i)), x: frame.minX + 12 * s, y: frame.minY + 6 * s, width: outW - 24 * s, maxLines: 1) { scene.add(.text(l)) }
            }
            if i < spec.items.count - 1 {
                let next = y + blockH + 36 * s
                scene.add(.stroke([.move(CGPoint(x: cx, y: cy + qH / 2)), .line(CGPoint(x: cx, y: next + 44 * s - 10 * s))], role: .rule, width: 2 * s, dashed: true))
                head(at: CGPoint(x: cx, y: next + 44 * s - 10 * s), direction: CGPoint(x: 0, y: 1), role: .rule)
            }
            y += blockH + 36 * s
        }
        return y - 36 * s
    }

    // MARK: two columns — pros against cons, or before against after

    mutating func twoColumns(top: CGFloat, marks: (VisualIcon, VisualIcon), arrow: Bool) -> CGFloat {
        let groups = Array(spec.items.prefix(2))
        guard !groups.isEmpty else { return top }
        let mid = arrow ? 90 * s : 36 * s
        let colW = (W - 2 * pad - mid) / 2
        let header = 50 * s
        let accents = arrow ? (5, 0) : (2, 3)
        var bottom = top
        for (c, group) in groups.enumerated() {
            let x = pad + CGFloat(c) * (colW + mid)
            let accent = c == 0 ? accents.0 : accents.1
            var kids: [VisualTextBlock] = []
            var h = header + 14 * s
            for child in group.children {
                if let b = block(child, VisualTextStyle(size: 19 * s), x: x + inner + 34 * s, y: top + h, width: colW - 2 * inner - 34 * s) { kids.append(b); h += b.frame.height + 12 * s }
            }
            h += inner - 12 * s
            scene.add(.card(CGRect(x: x, y: top, width: colW, height: h), fill: .tint(accent), stroke: nil, radius: radius, hit: nil))
            if let t = block(group.title, VisualTextStyle(size: 24 * s, weight: .bold, role: .accent(accent)), x: x + inner, y: 0, width: colW - 2 * inner, maxLines: 1) {
                var tt = t; tt.frame.origin.y = top + (header - t.frame.height) / 2 + 6 * s; scene.add(.text(tt))
            }
            for k in kids {
                scene.add(.symbol(c == 0 ? marks.0 : marks.1, CGRect(x: x + inner, y: k.frame.minY + (k.lineHeight - 22 * s) / 2, width: 22 * s, height: 22 * s), role: .accent(accent)))
                scene.add(.text(k))
            }
            bottom = max(bottom, top + h)
        }
        if arrow, groups.count == 2 {
            let y = top + (bottom - top) / 2
            self.arrow(from: CGPoint(x: pad + colW + 16 * s, y: y), to: CGPoint(x: pad + colW + mid - 16 * s, y: y), role: .accent(0))
        }
        return bottom
    }

    // MARK: hero band — the title large on the item's painted backdrop, or on an accent band without one

    /// Draws the band and the title on it; returns the band's bottom edge.
    mutating func heroBand(top: CGFloat, height: CGFloat, titleSize: CGFloat) -> CGFloat {
        let r = CGRect(x: pad, y: top, width: W - 2 * pad, height: height)
        let painted = spec.hero != nil
        if painted {
            scene.add(.image(r, radius: radius))
            scene.add(.card(r, fill: .scrim, stroke: nil, radius: radius, hit: nil))
        } else {
            scene.add(.card(r, fill: .accent(4), stroke: nil, radius: radius, hit: nil))
        }
        if let t = block(spec.title, VisualTextStyle(size: titleSize, weight: .bold, role: painted ? .onImage : .onAccent, align: .center), x: pad + 60 * s, y: 0, width: W - 2 * pad - 120 * s, maxLines: 2) {
            var tt = t; tt.frame.origin.y = top + (height - t.frame.height) / 2; scene.add(.text(tt))
        }
        return top + height
    }

    // MARK: value proposition — the offer as a hero band, its benefits beneath

    mutating func valueProposition(top: CGFloat) -> CGFloat {
        // The title is the offer: drawn again as the hero, large, instead of the small title line; on a painted
        // backdrop when the item has one, on an accent band when it has not.
        var y = top
        y = heroBand(top: y, height: (spec.hero == nil ? 170 : 240) * s, titleSize: 40 * s) + 32 * s
        let g = max(1, spec.items.count)
        let colW = (W - 2 * pad - CGFloat(g - 1) * gap) / CGFloat(g)
        var blocks: [(i: Int, title: VisualTextBlock?, kids: [VisualTextBlock], height: CGFloat)] = []
        for (i, item) in spec.items.enumerated() {
            let mark = 36 * s
            let t = block(item.title, VisualTextStyle(size: 23 * s, weight: .semibold), x: 0, y: 0, width: colW - 2 * inner, maxLines: 2)
            var h = inner + mark + 12 * s + (t?.frame.height ?? 0) + 10 * s
            var kids: [VisualTextBlock] = []
            for child in item.children {
                if let b = block(child, bodyStyle, x: 0, y: h, width: colW - 2 * inner, maxLines: 2) { kids.append(b); h += b.frame.height + 6 * s }
            }
            blocks.append((i, t, kids, h + inner - 6 * s))
        }
        let h = blocks.map(\.height).max() ?? 0
        var x = pad
        for b in blocks {
            scene.add(.card(CGRect(x: x, y: y, width: colW, height: h), fill: .surface, stroke: nil, radius: radius, hit: nil))
            scene.add(.symbol(.benefit, CGRect(x: x + inner, y: y + inner, width: 36 * s, height: 36 * s), role: .accent(b.i)))
            if var t = b.title { t.frame.origin = CGPoint(x: x + inner, y: y + inner + 36 * s + 12 * s); scene.add(.text(t)) }
            for var k in b.kids { k.frame.origin.x = x + inner; k.frame.origin.y += y; scene.add(.text(k)) }
            x += colW + gap
        }
        return y + h
    }

    // MARK: 2×2 — two axes, four quadrants, the items placed by their scores

    mutating func quadrant(top: CGFloat) -> CGFloat {
        let left = pad + 40 * s, right = W - pad - 40 * s
        let plotTop = top + 10 * s, plotBottom = H - pad - 36 * s
        let cx = (left + right) / 2, cy = (plotTop + plotBottom) / 2
        for (i, q) in [CGRect(x: left, y: plotTop, width: cx - left, height: cy - plotTop), CGRect(x: cx, y: plotTop, width: right - cx, height: cy - plotTop),
                       CGRect(x: left, y: cy, width: cx - left, height: plotBottom - cy), CGRect(x: cx, y: cy, width: right - cx, height: plotBottom - cy)].enumerated() {
            scene.add(.card(q.insetBy(dx: 3 * s, dy: 3 * s), fill: .tint(i), stroke: nil, radius: 12 * s, hit: nil))
        }
        scene.add(.stroke([.move(CGPoint(x: left, y: cy)), .line(CGPoint(x: right, y: cy))], role: .rule, width: 2 * s, dashed: false))
        scene.add(.stroke([.move(CGPoint(x: cx, y: plotTop)), .line(CGPoint(x: cx, y: plotBottom))], role: .rule, width: 2 * s, dashed: false))
        let axisStyle = VisualTextStyle(size: 18 * s, weight: .semibold, role: .quiet)
        if spec.columns.count >= 2 {
            place(spec.columns[0] + "  →", axisStyle, x: cx + 12 * s, y: plotBottom + 8 * s, width: right - cx)
            place("↑  " + spec.columns[1], axisStyle, x: left + 12 * s, y: plotTop + 6 * s, width: cx - left)
        }
        for (i, item) in spec.items.enumerated() {
            let parts = item.value.split(separator: ",").map { Double($0.trimmingCharacters(in: .whitespaces)) ?? 3 }
            let xv = min(5, max(1, parts.first ?? 3)), yv = min(5, max(1, parts.count > 1 ? parts[1] : 3))
            let px = left + (right - left) * CGFloat((xv - 0.5) / 5), py = plotBottom - (plotBottom - plotTop) * CGFloat((yv - 0.5) / 5)
            scene.add(.dot(CGPoint(x: px, y: py), radius: 9 * s, fill: .accent(i), stroke: .background))
            var ls = VisualTextStyle(size: 17 * s, weight: .semibold)
            let w = min(260 * s, VisualText.width(of: item.title, style: ls) + 4)
            let rightSide = px + 16 * s + w <= right
            ls.align = rightSide ? .leading : .trailing
            place(item.title, ls, x: rightSide ? px + 16 * s : px - 16 * s - w, y: py - 11 * s, width: w, maxLines: 1)
        }
        return H - pad
    }
}
