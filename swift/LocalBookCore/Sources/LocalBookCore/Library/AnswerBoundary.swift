import Foundation

/// The per-notebook answer boundary: a question asked in a notebook is answered from that notebook or not at all.
///
/// Retrieval is already scoped to one notebook (`LibraryStore.search` takes exactly one), but nearest-neighbour
/// search always returns *something*: the five passages closest to "ttt" in a coffee notebook are still coffee
/// passages. The Mac's confidence rules cannot tell those apart from real matches, because the hybrid RRF score is
/// normalised (the best candidate is always 1.0, so its confidence is always 0.9 and the "very low confidence"
/// refusal never fires). The model then gets five unrelated passages and a question they do not cover, and a small
/// model fills the gap from what it knows (transformer architecture, for "ttt"). That is the leak.
///
/// This gate asks a plainer question before any generation: is there evidence in the kept passages that this
/// notebook holds anything about the question? Evidence is one of
///   - a word of the question (not a stopword) appearing in a passage, allowing a short common stem;
///   - the reranker rating the best passage clearly relevant (`strongRerank`), which covers paraphrases;
///   - the question being about the notebook as a whole ("main points", "what changed recently", "summarize").
/// Without evidence the app answers with `noAnswer(notebook:)` and never calls the model. With evidence, the model
/// is still told (`instructions(notebook:)`) that the passages are the only thing it may use, and an answer that is
/// the model's own refusal (`isRefusal`) is shown as the clean one-line refusal with no sources.
public enum AnswerBoundary {
    public struct Passage: Sendable {
        public let text: String
        /// The reranker's 0…1 relevance, nil when it did not run.
        public let rerank: Double?
        public init(text: String, rerank: Double?) { self.text = text; self.rerank = rerank }
    }

    /// A reranker score at or above this is evidence on its own (0.1 is the Mac's "0.24 confidence" line).
    public static let strongRerank = 0.1

    /// Does this notebook hold anything about the question? See the type comment for what counts.
    public static func hasEvidence(question: String, passages: [Passage], strongRerank: Double = strongRerank) -> Bool {
        guard !passages.isEmpty else { return false }
        if isHolistic(question) { return true }
        if let best = passages.compactMap(\.rerank).max(), best >= strongRerank { return true }
        let wanted = terms(of: question)
        guard !wanted.isEmpty else { return false }
        for p in passages {
            let have = Set(words(of: p.text))
            for term in wanted where have.contains(term) || (term.count >= 5 && have.contains(where: { sharesStem($0, term) })) {
                return true
            }
        }
        return false
    }

    /// A question about the notebook as a whole rather than one thing in it: always has evidence, since the
    /// passages *are* the notebook.
    public static func isHolistic(_ question: String) -> Bool {
        let w = Set(words(of: question))
        return !w.isDisjoint(with: holisticWords)
    }

    /// The question's content words: lowercase, letters and digits only, stopwords and chatter dropped, in order.
    public static func terms(of question: String) -> [String] {
        var seen = Set<String>()
        return words(of: question).filter { !stopwords.contains($0) && seen.insert($0).inserted }
    }

    /// The one-line refusal, naming the notebook so it is clear which boundary applied.
    public static func noAnswer(notebook: String) -> String {
        "I couldn't find anything about that in \(cleanTitle(notebook)). Try other words, or add a source about it."
    }

    /// The paragraph put in front of the answer model's instructions: this notebook only, these passages only.
    public static func instructions(notebook: String) -> String {
        let title = cleanTitle(notebook)
        return "You are answering inside one notebook, “\(title)”. The numbered passages are everything you may use, and they "
            + "come from this notebook only. Never use anything you know from elsewhere, and never answer about a topic the "
            + "passages do not cover, however the question is worded. If the passages do not answer the question, reply with "
            + "exactly this sentence and nothing else: \"\(refusalSentence(title))\""
    }

    /// Whether the model's answer is a refusal (ours or the Mac's), so the app shows the clean line and no sources.
    public static func isRefusal(_ answer: String) -> Bool {
        let a = answer.lowercased()
        guard a.count < 400 else { return false }                  // a long answer that mentions not finding one thing is still an answer
        return refusalMarkers.contains { a.contains($0) }
    }

    // MARK: words

    static func refusalSentence(_ title: String) -> String { "I couldn't find anything about that in \(title)." }

    static let refusalMarkers = [
        "i couldn't find anything about that in",
        "i couldn't find this in the documents",
        "i could not find this in the documents",
        "i don't have enough relevant information in your documents",
    ]

    static func cleanTitle(_ title: String) -> String {
        let t = title.trimmingCharacters(in: .whitespacesAndNewlines)
        return t.isEmpty ? "this notebook" : t
    }

    /// Lowercase runs of letters and digits.
    static func words(of text: String) -> [String] {
        text.lowercased().split { !$0.isLetter && !$0.isNumber }.map(String.init)
    }

    /// "roasting" and "roasted", "schedules" and "schedule": same first five letters, both at least five long.
    static func sharesStem(_ a: String, _ b: String) -> Bool {
        a.count >= 5 && b.count >= 5 && a.prefix(5) == b.prefix(5)
    }

    /// Words that say "this notebook as a whole" rather than naming a thing in it.
    static let holisticWords: Set<String> = [
        "notebook", "notebooks", "notes", "note", "document", "documents", "docs", "source", "sources", "file", "files",
        "everything", "overall", "overview", "summary", "summarize", "summarise", "summarization", "recap", "digest",
        "theme", "themes", "topic", "topics", "main", "key", "points", "highlights", "takeaways", "gist",
        "recent", "recently", "latest", "newest", "changed", "mentioned",
    ]

    /// Question words, function words and chatter: none of these is evidence of anything.
    static let stopwords: Set<String> = [
        "a", "an", "the", "and", "or", "but", "if", "then", "than", "that", "this", "these", "those", "there", "here",
        "what", "which", "who", "whom", "whose", "where", "when", "why", "how",
        "is", "are", "was", "were", "be", "been", "being", "am", "do", "does", "did", "done", "doing",
        "have", "has", "had", "having", "can", "could", "would", "should", "will", "shall", "may", "might", "must",
        "of", "in", "on", "at", "to", "for", "from", "by", "with", "about", "into", "over", "under", "after", "before",
        "between", "during", "without", "within", "through", "across", "up", "down", "out", "off", "again", "further",
        "it", "its", "they", "them", "their", "theirs", "we", "us", "our", "ours", "you", "your", "yours",
        "he", "him", "his", "she", "her", "hers", "i", "me", "my", "mine", "myself", "yourself", "itself",
        "not", "no", "yes", "nor", "any", "all", "some", "each", "every", "both", "few", "more", "most", "other",
        "others", "such", "only", "own", "same", "so", "too", "very", "just", "also", "once", "ever", "never",
        "please", "tell", "give", "show", "list", "find", "explain", "describe", "say", "says", "said", "know",
        "mean", "means", "get", "got", "like", "want", "need", "thing", "things", "something", "anything",
        "everything", "nothing", "way", "lot", "much", "many", "one", "two", "ok", "okay",
        "hi", "hello", "hey", "thanks", "thank", "s", "t", "re", "ve", "ll", "d", "m",
    ]
}
