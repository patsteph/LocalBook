import Foundation

/// The one search that crosses notebooks: `@curator`. The product rule is that a notebook's chat answers from that
/// notebook alone; only the Curator looks across all of them, and says which notebook said what. The merge, the
/// context and the prompt are the old `cross_notebook_search.py`'s; the search itself is the library's own, run per
/// notebook (`LibraryStore.search` loads one notebook's vectors at a time, as `closestNotebook` does).
public enum CrossNotebook {
    public static let mention = "@curator"
    public static let perNotebook = 3
    public static let topK = 8
    public static let maxContextChars = 8_000

    /// "@curator what links these projects?" → "what links these projects?"; nil when the text is not for the Curator.
    public static func ask(in text: String) -> String? {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard t.lowercased().hasPrefix(mention) else { return nil }
        let rest = t.dropFirst(mention.count)
        guard rest.isEmpty || rest.first!.isWhitespace || rest.first! == ":" || rest.first! == "," else { return nil }
        return rest.drop { $0.isWhitespace || $0 == ":" || $0 == "," }.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// `@curator ?` and an empty ask show what the Curator can do here.
    public static let help = """
    **@curator — your cross-notebook research advisor**

    | Ask | What happens |
    |---|---|
    | `@curator <any question>` | Searches every notebook and writes one answer that says which notebook said what |
    | `@curator ?` | Shows this help |

    The Curator's morning brief, weekly wrap, patterns and devil's advocate arrive with the agents build.
    """

    /// The best `topK` passages across notebooks, by cosine score, keeping at most `perNotebook` from each.
    public static func merge(_ perNotebookHits: [[LibraryHit]], perNotebook: Int = perNotebook, topK: Int = topK) -> [LibraryHit] {
        var all: [LibraryHit] = []
        for hits in perNotebookHits {
            all.append(contentsOf: hits.sorted { $0.score > $1.score }.prefix(perNotebook))
        }
        all.sort { $0.score != $1.score ? $0.score > $1.score : $0.id < $1.id }
        return Array(all.prefix(topK))
    }

    /// `[1] Notebook / source: text` per passage, within the character budget (the old `build_context`).
    public static func context(_ hits: [LibraryHit], maxChars: Int = maxContextChars) -> String {
        var lines: [String] = []
        var total = 0
        for (i, h) in hits.enumerated() {
            let entry = "[\(i + 1)] \(h.notebookTitle) / \(h.filename): \(h.text)"
            if total + entry.count > maxChars {
                let remaining = maxChars - total
                if remaining > 100 { lines.append(String(entry.prefix(remaining)) + "...") }
                break
            }
            lines.append(entry)
            total += entry.count
        }
        return lines.joined(separator: "\n\n")
    }

    public static let instructions = "Answer only from the passages given. Cite them as [1], [2] by their numbers, and say which notebook a point comes from when they differ. "
        + "Write plain text: no LaTeX or dollar-sign math, and no tables."

    public static func prompt(question: String, hits: [LibraryHit], notebooksSearched: Int) -> String {
        "Question: \(question)\n\nPassages from \(notebooksSearched) notebooks:\n\n\(context(hits))\n\nAnswer:"
    }

    /// The MCP shape of one cross-notebook hit (`search_notebooks`).
    public static func row(_ h: LibraryHit) -> [String: Any] {
        ["notebook_id": h.notebookId, "notebook_title": h.notebookTitle, "source_id": h.sourceId, "filename": h.filename,
         "text": h.text, "chunk_index": h.chunkIndex, "source_type": h.sourceType, "_distance": Double(1 - h.score)]
    }

    /// The line under a Curator answer: how many notebooks were read and which ones were cited.
    public static func status(notebooksSearched: Int, hits: [LibraryHit], seconds: Double) -> String {
        let names = Array(NSOrderedSet(array: hits.map(\.notebookTitle))) as? [String] ?? []
        var line = "Curator read \(notebooksSearched) notebook\(notebooksSearched == 1 ? "" : "s") in \(String(format: "%.1f", seconds)) s"
        if !names.isEmpty { line += " · cited \(names.prefix(4).joined(separator: ", "))\(names.count > 4 ? " and \(names.count - 4) more" : "")" }
        return line
    }
}
