import Foundation
import LocalBookCore
import HuggingFace
import MLXHuggingFace
import Tokenizers
import MLXLMCommon
import PDFKit
#if canImport(AppKit)
import AppKit
#endif

/// Phase 3, slice 2: a file becomes a source on this device, the way the old app did it, without the Python Mac.
/// Text is read from the file, cut with the old app's chunker, kept inside the embedder's token budget, given a
/// two-sentence summary (chunk -1) and three questions per chunk (HyDE) by the answer model, embedded with the
/// document template, and written to the library as a local source that sync never touches.
public struct IngestReport: Sendable {
    public let sourceId: String
    public let title: String
    public let format: String
    public let characters: Int
    public let chunks: Int
    public let summarized: Bool
    public let questioned: Int
}

/// Text out of a file, with the format name the old app would give it. PDF through PDFKit with the old app's page
/// markers; Word, RTF, OpenDocument, HTML and web archives through the system document importer (Mac only).
public enum TextExtract {
    /// Formats this device can read into a source today. Spreadsheets, PowerPoint, EPUB and notebooks wait in the Inbox.
    public static func format(for url: URL) -> String? {
        let ext = url.pathExtension.lowercased()
        switch ext {
        case "pdf": return "pdf"
        case "txt", "md", "markdown", "json", "xml", "py", "js", "ts", "css", "yaml", "yml", "tex", "bib": return ext
        #if canImport(AppKit)
        case "docx", "doc", "rtf", "rtfd", "odt", "html", "htm", "webarchive": return ext
        #endif
        default: return nil
        }
    }

    public static func canRead(_ url: URL) -> Bool { format(for: url) != nil }

    public static func text(from url: URL) throws -> String {
        guard let format = format(for: url) else { throw LabError.model("\(url.lastPathComponent) is a kind of file this Mac cannot read into a source yet") }
        switch format {
        case "pdf":
            guard let doc = PDFDocument(url: url) else { throw LabError.model("\(url.lastPathComponent) could not be opened as a PDF") }
            var pages: [String] = []
            for i in 0..<doc.pageCount {
                var piece = ["=== Page \(i + 1) ==="]
                let text = (doc.page(at: i)?.string ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                if !text.isEmpty { piece.append(text) }
                pages.append(piece.joined(separator: "\n"))
            }
            let text = pages.joined(separator: "\n\n")
            if doc.pageCount > 0, text.filter({ !$0.isWhitespace && $0 != "=" }).count < 40 {
                throw LabError.model("\(url.lastPathComponent) has no text layer; scanned PDFs are read in a later slice (Vision)")
            }
            return text
        case "docx", "doc", "rtf", "rtfd", "odt", "html", "htm", "webarchive":
            #if canImport(AppKit)
            let attributed = try NSAttributedString(url: url, options: [:], documentAttributes: nil)
            // python-docx hands paragraphs back one by one, joined by blank lines; the importer gives one string with a
            // newline per paragraph, so the same shape is rebuilt here.
            let paragraphs = attributed.string.components(separatedBy: .newlines).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
            return paragraphs.joined(separator: "\n\n")
            #else
            throw LabError.model("Word and rich-text files are read on the Mac")
            #endif
        default:
            let data = try Data(contentsOf: url)
            return String(data: data, encoding: .utf8) ?? String(data: data, encoding: .isoLatin1) ?? ""
        }
    }
}

extension LibraryModel {
    /// Read `url` into a new source of `notebook`. `progress` hears each stage in words.
    @discardableResult
    public func ingestFile(_ url: URL, notebook: String, enrich: Bool = true, progress: (@MainActor (String) -> Void)? = nil) async throws -> IngestReport {
        guard let format = TextExtract.format(for: url) else { throw LabError.model("\(url.lastPathComponent) is a kind of file this Mac cannot read into a source yet") }
        progress?("Reading \(url.lastPathComponent)…")
        let text = try TextExtract.text(from: url)
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw LabError.model("No text could be read from \(url.lastPathComponent)") }
        return try await ingestText(text, title: url.lastPathComponent, format: format, notebook: notebook, url: nil, enrich: enrich, progress: progress)
    }

    /// Make `text` a source of `notebook`: chunk, budget, summarise, question, embed, store. The old app's
    /// `rag_storage.ingest_document`, with its prompts, minus the topic model and entity graph (Phase 5).
    @discardableResult
    public func ingestText(_ text: String, title: String, format: String, notebook: String, url: String?, enrich: Bool = true,
                           progress: (@MainActor (String) -> Void)? = nil) async throws -> IngestReport {
        guard let store else { throw LibraryError.badResponse("library not open") }
        await waitForSearch()
        guard let engine, let tokenizer else { throw LibraryError.badResponse("search model not ready") }

        progress?("Cutting \(title) into passages…")
        let raw = Chunker.chunks(text: text, sourceType: format, filename: title)
        let chunks = TokenBudget.enforce(raw) { tokenizer.encode(text: $0, addSpecialTokens: false).count }
        guard !chunks.isEmpty else { throw LabError.model("\(title) had nothing to index") }

        var summary: String?
        if enrich, PyLen(text) >= 500 {
            progress?("Summarising \(title)…")
            summary = try? await generateText(Self.summaryPrompt(text: text, filename: title, format: format), maxTokens: 200)
            if let s = summary?.trimmingCharacters(in: .whitespacesAndNewlines), s.count > 20 { summary = s } else { summary = nil }
        }

        var questions = [String](repeating: "", count: chunks.count)
        var questioned = 0
        if enrich {
            let eligible = min(chunks.count, Self.hydeMaxChunks)
            var start = 0
            while start < eligible {
                let batch = Array(chunks[start..<min(eligible, start + Self.hydeChunksPerBatch)])
                progress?("Writing questions for passages \(start + 1)–\(start + batch.count) of \(eligible)…")
                if let reply = try? await generateText(Self.hydePrompt(batch), maxTokens: 90 * batch.count) {
                    for (i, q) in Self.parseHyde(reply, count: batch.count).enumerated() where !q.isEmpty {
                        questions[start + i] = q; questioned += 1
                    }
                }
                start += batch.count
                try Task.checkCancellation()
            }
        }

        progress?("Embedding \(chunks.count) passages…")
        let sourceId = LibraryStore.localNotePrefix + "file-" + UUID().uuidString.lowercased()
        var rows: [LibraryChunk] = []
        for (i, chunk) in chunks.enumerated() {
            let toEmbed = questions[i].isEmpty ? chunk : "\(chunk)\n\nQuestions this answers:\n\(questions[i])"
            let ids = TokenRepair.spaceFallback(tokenizer.encode(text: "title: none | text: " + toEmbed))
            let v = try await engine.embed(tokenIds: Array(ids.prefix(EmbeddingGemma2Engine.maxTokens)))
            let half = v.map { Float16($0) }
            rows.append(.init(notebookId: notebook, sourceId: sourceId, chunkIndex: i, text: chunk, summary: false,
                              filename: title, sourceType: format, vector: half.withUnsafeBytes { Data($0) }))
            if i % 8 == 7 { progress?("Embedding passage \(i + 1) of \(chunks.count)…") }
            try Task.checkCancellation()
        }
        if let summary {
            let ids = TokenRepair.spaceFallback(tokenizer.encode(text: "title: none | text: " + summary))
            let v = try await engine.embed(tokenIds: Array(ids.prefix(EmbeddingGemma2Engine.maxTokens)))
            let half = v.map { Float16($0) }
            rows.append(.init(notebookId: notebook, sourceId: sourceId, chunkIndex: -1, text: summary, summary: true,
                              filename: title, sourceType: "summary", vector: half.withUnsafeBytes { Data($0) }))
        }
        try await store.addLocalSource(notebook: notebook, sourceId: sourceId, title: title, format: format, text: text, url: url, chunks: rows)
        await refresh()
        return IngestReport(sourceId: sourceId, title: title, format: format, characters: PyLen(text), chunks: chunks.count,
                            summarized: summary != nil, questioned: questioned)
    }

    // MARK: the answer model as a plain text generator

    /// One prompt, one reply, from the loaded answer model (loaded now if it is not).
    func generateText(_ prompt: String, maxTokens: Int) async throws -> String {
        if gemma == nil {
            SoakTrace.log("answer model load begins (ingest)")
            gemma = try await #huggingFaceLoadModelContainer(configuration: AnswerModels.configuration)
            SoakTrace.log("answer model load ends (ingest)")
        }
        guard let gemma else { throw LibraryError.badResponse("answer model not loaded") }
        let session = ChatSession(gemma, instructions: "Do exactly what the request asks and reply with the requested text only.",
                                  generateParameters: GenerateParameters(maxTokens: maxTokens, temperature: 0.3))
        var out = ""
        for try await item in session.streamDetails(to: prompt) {
            if case .chunk(let c) = item { out += c }
        }
        return out
    }

    // MARK: the old app's prompts (rag_storage.generate_document_summary, generate_chunk_questions)

    static let hydeChunksPerBatch = 3
    static let hydeMaxChunks = 200

    static func summaryPrompt(text: String, filename: String, format: String) -> String {
        let sample = PyPrefix(text, 4000)
        if ["xlsx", "csv", "tabular"].contains(format) {
            return """
            Summarize this tabular data from '\(filename)'. Include:
            - What entities/people are tracked
            - What metrics/values are recorded
            - Time periods covered
            - Key totals or patterns

            Data sample:
            \(sample)

            Summary (2-3 sentences):
            """
        }
        return """
        Summarize the key points from '\(filename)' in 2-3 sentences. Focus on:
        - Main topic/purpose
        - Key facts or findings
        - Important entities mentioned

        Content:
        \(sample)

        Summary:
        """
    }

    static func hydePrompt(_ batch: [String]) -> String {
        let parts = batch.enumerated().map { "P\($0.offset + 1): \(PyPrefix($0.element, 1500))" }
        return "Below are \(batch.count) passages labeled P1..P\(batch.count). For EACH passage, write exactly 3 short, specific questions it directly answers.\n"
            + "Output ONE line per passage, in this EXACT format and nothing else:\n"
            + "P1: <question1> <question2> <question3>\n"
            + "P2: <question1> <question2> <question3>\n\n"
            + parts.joined(separator: "\n\n")
    }

    /// `_HYDE_LINE_RE`: one questions string per passage, "" where the model gave none.
    static func parseHyde(_ reply: String, count: Int) -> [String] {
        var out = [String](repeating: "", count: count)
        guard let re = try? NSRegularExpression(pattern: "^\\s*P\\s*(\\d+)\\s*[:\\-.]\\s*(.+?)\\s*$", options: [.caseInsensitive, .anchorsMatchLines]) else { return out }
        let ns = reply as NSString
        for m in re.matches(in: reply, range: NSRange(location: 0, length: ns.length)) {
            guard let n = Int(ns.substring(with: m.range(at: 1))), n >= 1, n <= count else { continue }
            out[n - 1] = ns.substring(with: m.range(at: 2)).trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return out
    }
}

/// Python-style length and prefix (code points), as the old app's limits were written.
private func PyLen(_ s: String) -> Int { s.unicodeScalars.count }
private func PyPrefix(_ s: String, _ n: Int) -> String {
    guard n < s.unicodeScalars.count else { return s }
    var v = String.UnicodeScalarView(); v.append(contentsOf: s.unicodeScalars.prefix(n)); return String(v)
}
