import SwiftUI

/// A visual: the structure the answer model wrote (not a picture). `VisualLayout` places it as a `VisualScene`, which
/// `VisualSceneView` draws natively (crisp, light and dark), `VisualSlide.image` saves as a PNG and `VisualSVG` writes as
/// a vector file. The form (`Layout`) is an idiom from the catalogue; idioms sharing a `Shape` fill the same fields, so
/// the same content can be drawn as any of them without asking the model again.
public struct VisualSpec: Codable, Hashable, Sendable {
    /// What the model is asked to fill: the fields an idiom draws from.
    public enum Shape: Sendable {
        /// `items` in order, each with a title, a detail and (timelines) a `value` for when.
        case sequence
        /// `items` with children: a title per group and short phrases under it.
        case groups
        /// `columns` and `rows`.
        case matrix
        /// `columns` = the two axes; `items` with `value` "x,y" from 1 to 5.
        case quadrant
        /// `stats` (value + label) and `items` (point + detail).
        case figures
    }
    public enum Family: String, CaseIterable, Sendable {
        case process, structure, comparison, data
        public var label: String { switch self { case .process: "Process"; case .structure: "Structure"; case .comparison: "Comparison"; case .data: "Facts and figures" } }
    }

    /// The catalogue of forms. The raw values are stored in saved items, so they never change.
    public enum Layout: String, Codable, CaseIterable, Identifiable, Sendable {
        // Process
        case flowchart, timeline, cycle, swimlane, decisionTree = "decision_tree"
        // Structure
        case mindmap, tree, clusters, pyramid, valueProposition = "value_proposition"
        // Comparison
        case comparison, prosCons = "pros_cons", beforeAfter = "before_after", quadrant, rankedList = "ranked_list"
        // Facts and figures
        case infographic, statCallouts = "stat_callouts"

        public var id: String { rawValue }
        public var label: String {
            switch self {
            case .mindmap: "Mind map"; case .flowchart: "Flowchart"; case .comparison: "Comparison"; case .timeline: "Timeline"; case .infographic: "Infographic"
            case .cycle: "Cycle"; case .swimlane: "Swimlanes"; case .decisionTree: "Decision tree"; case .tree: "Hierarchy"; case .clusters: "Concept clusters"
            case .pyramid: "Layers"; case .valueProposition: "Value proposition"; case .prosCons: "Pros and cons"; case .beforeAfter: "Before and after"
            case .quadrant: "2×2 matrix"; case .rankedList: "Ranked list"; case .statCallouts: "Key numbers"
            }
        }
        public var symbol: String {
            switch self {
            case .mindmap: "point.3.connected.trianglepath.dotted"; case .flowchart: "arrow.triangle.branch"; case .comparison: "tablecells"
            case .timeline: "clock.arrow.circlepath"; case .infographic: "chart.bar.doc.horizontal"; case .cycle: "arrow.triangle.2.circlepath"
            case .swimlane: "rectangle.split.1x2"; case .decisionTree: "arrow.triangle.branch"; case .tree: "list.bullet.indent"; case .clusters: "square.grid.2x2"
            case .pyramid: "triangle"; case .valueProposition: "star.square"; case .prosCons: "plus.forwardslash.minus"; case .beforeAfter: "arrow.left.arrow.right"
            case .quadrant: "squareshape.split.2x2"; case .rankedList: "list.number"; case .statCallouts: "number"
            }
        }
        public var family: Family {
            switch self {
            case .flowchart, .timeline, .cycle, .swimlane, .decisionTree: .process
            case .mindmap, .tree, .clusters, .pyramid, .valueProposition: .structure
            case .comparison, .prosCons, .beforeAfter, .quadrant, .rankedList: .comparison
            case .infographic, .statCallouts: .data
            }
        }
        public var shape: Shape {
            switch self {
            case .flowchart, .timeline, .cycle, .pyramid, .rankedList: .sequence
            case .mindmap, .tree, .clusters, .swimlane, .decisionTree, .prosCons, .beforeAfter, .valueProposition: .groups
            case .comparison: .matrix
            case .quadrant: .quadrant
            case .infographic, .statCallouts: .figures
            }
        }
        /// When the engine should choose this form, for the pick prompt and the rules.
        public var useWhen: String {
            switch self {
            case .flowchart: "steps of a process in order"
            case .timeline: "events with dates, a history, how something developed"
            case .cycle: "a loop that repeats: stages that lead back to the start"
            case .swimlane: "a process where several people or teams each own some steps"
            case .decisionTree: "decisions with branches: if this, then that"
            case .mindmap: "the main ideas of a subject and the points under each"
            case .tree: "a hierarchy: parts within parts, an org chart, a taxonomy"
            case .clusters: "concepts grouped by theme"
            case .pyramid: "layers or levels that build on one another, from base to top"
            case .valueProposition: "one offer or idea and the few benefits that support it"
            case .comparison: "two or more options compared on several criteria"
            case .prosCons: "the arguments for and against one thing"
            case .beforeAfter: "a change: how it was and how it is now"
            case .quadrant: "items placed on two axes, such as effort against impact"
            case .rankedList: "an ordered ranking with a reason for each place"
            case .infographic: "key figures with a few points to remember"
            case .statCallouts: "the numbers that matter most, nothing else"
            }
        }
        /// `useWhen` as a sentence for the picker's footnote.
        public var hint: String { useWhen.prefix(1).uppercased() + String(useWhen.dropFirst()) + "." }
        /// The other forms the same filled content can be drawn as, without asking the model again.
        public var alternatives: [Layout] { Layout.allCases.filter { $0 != self && $0.shape == shape } }
    }
    public struct Item: Codable, Hashable, Sendable {
        public var title: String
        public var detail: String = ""
        public var value: String = ""
        public var children: [String] = []
        public init(title: String, detail: String = "", value: String = "", children: [String] = []) {
            self.title = title; self.detail = detail; self.value = value; self.children = children
        }
        public init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
            detail = try c.decodeIfPresent(String.self, forKey: .detail) ?? ""
            value = try c.decodeIfPresent(String.self, forKey: .value) ?? ""
            children = try c.decodeIfPresent([String].self, forKey: .children) ?? []
        }
    }
    public struct Row: Codable, Hashable, Sendable { public var label: String; public var cells: [String] }

    public var layout: Layout
    public var title: String
    public var items: [Item] = []          // branches, steps, events, groups or key points
    public var stats: [Item] = []          // figures (value + title as the label)
    public var columns: [String] = []      // comparison: the options; quadrant: the two axes
    public var rows: [Row] = []            // comparison
    /// The theme's id (`VisualTheme.all`); nil, and ids from a later version, draw as Clean.
    public var theme: String? = nil
    /// The file of the painted backdrop (`VisualHero`), beside the item; nil, or a file not on this device, draws a band.
    public var hero: String? = nil
    /// Colours read from the painted backdrop (`#rrggbb`), which the theme's first accents take so the picture and the
    /// cards read as one piece; nil keeps the theme's own accents.
    public var palette: [String]? = nil

    /// The theme this picture is dressed in, with its accents matched to the backdrop when one was painted.
    public var dressing: VisualTheme { VisualTheme.named(theme).matched(to: palette) }

    public init(layout: Layout, title: String, items: [Item] = [], stats: [Item] = [], columns: [String] = [], rows: [Row] = [], theme: String? = nil) {
        self.layout = layout; self.title = title; self.items = items; self.stats = stats; self.columns = columns; self.rows = rows; self.theme = theme
    }

    /// A saved item needs only its form and title: a list it never had, or a field from a later version, reads as empty.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        layout = try c.decode(Layout.self, forKey: .layout)
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        items = try c.decodeIfPresent([Item].self, forKey: .items) ?? []
        stats = try c.decodeIfPresent([Item].self, forKey: .stats) ?? []
        columns = try c.decodeIfPresent([String].self, forKey: .columns) ?? []
        rows = try c.decodeIfPresent([Row].self, forKey: .rows) ?? []
        theme = try c.decodeIfPresent(String.self, forKey: .theme)
        hero = try c.decodeIfPresent(String.self, forKey: .hero)
        palette = try c.decodeIfPresent([String].self, forKey: .palette)
    }

    /// The same content as another form of the same shape.
    public func drawn(as other: Layout) -> VisualSpec { var s = self; s.layout = other; return s }
    /// The same picture in another theme.
    public func dressed(in theme: VisualTheme) -> VisualSpec { var s = self; s.theme = theme.id; return s }

    /// The Studio item's title for a spec: the form, then the picture's own title (or the notebook's).
    public static func itemTitle(_ spec: VisualSpec, notebook: String) -> String {
        "\(spec.layout.label): \(spec.title.isEmpty ? notebook : spec.title)"
    }

    /// `title` with its form prefix swapped for `layout`'s, when the item was redrawn as another form.
    public static func retitle(_ title: String, from old: Layout, to new: Layout) -> String {
        title.hasPrefix(old.label + ": ") ? new.label + ": " + String(title.dropFirst(old.label.count + 2)) : title
    }

    /// Reads the model's JSON object leniently. nil when there is nothing usable to draw.
    public static func parse(_ raw: String, layout: Layout) -> VisualSpec? {
        guard let o = JSONObjects.first(in: raw) else { return nil }
        func text(_ v: Any?) -> String { (v as? String ?? (v as? NSNumber).map { "\($0)" } ?? "").trimmingCharacters(in: .whitespacesAndNewlines) }
        func objects(_ key: String) -> [[String: Any]] { o[key] as? [[String: Any]] ?? [] }
        func strings(_ v: Any?) -> [String] { (v as? [Any] ?? []).map(text).filter { !$0.isEmpty } }
        // Models rename keys freely ("name" for "title", "phases" for "items"): read the usual aliases, then any list of objects.
        /// A level word or a number: what a model writes when it scores an item on an axis.
        func scored(_ v: Any?) -> Bool {
            if v is NSNumber { return true }
            let t = text(v).lowercased()
            return Double(t) != nil || ["very high", "high", "medium", "mid", "moderate", "low", "very low"].contains(t)
        }
        // The usual title keys first; failing those, the entry's one short string that is not a score ("technique":
        // "RAG" beside "effort": "low") names it.
        func titled(_ e: [String: Any]) -> String {
            let t = text(e["title"] ?? e["name"] ?? e["label"] ?? e["step"] ?? e["stage"] ?? e["question"])
            if !t.isEmpty { return t }
            let skip: Set<String> = ["detail", "description", "text", "summary", "value", "when", "date", "x", "y", "position", "quadrant", "cell",
                                     "rationale", "reason", "reasoning", "why", "note", "notes", "explanation", "justification", "comment", "yes", "no", "then", "else"]
            let shorts = e.filter { !skip.contains($0.key.lowercased()) }.compactMap { k, v -> (String, String)? in
                guard let str = v as? String, !str.isEmpty, str.count <= 60, !str.contains("\n"), !scored(str) else { return nil }
                return (k, str)
            }
            // Several short strings: the name is the shortest of them (a name is a few words; a reason is a sentence).
            return shorts.min { a, b in a.1.count != b.1.count ? a.1.count < b.1.count : a.0 < b.0 }?.1 ?? ""
        }
        func detailed(_ e: [String: Any]) -> String { text(e["detail"] ?? e["description"] ?? e["text"] ?? e["summary"]) }
        func anyList(_ keys: [String]) -> [[String: Any]] {
            if let l = keys.map(objects).first(where: { !$0.isEmpty }) { return l }
            return o.values.compactMap { $0 as? [[String: Any]] }.first { l in l.contains { !titled($0).isEmpty } } ?? []
        }
        /// "x": 4, "x": "4", "x": "high" or "x": "low" all place an item.
        func position(_ v: Any?) -> String {
            let t = text(v).lowercased()
            if let n = Double(t) { return "\(n)" }
            if t.hasPrefix("very h") { return "5" }; if t.hasPrefix("h") { return "4.5" }
            if t.hasPrefix("very l") { return "1" }; if t.hasPrefix("l") { return "1.5" }
            if t.hasPrefix("m") { return "3" }
            return "3"
        }
        var spec = VisualSpec(layout: layout, title: text(o["title"]))
        switch layout.shape {
        case .sequence:
            let list = anyList(["items", "steps", "events", "stages", "phases", "groups"])
            spec.items = list.map { Item(title: titled($0), detail: detailed($0), value: text($0["when"] ?? $0["value"] ?? $0["date"])) }
        case .groups:
            // Models often write "items" whatever the prompt calls them: take the first list whose entries have children.
            let lists = ["groups", "branches", "questions", "items", "steps", "decisions", "nodes"].map(objects).filter { !$0.isEmpty }
            let list = lists.first { l in l.contains { $0["children"] != nil } } ?? lists.first ?? anyList([])
            /// The children of a group: a list of strings, a list of objects (each named by its title or its text), or
            /// for a decision the "yes" and "no" keys, however the answer is spelt.
            func children(_ e: [String: Any]) -> [String] {
                let kids = e["children"] ?? e["points"] ?? e["outcomes"] ?? e["options"] ?? e["items"] ?? e["answers"] ?? e["branches"]
                var out = strings(kids)
                if out.isEmpty, let objs = kids as? [[String: Any]] {
                    out = objs.compactMap { c -> String? in
                        let answer = text(c["answer"] ?? c["choice"] ?? c["if"] ?? c["label"] ?? c["condition"])
                        let advice = text(c["then"] ?? c["advice"] ?? c["outcome"] ?? c["action"] ?? c["result"] ?? c["detail"] ?? c["text"] ?? c["title"] ?? c["name"])
                        if answer.isEmpty { return advice.isEmpty ? nil : advice }
                        return advice.isEmpty ? answer : "\(answer): \(advice)"
                    }
                }
                if out.isEmpty {
                    let lower = Dictionary(e.map { ($0.key.lowercased(), $0.value) }, uniquingKeysWith: { a, _ in a })
                    func answer(_ keys: [String]) -> String? {
                        for k in keys {
                            guard let v = lower[k] else { continue }
                            let t = (v as? [String: Any]).map { d in text(d["advice"] ?? d["then"] ?? d["action"] ?? d["text"] ?? d["title"]) } ?? text(v)
                            if !t.isEmpty { return t }
                        }
                        return nil
                    }
                    if let y = answer(["yes", "if yes", "if_yes", "ifyes", "true", "then"]), let n = answer(["no", "if no", "if_no", "ifno", "false", "else", "otherwise"]) {
                        out = ["Yes: \(y)", "No: \(n)"]
                    }
                }
                return out
            }
            spec.items = list.map { Item(title: titled($0), detail: detailed($0), children: children($0)) }
        case .quadrant:
            var axes = strings(o["axes"] ?? o["columns"])
            if axes.count < 2, let a = o["axes"] as? [String: Any] { axes = [text(a["x"] ?? a["horizontal"]), text(a["y"] ?? a["vertical"])].filter { !$0.isEmpty } }
            if axes.count < 2 { axes = [text(o["x_axis"] ?? o["xAxis"] ?? o["x"]), text(o["y_axis"] ?? o["yAxis"] ?? o["y"])].filter { !$0.isEmpty } }
            /// The two scores of an entry: "x"/"y", a "position" object, keys named after the axes ("effort": "high",
            /// "impact": 4), or failing those the entry's first two scored values in key order.
            func scores(_ e: [String: Any]) -> (Any?, Any?)? {
                if let pos = e["position"] as? [String: Any], pos["x"] != nil || pos["y"] != nil { return (pos["x"], pos["y"]) }
                if e["x"] != nil || e["y"] != nil { return (e["x"], e["y"]) }
                let lower = Dictionary(e.map { ($0.key.lowercased(), $0.value) }, uniquingKeysWith: { a, _ in a })
                if axes.count >= 2 {
                    func byAxis(_ axis: String) -> Any? {
                        let words = axis.lowercased().split(separator: " ").map(String.init)
                        return lower.first { k, v in scored(v) && words.contains { w in w.count > 2 && (k == w || k.hasPrefix(w) || w.hasPrefix(k)) } }?.value
                    }
                    if let a = byAxis(axes[0]), let b = byAxis(axes[1]) { return (a, b) }
                }
                let rest = lower.filter { scored($0.value) && !["title", "name", "label"].contains($0.key) }.sorted { $0.key < $1.key }
                guard rest.count >= 2 else { return nil }
                let first: Any? = rest[0].value, second: Any? = rest[1].value
                return (first, second)
            }
            /// A cell named in words ("High impact, low effort", "Quick wins: low effort / high impact") as two scores,
            /// read against the axes when they are known, else in the order written. nil when no level word is there.
            func cell(_ words: String) -> (Any?, Any?)? {
                let t = words.lowercased()
                let levels: [(String, Double)] = [("very high", 5), ("very low", 1), ("high", 4.5), ("low", 1.5), ("medium", 3), ("mid", 3), ("moderate", 3)]
                var found: [(at: Int, level: Double)] = []
                var scan = t
                for (word, v) in levels {
                    while let r = scan.range(of: "\\b\(word)\\b", options: .regularExpression) {
                        let at = scan.distance(from: scan.startIndex, to: r.lowerBound)
                        found.append((at, v))
                        scan.replaceSubrange(r, with: String(repeating: " ", count: word.count))
                    }
                }
                found.sort { $0.at < $1.at }
                guard let firstLevel = found.first else { return nil }
                if axes.count >= 2 {
                    // Each level word belongs to the axis word nearest after or before it.
                    func pos(_ axis: String) -> Int? {
                        for w in axis.lowercased().split(separator: " ").map(String.init) where w.count > 2 {
                            if let r = t.range(of: w.prefix(5)) { return t.distance(from: t.startIndex, to: r.lowerBound) }
                        }
                        return nil
                    }
                    if let ax = pos(axes[0]), let ay = pos(axes[1]), found.count >= 2 {
                        let x = found.min { abs($0.at - ax) < abs($1.at - ax) }!.level
                        let y = found.min { abs($0.at - ay) < abs($1.at - ay) }!.level
                        return (x, y)
                    }
                    if let ax = pos(axes[0]), found.count == 1 { return abs(firstLevel.at - ax) < 12 ? (firstLevel.level, 3.0) : (3.0, firstLevel.level) }
                }
                return (firstLevel.level, found.count >= 2 ? found[1].level : 3.0)
            }
            let list = anyList(["items", "points", "techniques"])
            var placed = list.compactMap { e -> ([String: Any], (Any?, Any?))? in scores(e).map { (e, $0) } }
            // Items with their cell in words rather than scores ("quadrant": "High impact, low effort").
            if placed.isEmpty {
                placed = list.compactMap { e in
                    let words = text(e["quadrant"] ?? e["cell"] ?? e["position"] ?? e["placement"] ?? e["category"])
                    return cell(words).map { (e, $0) }
                }
            }
            // The model grouped the items by cell instead: groups whose titles name the cell and list the items, or a
            // dictionary of cell name to names. Each name is placed where its cell says.
            if placed.isEmpty {
                var cells: [(String, [String])] = []
                let groups = anyList(["quadrants", "cells", "groups"])
                for g in groups {
                    let names = strings(g["children"] ?? g["items"] ?? g["techniques"] ?? g["points"] ?? g["members"] ?? g["examples"])
                    let label = [titled(g), detailed(g), text(g["quadrant"] ?? g["cell"] ?? g["position"])].filter { !$0.isEmpty }.joined(separator: " ")
                    if !names.isEmpty { cells.append((label, names)) }
                }
                if cells.isEmpty {
                    for (k, v) in o {
                        if let dict = v as? [String: Any] {
                            for (name, members) in dict where !strings(members).isEmpty { cells.append((name, strings(members))) }
                            for (name, note) in dict where cell(text(note)) != nil && !(note is [Any]) && k != "axes" { cells.append((text(note), [name])) }
                        }
                    }
                }
                for (label, names) in cells {
                    guard let at = cell(label) else { continue }
                    for n in names { placed.append((["title": n], at)) }
                }
            }
            // Scored on keys named after the axes but the axes themselves unnamed: take the key names as the axes.
            if axes.count < 2, let first = placed.first?.0 {
                let keys = first.filter { scored($0.value) && !["x", "y"].contains($0.key.lowercased()) }.map(\.key).sorted()
                if keys.count >= 2 { axes = [keys[0].capitalized, keys[1].capitalized] }
            }
            // Items placed but the axes unnamed: draw them anyway with plain axis names rather than nothing.
            if axes.count < 2, !placed.isEmpty { axes = ["Horizontal", "Vertical"] }
            spec.columns = axes
            spec.items = placed.map { e, pos in Item(title: titled(e), detail: detailed(e), value: "\(position(pos.0)),\(position(pos.1))") }
            spec.items = spread(spec.items)
        case .figures:
            spec.stats = anyList(["stats", "numbers", "figures"]).map { Item(title: text($0["label"] ?? $0["title"] ?? $0["name"]), value: text($0["value"] ?? $0["number"] ?? $0["figure"])) }
            spec.items = objects("points").map { Item(title: titled($0), detail: detailed($0)) }
        case .matrix:
            spec.columns = strings(o["columns"])
            spec.rows = objects("rows").map { Row(label: text($0["label"]), cells: ($0["cells"] as? [Any] ?? []).map(text)) }
        }
        spec.items.removeAll { $0.title.isEmpty }; spec.stats.removeAll { $0.value.isEmpty }
        spec.rows.removeAll { $0.label.isEmpty }
        // A decision tree needs outcomes a reader can act on. A reply whose "outcomes" are ticks and crosses
        // ("RAG: ✓") is a comparison in disguise and is not drawn as decisions: `parseAnyShape` draws it as the table it is.
        if layout == .decisionTree {
            let outcomes = spec.items.flatMap(\.children)
            let hollow = outcomes.filter { c in
                let after = c.split(separator: ":", maxSplits: 1).last.map { String($0) } ?? c
                return after.filter(\.isLetter).count < 3
            }
            if outcomes.isEmpty || hollow.count * 2 >= outcomes.count { return nil }
        }
        let usable: Bool = switch layout.shape {
        case .matrix: !spec.rows.isEmpty && !spec.columns.isEmpty
        case .quadrant: !spec.items.isEmpty && spec.columns.count >= 2
        default: !spec.items.isEmpty || !spec.stats.isEmpty
        }
        return usable ? spec : nil
    }

    /// `parse` for the asked form, or, when the reply does not fit that form's shape, the first other shape it does
    /// fit, drawn as that shape's plain form. nil only when nothing in the reply can be drawn: the engine never comes
    /// back blank because the model answered a different question than the form asked.
    public static func parseAnyShape(_ raw: String, preferring layout: Layout) -> VisualSpec? {
        if let s = parse(raw, layout: layout) { return s }
        // A decision tree refused a reply of its own shape: the reply was a comparison in disguise (criteria with a
        // mark per option), and is drawn as the table it is. A mind map of bare questions is not offered: it reads worse
        // than either, so when the reply is neither decisions nor a table the engine asks again.
        if layout == .decisionTree {
            if let table = comparison(fromMarks: raw) { return table }
            for other in [Layout.infographic, .comparison, .quadrant] { if let s = parse(raw, layout: other) { return s } }
            return nil
        }
        // Another form refused a reply of its own shape: that shape's plain form draws it before any other shape is tried.
        let plain: Layout = switch layout.shape {
        case .groups: .mindmap; case .sequence: .flowchart; case .figures: .infographic; case .matrix: .comparison; case .quadrant: .quadrant
        }
        if plain != layout, let s = parse(raw, layout: plain) { return s }
        let grouped = raw.contains("\"children\"")
        for other in (grouped ? [Layout.mindmap, .flowchart] : [.flowchart, .mindmap]) + [.infographic, .comparison, .quadrant] where other.shape != layout.shape {
            if let s = parse(raw, layout: other) { return s }
        }
        return nil
    }

    /// Items that share one spot (every item of a cell, when placed by the cell's name) spread over a small grid
    /// around it, so no two dots or labels sit on top of each other.
    static func spread(_ items: [Item]) -> [Item] {
        var byValue: [String: [Int]] = [:]
        for (i, it) in items.enumerated() { byValue[it.value, default: []].append(i) }
        var out = items
        for (value, at) in byValue where at.count > 1 {
            let parts = value.split(separator: ",").compactMap { Double($0.trimmingCharacters(in: .whitespaces)) }
            guard parts.count == 2 else { continue }
            let cols = at.count <= 2 ? at.count : 2
            let rows = Int((Double(at.count) / Double(cols)).rounded(.up))
            for (k, i) in at.enumerated() {
                let c = k % cols, r = k / cols
                let dx = cols == 1 ? 0 : (Double(c) - Double(cols - 1) / 2) * 1.4
                let dy = rows == 1 ? 0 : (Double(rows - 1) / 2 - Double(r)) * (1.6 / Double(max(1, rows - 1)))
                let x = min(5, max(1, parts[0] + dx)), y = min(5, max(1, parts[1] + dy))
                out[i].value = "\(x),\(y)"
            }
        }
        return out
    }

    /// Groups whose children are "option: mark" pairs ("RAG: ✓", "Fine-tuning: ✗", "Long context: partial") read as a
    /// comparison table: the options are the columns, each group is a row, the marks are the cells. nil unless at
    /// least two options appear in at least two groups.
    public static func comparison(fromMarks raw: String) -> VisualSpec? {
        guard let groups = parse(raw, layout: .mindmap), groups.items.count >= 2 else { return nil }
        var columns: [String] = []
        var marks: [(label: String, cells: [String: String])] = []
        for g in groups.items {
            var cells: [String: String] = [:]
            for c in g.children {
                let parts = c.split(separator: ":", maxSplits: 1).map { $0.trimmingCharacters(in: .whitespaces) }
                guard parts.count == 2, !parts[0].isEmpty, parts[0].count <= 30 else { continue }
                if !columns.contains(parts[0]) { columns.append(parts[0]) }
                cells[parts[0]] = parts[1].isEmpty ? "✓" : parts[1]
            }
            if !cells.isEmpty { marks.append((g.title.trimmingCharacters(in: CharacterSet(charactersIn: ": ")), cells)) }
        }
        guard columns.count >= 2, marks.count >= 2 else { return nil }
        let rows = marks.map { m in Row(label: m.label, cells: columns.map { m.cells[$0] ?? "" }) }
        return VisualSpec(layout: .comparison, title: groups.title, columns: columns, rows: rows)
    }

    /// How much each visual carries. Standard is the default; Deep is for when a summary isn't enough.
    public enum Detail: String, CaseIterable, Identifiable, Sendable {
        case overview, standard, deep
        public var id: String { rawValue }
        public var label: String { switch self { case .overview: "Overview"; case .standard: "Standard"; case .deep: "Deep" } }
        /// Reply budget for the model.
        public var tokens: Int { switch self { case .overview: 800; case .standard: 1_300; case .deep: 2_400 } }
        /// How many notebook passages to read.
        public var passages: Int { switch self { case .overview: 8; case .standard: 14; case .deep: 24 } }
        fileprivate func pick(_ a: String, _ b: String, _ c: String) -> String { switch self { case .overview: a; case .standard: b; case .deep: c } }
    }

    /// The JSON shape each form is asked for: one schema per shape, with the form's own words for what the items are.
    public static func request(_ layout: Layout, about: String, detail: Detail = .standard) -> String {
        let d = detail
        let sentence = d.pick("a few words", "one short sentence", "one or two sentences")
        let n = d.pick("4 or 5", "5 to 8", "8 to 12")
        let lead = "Reply with ONLY a JSON object:"
        switch layout {
        case .flowchart:
            return "Make a flowchart of the main process or sequence in \(about). \(lead) {\"title\": at most 6 words, \"items\": [\(n) steps in order {\"title\": at most 6 words, \"detail\": \(sentence)}]}."
        case .timeline:
            return "Make a timeline for \(about). \(lead) {\"title\": at most 6 words, \"items\": [\(n) events in order {\"when\": the date or stage, \"title\": at most 6 words, \"detail\": \(sentence)}]}."
        case .cycle:
            let k = d.pick("4", "5 or 6", "6 to 8")
            return "Show the repeating cycle in \(about). \(lead) {\"title\": at most 6 words, \"items\": [\(k) stages in order, the last leading back to the first {\"title\": at most 5 words, \"detail\": \(sentence)}]}."
        case .pyramid:
            let k = d.pick("3 or 4", "4 or 5", "5 to 7")
            return "Show the layers or levels in \(about), from the foundation up. \(lead) {\"title\": at most 6 words, \"items\": [\(k) layers from the base to the top {\"title\": at most 5 words, \"detail\": \(sentence)}]}."
        case .rankedList:
            return "Rank the most important things in \(about). \(lead) {\"title\": at most 6 words, \"items\": [\(n) entries from first to last {\"title\": at most 6 words, \"value\": a score, figure or one-word rating if the passages give one, \"detail\": why it ranks here, \(sentence)}]}."
        case .mindmap:
            let g = d.pick("4 or 5", "5 or 6", "7 or 8"), k = d.pick("1 or 2", "2 to 4", "4 to 6"), w = d.pick("6", "8", "12")
            return "Make a mind map of \(about). \(lead) {\"title\": the central topic in at most 6 words, \"groups\": [\(g) branches {\"title\": 1 to 4 words, \"children\": [\(k) short phrases of at most \(w) words]}]}."
        case .tree:
            let g = d.pick("3 or 4", "4 or 5", "5 to 7"), k = d.pick("2 or 3", "2 to 4", "3 to 5")
            return "Show the hierarchy in \(about): the parts and what each contains. \(lead) {\"title\": the whole, at most 5 words, \"groups\": [\(g) parts {\"title\": at most 4 words, \"children\": [\(k) sub-parts of at most 5 words each]}]}."
        case .clusters:
            let g = d.pick("3 or 4", "4 to 6", "6 to 8"), k = d.pick("2 or 3", "3 or 4", "4 to 6")
            return "Group the concepts in \(about) by theme. \(lead) {\"title\": at most 6 words, \"groups\": [\(g) themes {\"title\": at most 4 words, \"children\": [\(k) concepts of at most 6 words each]}]}."
        case .swimlane:
            let g = d.pick("2 or 3", "3 or 4", "4 or 5"), k = d.pick("2 or 3", "3 or 4", "4 to 6")
            return "Show who does what in the process in \(about). \(lead) {\"title\": at most 6 words, \"groups\": [\(g) people, teams or systems {\"title\": the actor, at most 3 words, \"children\": [\(k) steps that actor does, in order, at most 7 words each]}]}."
        case .decisionTree:
            let g = d.pick("2 or 3", "3 or 4", "4 to 6")
            return "Make a decision guide for \(about): the yes-or-no questions a reader asks about their own situation, in the order they should ask them, and what to do for each answer. \(lead) {\"title\": at most 6 words, \"questions\": [\(g) questions in order {\"question\": a yes-or-no question ending in a question mark, at most 10 words, \"yes\": what to do when the answer is yes, at most 8 words, \"no\": what to do when the answer is no, or \"Ask the next question\", at most 8 words}]}. For example, for choosing a car: {\"title\": \"Which car to buy\", \"questions\": [{\"question\": \"Do you drive more than 50 miles a day?\", \"yes\": \"Choose the hybrid\", \"no\": \"Ask the next question\"}]}. Every answer is advice in words: no ticks, crosses, scores or tables."
        case .prosCons:
            let k = d.pick("2 or 3", "3 or 4", "4 to 6")
            return "Weigh \(about). \(lead) {\"title\": the thing weighed, at most 6 words, \"groups\": [exactly 2 groups: {\"title\": \"Pros\", \"children\": [\(k) arguments for, at most 10 words each]}, {\"title\": \"Cons\", \"children\": [\(k) arguments against, at most 10 words each]}]}."
        case .beforeAfter:
            let k = d.pick("2 or 3", "3 or 4", "4 to 6")
            return "Show the change in \(about). \(lead) {\"title\": at most 6 words, \"groups\": [exactly 2 groups: {\"title\": \"Before\", \"children\": [\(k) points about how it was, at most 10 words each]}, {\"title\": \"After\", \"children\": [\(k) matching points about how it is now, at most 10 words each]}]}."
        case .valueProposition:
            let k = d.pick("1 or 2", "2 or 3", "3 or 4")
            return "State the central offer or idea in \(about) and what supports it. \(lead) {\"title\": the offer in at most 8 words, \"groups\": [3 benefits {\"title\": at most 4 words, \"children\": [\(k) supporting facts of at most 10 words each]}]}."
        case .comparison:
            let r = d.pick("3 or 4", "4 to 6", "7 to 10")
            return "Make a comparison table for \(about). \(lead) {\"title\": at most 6 words, \"columns\": [2 or 3 names of the things compared], \"rows\": [\(r) items {\"label\": the aspect compared, \"cells\": [one short phrase per column, in column order]}]}."
        case .quadrant:
            let k = d.pick("4 to 6", "6 to 9", "9 to 14")
            return "Sort the items in \(about) into the four cells of two axes. \(lead) {\"title\": at most 6 words, \"axes\": [the horizontal axis, the vertical axis, one or two words each], \"cells\": [exactly 4 cells {\"cell\": \"low <horizontal axis>, low <vertical axis>\", \"items\": [names of at most 4 words]}, then the same for \"high …, low …\", \"low …, high …\" and \"high …, high …\"]}, with \(k) names in all, each in one cell only. For example, for chores on effort and impact: {\"title\": \"Chores worth doing\", \"axes\": [\"Effort\", \"Impact\"], \"cells\": [{\"cell\": \"low effort, low impact\", \"items\": [\"Dusting\"]}, {\"cell\": \"high effort, low impact\", \"items\": [\"Repainting the shed\"]}, {\"cell\": \"low effort, high impact\", \"items\": [\"Paying the bills\", \"Locking up\"]}, {\"cell\": \"high effort, high impact\", \"items\": [\"Fixing the roof\"]}]}."
        case .infographic:
            let st = d.pick("3", "3 or 4", "4 to 6"), p = d.pick("2 or 3", "3 to 5", "5 to 8")
            return "Make an infographic for \(about). \(lead) {\"title\": a punchy headline of at most 8 words, \"stats\": [\(st) items {\"value\": a figure, number or short fact taken from the passages, \"label\": at most 5 words}], \"points\": [\(p) items {\"title\": at most 5 words, \"detail\": \(sentence)}]}."
        case .statCallouts:
            let st = d.pick("3 or 4", "4 to 6", "6 to 8")
            return "Pull out the numbers that matter most in \(about). \(lead) {\"title\": a punchy headline of at most 8 words, \"stats\": [\(st) items {\"value\": the figure as written in the passages, \"label\": what it measures, at most 6 words}], \"points\": []}."
        }
    }
}

/// The form of a visual: "Choose for me" first, then the catalogue by family.
public struct VisualFormPicker: View {
    @Binding var layout: VisualSpec.Layout?
    public init(layout: Binding<VisualSpec.Layout?>) { _layout = layout }

    public var body: some View {
        Picker("Form", selection: $layout) {
            Label("Choose for me", systemImage: "wand.and.sparkles").tag(VisualSpec.Layout?.none)
            ForEach(VisualSpec.Family.allCases, id: \.self) { family in
                Section(family.label) {
                    ForEach(VisualSpec.Layout.allCases.filter { $0.family == family }) { Label($0.label, systemImage: $0.symbol).tag(Optional($0)) }
                }
            }
        }
    }
}

/// The theme of a visual, with each one's line.
public struct VisualThemePicker: View {
    @Binding var theme: String
    public init(theme: Binding<String>) { _theme = theme }

    public var body: some View {
        Picker("Theme", selection: $theme) {
            ForEach(VisualTheme.all) { t in Text(t.name).tag(t.id) }
        }
    }
}

/// Draws a scene with SwiftUI's `Canvas`, scaled to whatever frame it is given, in the theme's colours for the current
/// appearance. Cards that carry a `hit` get an invisible button over them (mind-map branches open in place).
public struct VisualSceneView: View {
    public let scene: VisualScene
    public var theme: VisualTheme = .clean
    /// The painted backdrop the scene's `.image` node shows, when the item has one on this device.
    public var hero: CGImage? = nil
    public var hit: ((Int) -> Void)? = nil
    @Environment(\.colorScheme) private var colorScheme

    public init(scene: VisualScene, theme: VisualTheme = .clean, hero: CGImage? = nil, hit: ((Int) -> Void)? = nil) {
        self.scene = scene; self.theme = theme; self.hero = hero; self.hit = hit
    }

    private func color(_ role: VisualRole) -> Color {
        let c = theme.color(role, dark: colorScheme == .dark)
        return Color(red: c.r, green: c.g, blue: c.b, opacity: c.a)
    }

    private static func font(_ style: VisualTextStyle) -> Font {
        let w: Font.Weight = switch style.weight { case .regular: .regular; case .semibold: .semibold; case .bold: .bold; case .heavy: .heavy }
        let design: Font.Design = style.rounded ? .rounded : (style.design == .serif ? .serif : (style.design == .rounded ? .rounded : .default))
        return .system(size: style.size, weight: w, design: design)
    }

    public var body: some View {
        GeometryReader { geo in
            let k = min(geo.size.width / scene.size.width, geo.size.height / scene.size.height)
            Canvas(rendersAsynchronously: false) { ctx, _ in
                ctx.scaleBy(x: k, y: k)
                ctx.fill(Path(CGRect(origin: .zero, size: scene.size)), with: .color(color(.background)))
                for node in scene.nodes {
                    switch node {
                    case .card(let r, let fill, let stroke, let radius, _):
                        let p = Path(roundedRect: r, cornerRadius: radius)
                        var isTint = false
                        if case .tint = fill { isTint = true }
                        if theme.shadows, !isTint {
                            ctx.drawLayer { layer in
                                layer.addFilter(.shadow(color: .black.opacity(0.35), radius: 14, y: 6))
                                layer.fill(p, with: .color(color(fill)))
                            }
                        } else {
                            ctx.fill(p, with: .color(color(fill)))
                        }
                        if let stroke { ctx.stroke(p, with: .color(color(stroke)), lineWidth: 2 * theme.strokeScale) }
                        else if theme.outlinedCards { ctx.stroke(p, with: .color(color(isTint ? .ink : .rule)), lineWidth: 1.6 * theme.strokeScale) }
                    case .text(let t):
                        let font = Self.font(t.style)
                        for (i, line) in t.lines.enumerated() {
                            let y = t.frame.minY + CGFloat(i) * t.lineHeight
                            let text = Text(line).font(font).foregroundStyle(color(t.style.role))
                            switch t.style.align {
                            case .leading: ctx.draw(text, at: CGPoint(x: t.frame.minX, y: y), anchor: .topLeading)
                            case .center: ctx.draw(text, at: CGPoint(x: t.frame.midX, y: y), anchor: .top)
                            case .trailing: ctx.draw(text, at: CGPoint(x: t.frame.maxX, y: y), anchor: .topTrailing)
                            }
                        }
                    case .stroke(let segments, let role, let width, let dashed):
                        var p = Path()
                        for seg in segments {
                            switch seg {
                            case .move(let pt): p.move(to: pt)
                            case .line(let pt): p.addLine(to: pt)
                            case .curve(let to, let c1, let c2): p.addCurve(to: to, control1: c1, control2: c2)
                            }
                        }
                        ctx.stroke(p, with: .color(color(role)), style: StrokeStyle(lineWidth: width * theme.strokeScale, lineCap: .round, lineJoin: .round, dash: dashed ? [6, 6] : []))
                    case .fill(let points, let role):
                        var p = Path()
                        if let first = points.first { p.move(to: first); for pt in points.dropFirst() { p.addLine(to: pt) }; p.closeSubpath() }
                        ctx.fill(p, with: .color(color(role)))
                    case .dot(let centre, let radius, let fill, let stroke):
                        let p = Path(ellipseIn: CGRect(x: centre.x - radius, y: centre.y - radius, width: radius * 2, height: radius * 2))
                        ctx.fill(p, with: .color(color(fill)))
                        if let stroke { ctx.stroke(p, with: .color(color(stroke)), lineWidth: 2) }
                    case .symbol(let icon, let r, let role):
                        var image = ctx.resolve(Image(systemName: theme.symbol(for: icon)))
                        image.shading = .color(color(role))
                        ctx.draw(image, in: r)
                    case .image(let r, let radius):
                        let p = Path(roundedRect: r, cornerRadius: radius)
                        if let hero {
                            // Fill the box, cropping the long side, so the picture never letterboxes.
                            let k = max(r.width / CGFloat(hero.width), r.height / CGFloat(hero.height))
                            let w = CGFloat(hero.width) * k, h = CGFloat(hero.height) * k
                            let fit = CGRect(x: r.midX - w / 2, y: r.midY - h / 2, width: w, height: h)
                            ctx.drawLayer { layer in
                                layer.clip(to: p)
                                layer.draw(Image(decorative: hero, scale: 1), in: fit)
                            }
                        } else {
                            ctx.fill(p, with: .color(color(.accent(4))))
                        }
                    }
                }
            }
            .overlay(alignment: .topLeading) {
                if let hit {
                    ForEach(Array(scene.nodes.enumerated()), id: \.offset) { _, node in
                        if case .card(let r, _, _, _, let tag) = node, let tag {
                            Color.clear.contentShape(Rectangle())
                                .frame(width: r.width * k, height: r.height * k)
                                .offset(x: r.minX * k, y: r.minY * k)
                                .onTapGesture { hit(tag) }
                                .accessibilityLabel("Branch \(tag + 1)")
                                .accessibilityHint("Shows all of this branch, or folds it")
                        }
                    }
                }
            }
        }
        .accessibilityLabel(scene.title)
    }
}

/// Every visual is one 16:9 slide: shown at 800×450 and saved at 2560×1440 (2K). The layout fits itself to the
/// frame (shrinking the type in steps when it must), so the shape never changes. On the phone it is scaled to the
/// screen and can be pinched to zoom. `fit` is kept for callers from the old slide and is always 1 now.
public struct VisualSlide: View {
    public static let size = CGSize(width: 800, height: 450)
    public static let exportWidth: CGFloat = 2560
    public let spec: VisualSpec
    public var fit: CGFloat = 1
    public var expanded: Binding<Set<Int>> = .constant([])

    public init(spec: VisualSpec, fit: CGFloat = 1, expanded: Binding<Set<Int>> = .constant([])) {
        self.spec = spec; self.fit = fit; self.expanded = expanded
    }

    public var body: some View {
        VisualSceneView(scene: VisualLayout.scene(for: spec, expanded: expanded.wrappedValue), theme: spec.dressing, hero: VisualHero.image(named: spec.hero)) { i in
            withAnimation(.snappy) {
                if expanded.wrappedValue.contains(i) { expanded.wrappedValue.remove(i) } else { expanded.wrappedValue.insert(i) }
            }
        }
        .frame(width: Self.size.width, height: Self.size.height)
    }

    /// The layout fits the slide itself; 1, kept so the apps' views need no change.
    @MainActor public static func fit(for spec: VisualSpec, expanded: Set<Int> = []) -> CGFloat { 1 }

    /// The picture as a bitmap in light appearance: 2K wide for sharing, or `width` for the critic's look.
    @MainActor public static func image(for spec: VisualSpec, fit: CGFloat = 1, width: CGFloat = VisualSlide.exportWidth) -> PlatformImage? {
        let r = ImageRenderer(content: VisualSlide(spec: spec).environment(\.colorScheme, .light))
        r.scale = width / size.width
        return r.platformImage
    }

    /// The vector file of the same picture, in light appearance.
    public static func svg(for spec: VisualSpec, expanded: Set<Int> = []) -> String {
        VisualSVG.render(VisualLayout.scene(for: spec, expanded: expanded), theme: spec.dressing, hero: VisualHero.data(named: spec.hero))
    }
}
