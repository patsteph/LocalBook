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

    /// `index.md` beside the pictures: the notebook, the device, then one row per prompt with its picture's name.
    public static func index(notebook: String, device: String, date: Date, made: [(prompt: Prompt, file: String?, problem: String?)]) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd HH:mm"
        var lines = ["# Visual golden set: \(notebook)", "", "Drawn by the Swift Mac app on \(device), \(f.string(from: date)).", "",
                     "Open the old app's `old-app-visuals.html` for the same notebook beside this folder and judge each pair: does the Swift drawing say what the old one said, as clearly?", "",
                     "| # | Layout | Detail | Ask | Swift picture |", "|---|---|---|---|---|"]
        for (i, m) in made.enumerated() {
            let ask = m.prompt.topic.isEmpty ? "(the whole notebook)" : m.prompt.topic
            let got = m.file.map { "[\($0)](\($0))" } ?? "failed: \(m.problem ?? "unknown")"
            lines.append("| \(i + 1) | \(m.prompt.layout.label) | \(m.prompt.detail.label) | \(ask) | \(got) |")
        }
        lines += ["", "Judgement (fill in): ___ of 15 pairs where the Swift drawing is as good or better."]
        return lines.joined(separator: "\n")
    }
}
