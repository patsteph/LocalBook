import Foundation

/// Chooses the form of a visual when the person leaves it to the engine. The words of the ask decide first: a rule
/// that names a form ("the steps", "pros and cons", "over time") fires with no model call. Only when no rule fires
/// is the answer model asked, with the shortest possible question (one id back), so the pick costs a second at
/// most and never changes what the Fill step is given.
public enum VisualPick {
    /// The rules, most specific first. Each is a pattern over the lower-cased ask and the form it names.
    static let rules: [(pattern: String, layout: VisualSpec.Layout)] = [
        (#"pros? (and|&|/) ?cons?|for and against|strengths? and weakness|advantages? and disadvantages?|trade-?offs?|should (we|i)\b|worth it|\bweigh"#, .prosCons),
        (#"before (and|&|vs\.?) ?after|then and now|how it (was|used to be).*now|what changed|old (vs\.?|versus|and) new"#, .beforeAfter),
        (#"value proposition|the offer\b|\bpitch\b|selling point|why (choose|buy|use)|benefits? of"#, .valueProposition),
        (#"who does what|each (team|person|role|department)|roles? and responsibilit|hand-?offs?|across teams|swim ?lanes?"#, .swimlane),
        (#"\bcycle|lifecycle|life cycle|recurring|\brepeats?\b|feedback loop|iterat|\bseasons?\b|round and round"#, .cycle),
        (#"decision|\bif .* then\b|branch|whether to|which (path|route|option) to|choose between|\bdecide"#, .decisionTree),
        (#"timeline|history|over time|chronolog|\bdates?\b|evolution|evolved|developed|turning points?|phases|milestones|\byears?\b|decades?"#, .timeline),
        (#"\brank|top \d+|top (three|five|ten)|priorit|best to worst|in order of importance|leaderboard"#, .rankedList),
        (#"effort (and|vs\.?|against) impact|impact (and|vs\.?|against) effort|urgen(t|cy) (and|vs\.?) import|2 ?[x×] ?2|two axes|quadrant|matrix of|plot(ted)? (on|against)"#, .quadrant),
        (#"hierarch|org(anisation|anization)? chart|\btaxonomy\b|parts? of|components? of|made up of|breakdown of|structure of|consists? of|anatomy"#, .tree),
        (#"\blayers?\b|\blevels?\b|\btiers?\b|foundation|pyramid|builds? on|\bstack\b|maturity|from (the )?(bottom|base) (up|to)"#, .pyramid),
        (#"\bgroup|cluster|\bthemes?\b|categor|concepts|vocabulary|glossary|terms? and definitions"#, .clusters),
        (#"compar|versus|\bvs\.?\b|differ|alternatives|options|side by side|which is better|criteria"#, .comparison),
        (#"infographic|at a glance|one[- ]page|fact ?sheet|facts and figures|what matters and why|cheat ?sheet"#, .infographic),
        (#"\bnumbers?\b|statistic|\bstats?\b|figures|metrics|by the numbers|how (many|much)|percent|\bkpis?\b"#, .statCallouts),
        (#"\bsteps?\b|process|procedure|how (to|do(es)?|is|are) .* (work|made|done|happen)|workflow|sequence|\bstages?\b|pipeline|\bflow\b|recipe|instructions|from start to finish"#, .flowchart),
        (#"mind ?map|main ideas|overview|big picture|key points|key ideas|everything|the whole|summar|main (topics|themes|concepts)"#, .mindmap),
    ]

    /// The form the words of the ask name, or nil when they name none (and the model should be asked).
    public static func rule(for topic: String) -> VisualSpec.Layout? {
        let t = topic.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return nil }
        for r in rules where t.range(of: r.pattern, options: .regularExpression) != nil { return r.layout }
        return nil
    }

    /// The two axes an ask names for a two-axis plot ("effort against impact", "urgency vs importance", "plot risk
    /// and reward"), horizontal first, capitalised; nil when the ask names none.
    public static func axes(in topic: String) -> [String]? {
        let t = topic.lowercased()
        // "against" and "versus" name axes almost always; "and" and "by" only when nothing stronger is there.
        let side = #"([a-z][a-z-]+(?: [a-z][a-z-]+)?)"#
        var words: [String] = []
        for joiner in [#"(?:against|vs\.?|versus)"#, #"(?:by|and)"#] {
            guard let m = t.range(of: #"\b"# + side + #"\s+"# + joiner + #"\s+"# + side + #"\b"#, options: .regularExpression) else { continue }
            words = String(t[m]).replacingOccurrences(of: #"\s+"# + joiner + #"\s+"#, with: "|", options: .regularExpression).split(separator: "|").map { $0.trimmingCharacters(in: .whitespaces) }
            break
        }
        guard words.count == 2 else { return nil }
        // "the main techniques on two axes, effort against impact": the first word may carry an article or a verb.
        let stop: Set<String> = ["the", "a", "an", "on", "of", "two", "axes", "plot", "place", "sort", "map", "by", "with", "their", "its"]
        let clean = words.map { w in w.split(separator: " ").map(String.init).filter { !stop.contains($0) }.joined(separator: " ") }.filter { !$0.isEmpty }
        guard clean.count == 2, clean.allSatisfy({ $0.count >= 3 && $0.count <= 24 }) else { return nil }
        return clean.map(\.capitalized)
    }

    /// What the model is told when asked to choose.
    public static let instructions = "You choose the best form for a diagram. Reply with exactly one id from the list and nothing else."

    /// The question put to the model: the ask, a glimpse of the passages, and the catalogue as ids with when to use each.
    public static func prompt(about: String, passages: [String]) -> String {
        let glimpse = passages.prefix(6).map { String($0.prefix(240)).replacingOccurrences(of: "\n", with: " ") }.joined(separator: "\n- ")
        let menu = VisualSpec.Layout.allCases.map { "\($0.rawValue): \($0.useWhen)" }.joined(separator: "\n")
        return "Which form best shows \(about)?\n\nFrom the notes:\n- \(glimpse)\n\nForms:\n\(menu)\n\nReply with one id only."
    }

    /// The form named in the model's reply (an id or a label, anywhere in the text), or nil.
    public static func parse(_ raw: String) -> VisualSpec.Layout? {
        let t = raw.lowercased()
        // Longest ids first, so "decision_tree" wins over a bare "tree" inside it.
        let byLength = VisualSpec.Layout.allCases.sorted { $0.rawValue.count > $1.rawValue.count }
        var best: (layout: VisualSpec.Layout, at: String.Index)?
        for l in byLength {
            for key in [l.rawValue, l.rawValue.replacingOccurrences(of: "_", with: " "), l.label.lowercased()] {
                if let r = t.range(of: "\\b\(NSRegularExpression.escapedPattern(for: key))\\b", options: .regularExpression),
                   best == nil || r.lowerBound < best!.at {
                    best = (l, r.lowerBound)
                }
            }
        }
        return best?.layout
    }

    /// How many tokens the reply needs: an id and a little slack.
    public static let replyTokens = 16
}
