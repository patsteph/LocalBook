import Foundation
import LocalBookCore
import HuggingFace
import MLXHuggingFace
import Tokenizers
import MLXLMCommon

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

extension LibraryModel {
    /// What becomes of the file itself once its text is in the library.
    public enum OriginalFile: Sendable {
        /// Copied under the app's files (`Sources/<notebook>/`), so Quick Look and "Show in Finder" work for good.
        case copy
        /// Left where it is and remembered by path (a watched folder the user granted).
        case reference
        case none
    }

    /// Read `url` into a new source of `notebook`. `progress` hears each stage in words. `title` and `link` stand in
    /// for the filename and record where a downloaded file came from.
    @discardableResult
    public func ingestFile(_ url: URL, notebook: String, title: String? = nil, link: String? = nil, enrich: Bool = true, keep: OriginalFile = .copy,
                           progress: (@MainActor (String) -> Void)? = nil) async throws -> IngestReport {
        guard let format = TextExtract.format(for: url) else { throw LabError.model("\(url.lastPathComponent) is a kind of file this Mac cannot read into a source yet") }
        progress?("Reading \(url.lastPathComponent)…")
        let text = try await TextExtract.text(from: url) { s in Task { @MainActor in progress?(s) } }
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw LabError.model("No text could be read from \(url.lastPathComponent)") }
        let sourceId = Self.newSourceId()
        let file: String?
        switch keep {
        case .copy: file = try Self.keepOriginal(url, notebook: notebook, sourceId: sourceId)
        case .reference: file = url.path(percentEncoded: false)
        case .none: file = nil
        }
        return try await ingestText(text, title: title ?? url.lastPathComponent, format: format, notebook: notebook, url: link, enrich: enrich,
                                    sourceId: sourceId, file: file, progress: progress)
    }

    public static func newSourceId() -> String { LibraryStore.localNotePrefix + "file-" + UUID().uuidString.lowercased() }

    /// Copies a file under `Sources/<notebook>/` in the app's files; returns the path relative to the user files folder.
    public static func keepOriginal(_ url: URL, notebook: String, sourceId: String) throws -> String {
        let folder = try AppFolders.ensure(AppFolders.userFiles.appending(path: "Sources", directoryHint: .isDirectory).appending(path: notebook, directoryHint: .isDirectory))
        let stem = String(sourceId.split(separator: "-").last ?? "file").prefix(8)
        let name = "\(stem) \(url.lastPathComponent)"
        let dest = folder.appending(path: name)
        if FileManager.default.fileExists(atPath: dest.path) { try FileManager.default.removeItem(at: dest) }
        try FileManager.default.copyItem(at: url, to: dest)
        return "Sources/\(notebook)/\(name)"
    }

    /// The kept original's location on disk, for Quick Look and the Finder.
    public static func originalURL(_ file: String) -> URL {
        file.hasPrefix("/") ? URL(filePath: file) : AppFolders.userFiles.appending(path: file)
    }

    /// Make a web address a source of `notebook`: the page's article, a video's transcript, or the document the
    /// address serves. The old app's `_scrape_and_ingest_background`, with the same source types ("web", "youtube").
    @discardableResult
    public func ingestLink(_ link: URL, notebook: String, enrich: Bool = true, progress: (@MainActor (String) -> Void)? = nil) async throws -> IngestReport {
        let fetched = try await WebExtract.fetch(link) { s in Task { @MainActor in progress?(s) } }
        switch fetched {
        case .file(let file, let name):
            defer { try? FileManager.default.removeItem(at: file.deletingLastPathComponent()) }
            return try await ingestFile(file, notebook: notebook, title: name, link: link.absoluteString, enrich: enrich, keep: .copy, progress: progress)
        case .text(let title, let text, let format, _, _):
            return try await ingestText(text, title: title, format: format, notebook: notebook, url: link.absoluteString, enrich: enrich, progress: progress)
        }
    }

    /// Make `text` a source of `notebook`: chunk, budget, summarise, question, embed, store. The old app's
    /// `rag_storage.ingest_document`, with its prompts, minus the topic model and entity graph (Phase 5).
    @discardableResult
    public func ingestText(_ text: String, title: String, format: String, notebook: String, url: String?, enrich: Bool = true,
                           sourceId: String? = nil, file: String? = nil, progress: (@MainActor (String) -> Void)? = nil) async throws -> IngestReport {
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
        let sourceId = sourceId ?? Self.newSourceId()
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
        try await store.addLocalSource(notebook: notebook, sourceId: sourceId, title: title, format: format, text: text, url: url, chunks: rows, file: file)
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
