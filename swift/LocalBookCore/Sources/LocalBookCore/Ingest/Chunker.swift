import Foundation

/// The old app's chunker, piece for piece (`backend/services/rag_chunking.py` and `hierarchical_chunker.py`), so a
/// source ingested here is cut exactly as the Python Mac would cut it and a citation points at the same passage on
/// every device. The quirks are kept on purpose: the golden fixture written by the Python functions is the test.
///
/// Routing (`chunk_text_smart`): spreadsheets and CSVs, or text that opens with `Row N:` lines, are cut by rows with the
/// header repeated; PDF, Word and PowerPoint text over 2,000 characters goes by sections and paragraphs; everything
/// else by paragraphs, then sentences, with a 200-character overlap between neighbours.
public enum Chunker {
    public static let chunkSize = 1000
    public static let chunkOverlap = 200

    /// `_chunk_text_by_type`: the chunks before the token budget is applied (see `TokenBudget.enforce`).
    public static func chunks(text: String, sourceType: String, filename: String) -> [String] {
        let lower = filename.lowercased()
        var tabular = ["xlsx", "xls", "csv"].contains(sourceType) || lower.hasSuffix(".xlsx") || lower.hasSuffix(".xls") || lower.hasSuffix(".csv")
        let head = PyText.prefix(text, 500)
        if !tabular && PyText.contains(head, "Row ") && PyText.contains(head, ": ") { tabular = true }
        if tabular { return tabularChunks(text) }
        let structured = ["pdf", "docx", "doc", "pptx"].contains(sourceType)
            || lower.hasSuffix(".pdf") || lower.hasSuffix(".docx") || lower.hasSuffix(".doc") || lower.hasSuffix(".pptx")
        if structured && PyText.len(text) > 2000 { return hierarchicalChunks(text, filename: filename) }
        return standardChunks(text)
    }

    // MARK: standard (`chunk_text`)

    public static func standardChunks(_ text: String) -> [String] {
        let size = chunkSize, overlap = chunkOverlap
        var paragraphs = PyText.split(text, "\n\n").map(PyText.strip).filter { !$0.isEmpty }
        if paragraphs.isEmpty { paragraphs = PyText.split(text, "\n").map(PyText.strip).filter { !$0.isEmpty } }
        var chunks: [String] = []
        var current = ""
        for para in paragraphs {
            if PyText.len(current) + PyText.len(para) + 2 > size {
                if !current.isEmpty { chunks.append(PyText.strip(current)) }
                if PyText.len(para) > size {
                    for sentence in sentences(para) {
                        if PyText.len(current) + PyText.len(sentence) + 1 > size {
                            if !current.isEmpty { chunks.append(PyText.strip(current)) }
                            if PyText.len(sentence) > size {
                                chunks.append(contentsOf: charSplit(sentence, size: size, overlap: overlap))
                                current = ""
                            } else {
                                current = sentence
                            }
                        } else {
                            current = current.isEmpty ? sentence : PyText.strip(current + " " + sentence)
                        }
                    }
                } else {
                    current = para
                }
            } else {
                current = current.isEmpty ? para : PyText.strip(current + "\n\n" + para)
            }
        }
        if !current.isEmpty { chunks.append(PyText.strip(current)) }
        guard !chunks.isEmpty else { return [] }
        if overlap > 0 && chunks.count > 1 {
            var out = [chunks[0]]
            for i in 1..<chunks.count {
                let prev = chunks[i - 1]
                let tail = PyText.len(prev) > overlap ? PyText.suffix(prev, overlap) : prev
                out.append(tail + "\n" + chunks[i])
            }
            chunks = out
        }
        return chunks
    }

    /// `split_into_sentences`
    static func sentences(_ text: String) -> [String] {
        PyText.reSplit("(?<=[.!?])\\s+", text).map(PyText.strip).filter { !$0.isEmpty }
    }

    /// `char_split`
    static func charSplit(_ text: String, size: Int, overlap: Int) -> [String] {
        var out: [String] = []
        let n = PyText.len(text)
        var start = 0
        while start < n {
            out.append(PyText.slice(text, start, start + size))
            start = start + size - overlap
        }
        return out
    }

    // MARK: tabular (`chunk_tabular_data`)

    public static func tabularChunks(_ text: String) -> [String] {
        let maxChars = chunkSize
        var headerLines: [String] = [], dataLines: [String] = []
        for line in PyText.split(text, "\n") {
            let s = PyText.strip(line)
            if s.isEmpty { continue }
            if s.hasPrefix("===") || s.hasPrefix("Data from sheet") || s.hasPrefix("Complete row data") || s.hasPrefix("This data is from")
                || (PyText.contains(s, "Column") && PyText.contains(s, ":") && s.hasPrefix("Row 1:")) {
                headerLines.append(s)
            } else {
                dataLines.append(s)
            }
        }
        let header = headerLines.isEmpty ? "" : headerLines.prefix(5).joined(separator: "\n")
        let headerLen = PyText.len(header) + 2
        var chunks: [String] = []
        var lines: [String] = []
        var length = headerLen
        func joined(_ ls: [String]) -> String { header.isEmpty ? ls.joined(separator: "\n") : header + "\n\n" + ls.joined(separator: "\n") }
        for line in dataLines {
            let lineLen = PyText.len(line) + 1
            if length + lineLen > maxChars && !lines.isEmpty {
                chunks.append(joined(lines))
                lines = [line]; length = headerLen + lineLen
            } else {
                lines.append(line); length += lineLen
            }
        }
        if !lines.isEmpty {
            let c = joined(lines)
            if !PyText.strip(c).isEmpty { chunks.append(c) }
        }
        return chunks.isEmpty ? standardChunks(text) : chunks
    }

    // MARK: hierarchical (`chunk_hierarchical` over `HierarchicalChunker`, leaves only, merged)

    static let sectionPatterns = [
        "^#{1,3}\\s+(.+)$",             // Markdown headers
        "^([A-Z][A-Za-z\\s]+):$",       // Title: format
        "^\\d+\\.\\s+([A-Z].+)$",       // numbered sections
        "^===\\s*Page\\s+\\d+\\s*===$", // PDF page markers
        "^[A-Z][A-Z\\s]{5,50}$",        // ALL CAPS headers
    ]

    /// `_detect_sections`: (title, content) in order; the heading line stays in the content.
    static func sections(_ text: String) -> [(title: String, content: String)] {
        var out: [(String, String)] = []
        var title = "Introduction"
        var content: [String] = []
        for line in PyText.split(text, "\n") {
            var header: String?
            let stripped = PyText.strip(line)
            for p in sectionPatterns {
                if let groups = PyText.match(p, stripped) {
                    header = groups.count > 1 ? (groups[1] ?? stripped) : stripped
                    break
                }
            }
            if let header, !header.isEmpty {
                if !content.isEmpty { out.append((title, content.joined(separator: "\n"))) }
                title = header
                content = [line]
            } else {
                content.append(line)
            }
        }
        if !content.isEmpty { out.append((title, content.joined(separator: "\n"))) }
        return out.map { (title: $0.0, content: $0.1) }
    }

    /// `_split_into_paragraphs`
    static func paragraphs(_ text: String) -> [String] {
        PyText.reSplit("\\n\\s*\\n|\\n(?=\\s{2,})", text).map(PyText.strip).filter { !$0.isEmpty }
    }

    /// `_merge_small_chunks`
    static func mergeSmall(_ chunks: [String], minSize: Int, maxSize: Int) -> [String] {
        guard let first = chunks.first else { return [] }
        var merged: [String] = []
        var current = first
        for c in chunks.dropFirst() {
            if PyText.len(current) < minSize && PyText.len(current) + PyText.len(c) < maxSize {
                current = current + "\n\n" + c
            } else {
                if !PyText.strip(current).isEmpty { merged.append(PyText.strip(current)) }
                current = c
            }
        }
        if !PyText.strip(current).isEmpty { merged.append(PyText.strip(current)) }
        return merged
    }

    public static func hierarchicalChunks(_ text: String, filename: String) -> [String] {
        // The leaves of HierarchicalChunker.chunk_document("temp"): a paragraph (level 2), or a section (level 1)
        // with no paragraph of 50+ characters under it. Each leaf is (group, text): the section for a paragraph,
        // the document for a leaf section.
        var pieces: [(group: String, body: String)] = []
        for (i, section) in sections(text).enumerated() {
            if PyText.strip(section.content).isEmpty { continue }
            let sectionId = "temp_s\(i)"
            let paras = mergeSmall(paragraphs(section.content), minSize: 100, maxSize: 500).filter { PyText.len($0) >= 50 }
            if paras.isEmpty {
                pieces.append((group: "temp_doc", body: PyText.prefix(section.content, 1500)))
            } else {
                for p in paras { pieces.append((group: sectionId, body: p)) }
            }
        }
        let result = mergeAdjacent(pieces)
        return result.isEmpty ? standardChunks(text) : result
    }

    /// `_merge_adjacent`
    static func mergeAdjacent(_ pieces: [(group: String, body: String)]) -> [String] {
        let target = chunkSize
        let floor = max(1, target / 4)
        var out: [String] = []
        var buf: [String] = []
        var bufGroup = ""
        func flush() { if !buf.isEmpty { out.append(PyText.strip(buf.joined(separator: "\n\n"))) } }
        for piece in pieces {
            let body = PyText.strip(piece.body)
            if body.isEmpty { continue }
            if buf.isEmpty { buf = [body]; bufGroup = piece.group; continue }
            let currentLen = buf.reduce(0) { $0 + PyText.len($1) } + 2 * buf.count
            let sameSection = piece.group == bufGroup
            let wouldFit = currentLen + PyText.len(body) <= target
            if wouldFit && (sameSection || currentLen < floor) {
                buf.append(body)
                if !sameSection { bufGroup = piece.group }
            } else {
                flush()
                buf = [body]; bufGroup = piece.group
            }
        }
        flush()
        out = out.filter { !$0.isEmpty }
        if out.count > 1, PyText.len(out[out.count - 1]) < floor,
           Double(PyText.len(out[out.count - 2]) + PyText.len(out[out.count - 1])) <= Double(target) * 1.5 {
            let tail = out.removeLast()
            out[out.count - 1] = out[out.count - 1] + "\n\n" + tail
        }
        return out
    }

    // MARK: parent text (`get_parent_context`)

    /// The chunk with its neighbours around it, up to `maxChars`: what the old app stores as `parent_text`.
    public static func parentContext(_ chunks: [String], index: Int, maxChars: Int = 2000) -> String {
        guard !chunks.isEmpty, index >= 0, index < chunks.count else { return "" }
        var parts = [chunks[index]]
        var length = PyText.len(chunks[index])
        var prev = index - 1
        while prev >= 0 && length < maxChars {
            let p = chunks[prev]
            if length + PyText.len(p) > maxChars {
                parts.insert(PyText.suffix(p, maxChars - length) + "...", at: 0)
                break
            }
            parts.insert(p, at: 0); length += PyText.len(p); prev -= 1
        }
        var next = index + 1
        while next < chunks.count && length < maxChars {
            let n = chunks[next]
            if length + PyText.len(n) > maxChars {
                parts.append("..." + PyText.prefix(n, maxChars - length))
                break
            }
            parts.append(n); length += PyText.len(n); next += 1
        }
        return parts.joined(separator: "\n\n")
    }
}
