import Foundation

/// Keeps every chunk inside the embedder's token limit by splitting, never cutting: the old app's
/// `services/token_budget.py`. EmbeddingGemma 2 embeds at most 512 tokens; the document template and the HyDE block
/// ("Questions this answers:" and a few questions) take some of them, so a chunk may use 400. A chunk over that is
/// broken at the coarsest boundary that fits (paragraphs, lines, sentences, words, then characters) and nothing is lost.
public enum TokenBudget {
    public static let embedderMaxTokens = 512
    public static let enrichmentReserveTokens = 112
    public static var budget: Int { max(32, embedderMaxTokens - enrichmentReserveTokens) }

    /// The estimate used when no tokenizer is at hand: two characters per token, which only ever splits early.
    public static func fallbackCount(_ text: String) -> Int { Int(Double(PyText.len(text)) / 2.0) + 1 }

    /// `enforce`: `chunks` with any chunk over `budget` tokens split into pieces that fit.
    public static func enforce(_ chunks: [String], budget: Int = TokenBudget.budget, count: (String) -> Int) -> [String] {
        var out: [String] = []
        for chunk in chunks {
            if count(chunk) <= budget { out.append(chunk) } else { out.append(contentsOf: splitToBudget(chunk, budget: budget, count: count)) }
        }
        return out
    }

    /// `split_to_budget`
    public static func splitToBudget(_ text: String, budget: Int, count: (String) -> Int) -> [String] {
        pieces(text, count: count, budget: budget).map(PyText.strip).filter { !$0.isEmpty }
    }

    /// `_pieces`: break `text` at the coarsest boundary that leaves every piece within `budget`.
    static func pieces(_ text: String, count: (String) -> Int, budget: Int) -> [String] {
        if count(text) <= budget { return [text] }
        for (sep, join) in [("\n\n", "\n\n"), ("\n", "\n"), (nil, " ")] as [(String?, String)] {
            let parts = (sep == nil ? PyText.reSplit("(?<=[.!?])\\s+", text) : PyText.split(text, sep!)).filter { !PyText.strip($0).isEmpty }
            if parts.count > 1 { return pack(parts, join: join, count: count, budget: budget) }
        }
        let words = PyText.splitWhitespace(text)
        if words.count > 1 { return pack(words, join: " ", count: count, budget: budget) }
        let step = max(1, Int(Double(budget) * 2.0) / 2)
        var out: [String] = []
        var i = 0
        let n = PyText.len(text)
        while i < n { out.append(PyText.slice(text, i, i + step)); i += step }
        return out
    }

    /// `_pack`
    static func pack(_ parts: [String], join: String, count: (String) -> Int, budget: Int) -> [String] {
        var out: [String] = []
        var buf = ""
        for part in parts {
            for piece in pieces(part, count: count, budget: budget) {
                let trial = buf.isEmpty ? piece : buf + join + piece
                if !buf.isEmpty && count(trial) > budget {
                    out.append(buf); buf = piece
                } else {
                    buf = trial
                }
            }
        }
        if !buf.isEmpty { out.append(buf) }
        return out
    }
}
