import CoreGraphics
import Foundation
import LocalBookCore
import PDFKit
import Vision

/// Ruled tables on a PDF page, found by Vision's document reader and written as the Markdown tables pymupdf4llm gave
/// the old app's chunker. The text layer under a table is removed from the page's prose, so a cell is read once, in
/// its row. A page is given some seconds (the first page pays for Vision's model to load); one that takes longer keeps
/// its prose and no tables, and the trace says so.
enum PDFTables {
    struct Table: Sendable {
        /// Where the table sits on the page, in PDF page points.
        let rect: CGRect
        let markdown: String
    }

    static let pageTimeout: Duration = .seconds(15)

    /// Every table Vision finds on `page`, or none when it finds none or runs out of time.
    static func find(on page: PDFPage) async -> [Table] {
        let bounds = page.bounds(for: .mediaBox)
        guard bounds.width > 0, bounds.height > 0 else { return [] }
        let scale = min(2.8, 2200 / max(bounds.width, bounds.height))
        let size = CGSize(width: bounds.width * scale, height: bounds.height * scale)
        #if canImport(AppKit)
        guard let cg = page.thumbnail(of: size, for: .mediaBox).cgImage(forProposedRect: nil, context: nil, hints: nil) else { return [] }
        #else
        guard let cg = page.thumbnail(of: size, for: .mediaBox).cgImage else { return [] }
        #endif
        let work = Task.detached(priority: .userInitiated) { () -> [Table] in
            let request = RecognizeDocumentsRequest()
            guard let observations = try? await request.perform(on: cg) else { return [] }
            var out: [Table] = []
            for observation in observations {
                for table in observation.document.tables {
                    let rows = table.rows.map { row in row.map { cell in clean(cell.content.text.transcript) } }
                    guard let md = markdown(rows) else { continue }
                    // Vision's region is normalized over the image with the origin at the lower left, as the page's is.
                    let box = table.boundingRegion.boundingBox
                    let rect = CGRect(x: bounds.minX + box.origin.x * bounds.width,
                                      y: bounds.minY + box.origin.y * bounds.height,
                                      width: box.width * bounds.width, height: box.height * bounds.height)
                    out.append(Table(rect: rect, markdown: md))
                }
            }
            return out
        }
        let started = ContinuousClock.now
        let clock = Task { try await Task.sleep(for: pageTimeout); work.cancel() }
        let tables = await work.value
        let timedOut = work.isCancelled
        clock.cancel()
        let took = started.duration(to: .now)
        let secs = Double(took.components.seconds) + Double(took.components.attoseconds) / 1e18
        SoakTrace.log("pdf tables: page \(page.label ?? "?"): \(tables.count) table(s) in \(String(format: "%.1f", secs)) s" + (timedOut ? " (gave up at the timeout; the page keeps its prose)" : ""))
        return tables
    }

    /// pymupdf4llm's table: a header row, the rule, the rows; cells with pipes or newlines flattened. Nothing for a
    /// table with one row or one column, which the old library did not write as a table either.
    static func markdown(_ rows: [[String]]) -> String? {
        let width = rows.map(\.count).max() ?? 0
        guard rows.count >= 2, width >= 2 else { return nil }
        let padded = rows.map { $0 + Array(repeating: "", count: width - $0.count) }
        var lines = ["|" + padded[0].joined(separator: "|") + "|", "|" + Array(repeating: "---", count: width).joined(separator: "|") + "|"]
        for row in padded.dropFirst() { lines.append("|" + row.joined(separator: "|") + "|") }
        return lines.joined(separator: "\n")
    }

    static func clean(_ s: String) -> String {
        s.replacingOccurrences(of: "|", with: "\\|").replacingOccurrences(of: "\n", with: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// `text` (the page's prose, one line per line) without the lines whose characters sit inside a table, then the
    /// tables, each in its own paragraph.
    static func merge(text: String, page: PDFPage, tables: [Table]) -> String {
        guard !tables.isEmpty else { return text }
        var covered: Set<String> = []
        for t in tables {
            // A little slack around the ruled box, since the text layer's glyph boxes overhang rules.
            let slack = t.rect.insetBy(dx: -2, dy: -2)
            if let selection = page.selection(for: slack), let inside = selection.string {
                for line in inside.components(separatedBy: .newlines) {
                    let key = line.trimmingCharacters(in: .whitespacesAndNewlines)
                    if !key.isEmpty { covered.insert(key) }
                }
            }
        }
        var kept: [String] = []
        for line in text.components(separatedBy: .newlines) {
            let plain = line.trimmingCharacters(in: .whitespacesAndNewlines).drop { $0 == "#" }.trimmingCharacters(in: .whitespaces)
            if covered.contains(String(plain)) { continue }
            kept.append(line)
        }
        var out = kept.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
        for t in tables { out += (out.isEmpty ? "" : "\n\n") + t.markdown }
        return out
    }
}
