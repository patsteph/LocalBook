import Foundation

/// The old app's writing panel: a task (improve, summarize, expand, rewrite, proofread, simplify, continue) in a
/// format (professional, academic, casual, technical, blog, email, bullet points, executive summary), over the
/// user's text, with passages from the notebook as reference when asked. The prompt only; the model runs in Engines.
public enum WritingAssistant {
    public enum Task: String, CaseIterable, Identifiable, Sendable {
        case improve, summarize, expand, rewrite, proofread, simplify, `continue`, outline
        public var id: String { rawValue }
        public var label: String {
            switch self {
            case .improve: "Improve"; case .summarize: "Summarize"; case .expand: "Expand"; case .rewrite: "Rewrite"
            case .proofread: "Proofread"; case .simplify: "Simplify"; case .continue: "Continue"; case .outline: "Outline"
            }
        }
        public var blurb: String {
            switch self {
            case .improve: "Enhance clarity and flow"; case .summarize: "Condense to key points"; case .expand: "Add detail and elaboration"
            case .rewrite: "A complete rewrite in the chosen style"; case .proofread: "Fix grammar and spelling only"; case .simplify: "Make it easier to understand"
            case .continue: "Carry the text on where it stops"; case .outline: "An outline of the points to make"
            }
        }
        var direction: String {
            switch self {
            case .improve: "Improve the text: clearer, better flow, the same meaning and length."
            case .summarize: "Summarize the text to its key points."
            case .expand: "Expand the text with detail and elaboration, keeping its line of thought."
            case .rewrite: "Rewrite the text completely in the style below, keeping every fact."
            case .proofread: "Proofread the text: fix grammar, spelling and punctuation only; change nothing else and keep the author's wording."
            case .simplify: "Simplify the text so a newcomer understands it: shorter sentences, plain words, the same facts."
            case .continue: "Continue the text from where it stops, in the same voice, for a few paragraphs."
            case .outline: "Write an outline of the points the text should make, as a nested list with short headings."
            }
        }
    }

    public enum Format: String, CaseIterable, Identifiable, Sendable {
        case professional, academic, casual, technical, blog, email, bullets, executive
        public var id: String { rawValue }
        public var label: String {
            switch self {
            case .professional: "Professional"; case .academic: "Academic"; case .casual: "Casual"; case .technical: "Technical"
            case .blog: "Blog post"; case .email: "Email"; case .bullets: "Bullet points"; case .executive: "Executive summary"
            }
        }
        var direction: String {
            switch self {
            case .professional: "Style: clear and formal, suited to business communication."
            case .academic: "Style: scholarly, well structured, formal in tone."
            case .casual: "Style: friendly, conversational, relaxed."
            case .technical: "Style: precise and detailed, for a technical audience."
            case .blog: "Style: engaging and readable, with good flow and a hook."
            case .email: "Style: concise, clear and action-oriented, as an email body."
            case .bullets: "Style: the key points as a bulleted list."
            case .executive: "Style: a high-level overview for decision makers, the conclusion first."
            }
        }
    }

    public static let instructions = """
    You are a careful writing assistant working on the user's own text. Follow the task and style given. Keep every \
    fact in the text; when reference passages are given, use only them for anything you add, and never invent names, \
    numbers or quotes. Reply with the finished text only: no preamble, no notes about what you changed, no markdown \
    fences. Plain text with simple Markdown (headings, lists, emphasis) where the style calls for it.
    """

    /// The prompt for `task` in `format` over `text`, with the notebook's passages as reference when given.
    public static func prompt(text: String, task: Task, format: Format, maxWords: Int?, reference: [String]) -> String {
        var p = task.direction + "\n" + format.direction
        if let maxWords { p += "\nTarget length: about \(maxWords) words." }
        if !reference.isEmpty {
            p += "\n\nReference passages from the user's notebook (use these facts, cite none):\n\n"
            p += reference.enumerated().map { "[\($0 + 1)] \(String($1.prefix(900)))" }.joined(separator: "\n\n")
        }
        p += "\n\nText:\n\(text)"
        return p
    }

    /// The reply with fences and a leading "Here is…" line removed.
    public static func clean(_ reply: String) -> String {
        var t = reply.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.hasPrefix("```") {
            t = t.replacingOccurrences(of: #"^```[a-z]*\n"#, with: "", options: .regularExpression)
            if t.hasSuffix("```") { t.removeLast(3) }
        }
        if let m = t.range(of: #"^(Here('s| is)|Sure|Certainly)[^\n]*:\n+"#, options: .regularExpression) { t.removeSubrange(m) }
        return t.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    public static func wordCount(_ text: String) -> Int { text.split(whereSeparator: \.isWhitespace).count }
}
