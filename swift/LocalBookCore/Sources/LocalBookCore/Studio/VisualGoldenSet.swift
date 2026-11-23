import Foundation

/// The 15-prompt golden set the audit proposed for visuals: three asks per layout, at the three depths, phrased so
/// that any notebook answers them. Both apps draw the same 15 from the same notebook and Patrick judges the pairs
/// side by side (absorption map, "Visuals" row). `scripts/visual_golden_export.py` carries the same list for the
/// old app; `VisualGoldenSetTests` holds this one still.
public enum VisualGoldenSet {
    public struct Prompt: Sendable, Identifiable {
        public let id: String
        public let layout: VisualSpec.Layout
        public let detail: VisualSpec.Detail
        public let topic: String
        /// The old app's template that answers the same ask (`visual_router` ids).
        public let oldTemplate: String
    }

    public static let prompts: [Prompt] = [
        Prompt(id: "01-mindmap-overview", layout: .mindmap, detail: .overview, topic: "", oldTemplate: "mindmap"),
        Prompt(id: "02-mindmap-standard", layout: .mindmap, detail: .standard, topic: "the main ideas and how they connect", oldTemplate: "mindmap"),
        Prompt(id: "03-mindmap-deep", layout: .mindmap, detail: .deep, topic: "every important concept, grouped by theme", oldTemplate: "overview_map"),
        Prompt(id: "04-flow-overview", layout: .flowchart, detail: .overview, topic: "how the main process works, step by step", oldTemplate: "process_flow"),
        Prompt(id: "05-flow-standard", layout: .flowchart, detail: .standard, topic: "the decisions and what follows from each", oldTemplate: "decision_tree"),
        Prompt(id: "06-flow-deep", layout: .flowchart, detail: .deep, topic: "the full sequence of steps with the branches and loops", oldTemplate: "horizontal_steps"),
        Prompt(id: "07-compare-overview", layout: .comparison, detail: .overview, topic: "the two most important options or sides", oldTemplate: "side_by_side_analysis"),
        Prompt(id: "08-compare-standard", layout: .comparison, detail: .standard, topic: "strengths and weaknesses of the approaches described", oldTemplate: "pros_cons"),
        Prompt(id: "09-compare-deep", layout: .comparison, detail: .deep, topic: "all the alternatives on the criteria that matter", oldTemplate: "quadrant"),
        Prompt(id: "10-timeline-overview", layout: .timeline, detail: .overview, topic: "the key events in order", oldTemplate: "timeline"),
        Prompt(id: "11-timeline-standard", layout: .timeline, detail: .standard, topic: "how things developed over time, with dates", oldTemplate: "timeline"),
        Prompt(id: "12-timeline-deep", layout: .timeline, detail: .deep, topic: "the complete history, phases and turning points", oldTemplate: "timeline"),
        Prompt(id: "13-info-overview", layout: .infographic, detail: .overview, topic: "the numbers that matter most", oldTemplate: "key_stats"),
        Prompt(id: "14-info-standard", layout: .infographic, detail: .standard, topic: "key facts, figures and names at a glance", oldTemplate: "key_stats"),
        Prompt(id: "15-info-deep", layout: .infographic, detail: .deep, topic: "a one-page summary of what matters and why", oldTemplate: "exec_summary_context"),
    ]

    /// One drawing's outcome: its picture's file name or why it failed, how long the model took, and what the lint found.
    public struct Made: Sendable {
        public var prompt: Prompt
        public var file: String?
        public var problem: String?
        public var seconds: Double?
        public var lint: [String]
        /// The form the engine chose, when the run let it choose instead of taking the prompt's.
        public var chosen: VisualSpec.Layout?
        /// The critic's verdict line, when it looked.
        public var critique: String?
        /// Extra fills the picture took (a lint defect or the critic's verdict each buy one).
        public var retries: Int
        public init(prompt: Prompt, file: String? = nil, problem: String? = nil, seconds: Double? = nil, lint: [String] = [], chosen: VisualSpec.Layout? = nil, critique: String? = nil, retries: Int = 0) {
            self.prompt = prompt; self.file = file; self.problem = problem; self.seconds = seconds; self.lint = lint; self.chosen = chosen; self.critique = critique; self.retries = retries
        }
        var time: String { seconds.map { String(format: "%.0f s", $0) } ?? "" }
        /// The prompt's form, or "Mind map → Cycle" when the engine chose.
        var form: String { chosen.map { "\(prompt.layout.label) → \($0.label)" } ?? prompt.layout.label }
        var checks: String {
            var parts = [lint.isEmpty ? (file == nil ? "" : "clean") : lint.joined(separator: "; ")]
            if retries > 0 { parts.append("\(retries) extra fill\(retries == 1 ? "" : "s")") }
            if let critique { parts.append("critic \(critique)") }
            return parts.filter { !$0.isEmpty }.joined(separator: " · ")
        }
    }

    /// `index.md` beside the pictures: the notebook, the device, then one row per prompt with its picture's name,
    /// the seconds the model took and what the lint found.
    public static func index(notebook: String, device: String, date: Date, made: [Made], theme: String? = nil) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd HH:mm"
        var lines = ["# Visual golden set: \(notebook)", "", "Drawn by the Swift Mac app on \(device), \(f.string(from: date))\(theme.map { " in the \($0) theme" } ?? "").", "",
                     "Open the old app's `old-app-visuals.html` for the same notebook beside this folder and judge each pair: does the Swift drawing say what the old one said, as clearly?", "",
                     "| # | Form | Detail | Ask | Swift picture | Time | Lint |", "|---|---|---|---|---|---|---|"]
        for (i, m) in made.enumerated() {
            let ask = m.prompt.topic.isEmpty ? "(the whole notebook)" : m.prompt.topic
            let got = m.file.map { "[\($0)](\($0))" } ?? "failed: \(m.problem ?? "unknown")"
            lines.append("| \(i + 1) | \(m.form) | \(m.prompt.detail.label) | \(ask) | \(got) | \(m.time) | \(m.checks) |")
        }
        lines += ["", "Judgement (fill in): ___ of 15 pairs where the Swift drawing is as good or better."]
        return lines.joined(separator: "\n")
    }

    /// The same index as a page with the pictures in it, for a browser: Markdown links to files beside a `.md`
    /// open in nothing on a Mac, which is what kept the first C6 sitting from the pictures.
    public static func indexHTML(notebook: String, device: String, date: Date, made: [Made], theme: String? = nil) -> String {
        func esc(_ s: String) -> String {
            s.replacingOccurrences(of: "&", with: "&amp;").replacingOccurrences(of: "<", with: "&lt;").replacingOccurrences(of: ">", with: "&gt;").replacingOccurrences(of: "\"", with: "&quot;")
        }
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd HH:mm"
        var out = ["<!DOCTYPE html><html><head><meta charset='utf-8'><title>Visual golden set: \(esc(notebook))</title>",
                   "<style>body{font-family:-apple-system,Helvetica,sans-serif;max-width:1100px;margin:30px auto;padding:0 20px}",
                   "section{border:1px solid #ddd;border-radius:10px;padding:16px;margin:20px 0}h2{margin:0 0 4px}.meta{color:#666;margin:0 0 12px}",
                   "img{max-width:100%;height:auto;background:#fff}p.err{color:#b00}</style></head><body>",
                   "<h1>Swift app: visual golden set, \(esc(notebook))</h1>",
                   "<p class='meta'>Drawn on \(esc(device)), \(f.string(from: date))\(theme.map { " in the \(esc($0)) theme" } ?? ""). Open the old app's old-app-visuals.html beside this page and judge each pair: does the Swift drawing say what the old one said, as clearly?</p>"]
        for (i, m) in made.enumerated() {
            let ask = m.prompt.topic.isEmpty ? "(the whole notebook)" : m.prompt.topic
            let facts = [m.time, m.checks].filter { !$0.isEmpty }.joined(separator: " · ")
            out.append("<section><h2>\(i + 1). \(esc(m.form)) · \(esc(m.prompt.detail.label))</h2><p class='meta'>\(esc(ask))\(facts.isEmpty ? "" : " · " + esc(facts))</p>")
            if let file = m.file { out.append("<img src='\(esc(file))' alt='\(esc(m.prompt.id))'>") }
            else { out.append("<p class='err'>failed: \(esc(m.problem ?? "unknown"))</p>") }
            out.append("</section>")
        }
        out.append("<p>Judgement (fill in index.md): ___ of 15 pairs where the Swift drawing is as good or better.</p></body></html>")
        return out.joined(separator: "\n")
    }
}
