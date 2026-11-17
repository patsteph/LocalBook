import Foundation
import LocalBookCore
import PDFKit
import Vision
#if canImport(AppKit)
import AppKit
#endif
#if canImport(UIKit)
import UIKit
#endif

/// Text out of a file, with the format name the old app would give it (`DocumentProcessor._extract_text`).
///
/// - PDF: PDFKit's text layer per page under the old app's `=== Page N ===` markers, with headings marked from the
///   fonts and ruled tables as Markdown tables (what pymupdf4llm's Markdown gave the chunker, `PDFTables`); a page with
///   no text layer is read with Vision instead.
/// - Word, RTF, OpenDocument, HTML, web archive: the system document importer (Mac only), paragraphs on blank lines.
/// - Spreadsheets and CSV: rows as sentences, the old app's shape (`TabularText`).
/// - PowerPoint: slide markers, shape text, tables and speaker notes (`PPTXReader`).
/// - Excel 97-2003, PowerPoint 97-2003, OpenDocument spreadsheets: own readers (`XLSReader`, `PPTLegacyReader`, `ODSReader`).
/// - EPUB: spine order with headings (`EPUBReader`); Jupyter notebooks: cells and outputs (`JupyterText`); SVG: its words.
/// - Plain text kinds: the bytes as UTF-8 (Latin-1 if not).
public enum TextExtract {
    /// Formats this device can read into a source today.
    public static func format(for url: URL) -> String? {
        let ext = url.pathExtension.lowercased()
        switch ext {
        case "pdf", "xlsx", "csv", "pptx", "xls", "ppt", "ods", "epub", "ipynb", "svg": return ext
        case "txt", "md", "markdown", "json", "xml", "py", "js", "ts", "css", "yaml", "yml", "tex", "bib": return ext
        #if canImport(AppKit)
        case "docx", "doc", "rtf", "rtfd", "odt", "html", "htm", "webarchive": return ext
        #endif
        default: return nil
        }
    }

    public static func canRead(_ url: URL) -> Bool { format(for: url) != nil }

    /// `progress` hears page-by-page progress on long PDFs.
    public static func text(from url: URL, progress: (@Sendable (String) -> Void)? = nil) async throws -> String {
        guard let format = format(for: url) else { throw LabError.model("\(url.lastPathComponent) is a kind of file this Mac cannot read into a source yet") }
        let name = url.lastPathComponent
        switch format {
        case "pdf":
            return try await Task.detached(priority: .userInitiated) { try await pdfText(url, name: name, progress: progress) }.value
        case "xlsx":
            return try await Task.detached(priority: .userInitiated) {
                let sheets = try XLSXReader.read(url)
                guard !sheets.isEmpty else { throw LabError.model("\(name) has no sheets") }
                return TabularText.workbookText(sheets)
            }.value
        case "csv":
            let raw = try plainText(url)
            let table = CSV.table(raw)
            let sentences = TabularText.sentences(table, sheet: "CSV Data")
            return table.rows.isEmpty ? raw : sentences
        case "pptx":
            return try await Task.detached(priority: .userInitiated) { try PPTXReader.read(url) }.value
        case "xls":
            return try await Task.detached(priority: .userInitiated) {
                let sheets = try XLSReader.read(try Data(contentsOf: url))
                guard !sheets.isEmpty else { throw LabError.model("\(name) has no sheets") }
                return TabularText.workbookText(sheets)
            }.value
        case "ppt":
            return try await Task.detached(priority: .userInitiated) { try PPTLegacyReader.read(try Data(contentsOf: url)) }.value
        case "ods":
            return try await Task.detached(priority: .userInitiated) { try ODSReader.read(url) }.value
        case "epub":
            return try await Task.detached(priority: .userInitiated) { try EPUBReader.read(url) }.value
        case "ipynb":
            return try JupyterText.text(try Data(contentsOf: url))
        case "svg":
            return SVGText.read(try Data(contentsOf: url))
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
            return try plainText(url)
        }
    }

    static func plainText(_ url: URL) throws -> String {
        let data = try Data(contentsOf: url)
        return String(data: data, encoding: .utf8) ?? String(data: data, encoding: .isoLatin1) ?? ""
    }

    // MARK: PDF

    static func pdfText(_ url: URL, name: String, progress: (@Sendable (String) -> Void)?) async throws -> String {
        guard let doc = PDFDocument(url: url) else { throw LabError.model("\(name) could not be opened as a PDF") }
        var pages: [String] = []
        var scanned = 0
        for i in 0..<doc.pageCount {
            guard let page = doc.page(at: i) else { continue }
            if doc.pageCount > 3, i % 5 == 0 { progress?("Reading page \(i + 1) of \(doc.pageCount) of \(name)…") }
            var text = (page.attributedString.map(markHeadings) ?? page.string ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            if text.filter({ !$0.isWhitespace }).count < 20 {                 // no text layer: a scan, or a picture page
                if let read = await recognize(page), !read.isEmpty { text = read; scanned += 1 }
            } else if Self.readsTables {
                // Ruled tables become Markdown tables, as pymupdf4llm wrote them; their cells leave the prose.
                text = PDFTables.merge(text: text, page: page, tables: await PDFTables.find(on: page))
            }
            var piece = ["=== Page \(i + 1) ==="]
            if !text.isEmpty { piece.append(text) }
            pages.append(piece.joined(separator: "\n"))
            try Task.checkCancellation()
        }
        let text = pages.joined(separator: "\n\n")
        if text.filter({ !$0.isWhitespace && $0 != "=" }).count < 40 {
            throw LabError.model("\(name) has no readable text, even with the page images read")
        }
        return text
    }

    /// Whether PDF pages are also read for ruled tables (Vision's document reader, a few hundred milliseconds a page).
    nonisolated(unsafe) static var readsTables = true

    /// Lines set in a larger type than the page's body text become Markdown headings, which is how the old app's PDF
    /// Markdown reached the section-aware chunker. The body size is the size that covers the most characters; a short
    /// line a fifth larger is a heading, the largest tier gets one `#`.
    static func markHeadings(_ attributed: NSAttributedString) -> String {
        let ns = attributed.string as NSString
        guard ns.length > 0 else { return "" }
        var weight: [Double: Int] = [:]
        attributed.enumerateAttribute(.font, in: NSRange(location: 0, length: ns.length)) { value, range, _ in
            if let size = fontSize(value) { weight[(size * 2).rounded() / 2, default: 0] += range.length }
        }
        guard let body = weight.max(by: { $0.value < $1.value })?.key, body > 0 else { return attributed.string }
        var out: [String] = []
        var location = 0
        while location < ns.length {
            let lineRange = ns.lineRange(for: NSRange(location: location, length: 0))
            let line = ns.substring(with: lineRange).trimmingCharacters(in: .whitespacesAndNewlines)
            location = lineRange.location + max(1, lineRange.length)
            if line.isEmpty { out.append(""); continue }
            var sizes: [Double: Int] = [:]
            attributed.enumerateAttribute(.font, in: lineRange) { value, range, _ in
                if let size = fontSize(value) { sizes[(size * 2).rounded() / 2, default: 0] += range.length }
            }
            let lineSize = sizes.max(by: { $0.value < $1.value })?.key ?? body
            let ratio = lineSize / body
            if ratio >= 1.18, line.count <= 120, !line.hasSuffix("."), line.rangeOfCharacter(from: .letters) != nil, !line.hasPrefix("#") {
                out.append((ratio >= 1.6 ? "# " : (ratio >= 1.35 ? "## " : "### ")) + line)
            } else {
                out.append(line)
            }
        }
        return out.joined(separator: "\n")
    }

    private static func fontSize(_ value: Any?) -> Double? {
        #if canImport(AppKit)
        return (value as? NSFont).map { Double($0.pointSize) }
        #else
        return (value as? UIFont).map { Double($0.pointSize) }
        #endif
    }

    /// The page rendered at about 200 dpi and read with Vision, the way a scanned page has to be.
    static func recognize(_ page: PDFPage) async -> String? {
        let bounds = page.bounds(for: .mediaBox)
        guard bounds.width > 0, bounds.height > 0 else { return nil }
        let scale = min(2.8, 2200 / max(bounds.width, bounds.height))
        let size = CGSize(width: bounds.width * scale, height: bounds.height * scale)
        #if canImport(AppKit)
        guard let cg = page.thumbnail(of: size, for: .mediaBox).cgImage(forProposedRect: nil, context: nil, hints: nil) else { return nil }
        #else
        guard let cg = page.thumbnail(of: size, for: .mediaBox).cgImage else { return nil }
        #endif
        return await withCheckedContinuation { cont in
            DispatchQueue.global(qos: .userInitiated).async {
                let req = VNRecognizeTextRequest()
                req.recognitionLevel = .accurate
                req.usesLanguageCorrection = true
                try? VNImageRequestHandler(cgImage: cg).perform([req])
                let lines = req.results?.compactMap { $0.topCandidates(1).first?.string } ?? []
                cont.resume(returning: lines.joined(separator: "\n"))
            }
        }
    }
}
