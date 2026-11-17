import Foundation
import LocalBookCore
import ZIPFoundation

/// Office Open XML readers for the formats the old app read with openpyxl and python-pptx: the zip is opened with
/// ZIPFoundation and the parts are parsed with Foundation's XMLParser. No third-party Office library.
enum OfficePackage {
    /// One part of the package, by its path inside the zip.
    static func part(_ archive: Archive, _ path: String) throws -> Data? {
        guard let entry = archive[path] else { return nil }
        var data = Data()
        _ = try archive.extract(entry) { data.append($0) }
        return data
    }

    /// `_rels/x.rels`: relationship id → target, resolved against `base`.
    static func relationships(_ archive: Archive, relsPath: String, base: String) throws -> [String: String] {
        guard let data = try part(archive, relsPath) else { return [:] }
        let p = RelsParser(); p.parse(data)
        var out: [String: String] = [:]
        for (id, target) in p.targets {
            out[id] = target.hasPrefix("/") ? String(target.dropFirst()) : normalize(base + "/" + target)
        }
        return out
    }

    static func normalize(_ path: String) -> String {
        var parts: [String] = []
        for p in path.split(separator: "/") {
            if p == ".." { _ = parts.popLast() } else if p != "." { parts.append(String(p)) }
        }
        return parts.joined(separator: "/")
    }

    final class RelsParser: NSObject, XMLParserDelegate {
        var targets: [(String, String)] = []
        func parse(_ data: Data) { let x = XMLParser(data: data); x.delegate = self; x.parse() }
        func parser(_ parser: XMLParser, didStartElement name: String, namespaceURI: String?, qualifiedName: String?, attributes: [String: String]) {
            if name == "Relationship", let id = attributes["Id"], let t = attributes["Target"] { targets.append((id, t)) }
        }
    }
}

// MARK: - .xlsx

/// A workbook as pandas' `read_excel(sheet_name=None)` gave it: every sheet in order, the first row as the header,
/// cells as text (shared strings resolved, booleans as True/False, dated cells as `yyyy-MM-dd HH:mm:ss`).
enum XLSXReader {
    static func read(_ url: URL) throws -> [(name: String, table: Table)] {
        let archive = try Archive(url: url, accessMode: .read)
        guard let wb = try OfficePackage.part(archive, "xl/workbook.xml") else { throw LabError.model("\(url.lastPathComponent) has no workbook inside") }
        let sheets = WorkbookParser(); sheets.parse(wb)
        let rels = try OfficePackage.relationships(archive, relsPath: "xl/_rels/workbook.xml.rels", base: "xl")
        var shared: [String] = []
        if let ss = try OfficePackage.part(archive, "xl/sharedStrings.xml") { let p = SharedStringsParser(); p.parse(ss); shared = p.strings }
        var dateStyles: Set<Int> = []
        if let st = try OfficePackage.part(archive, "xl/styles.xml") { let p = StylesParser(); p.parse(st); dateStyles = p.dateStyles }
        var out: [(String, Table)] = []
        for (name, rid) in sheets.sheets {
            guard let path = rels[rid], let data = try OfficePackage.part(archive, path) else { continue }
            let p = SheetParser(shared: shared, dateStyles: dateStyles); p.parse(data)
            out.append((name, Table.fromRows(p.rows)))
        }
        return out.map { (name: $0.0, table: $0.1) }
    }

    final class WorkbookParser: NSObject, XMLParserDelegate {
        var sheets: [(String, String)] = []
        func parse(_ data: Data) { let x = XMLParser(data: data); x.delegate = self; x.shouldProcessNamespaces = true; x.parse() }
        func parser(_ parser: XMLParser, didStartElement name: String, namespaceURI: String?, qualifiedName: String?, attributes: [String: String]) {
            if name == "sheet", let n = attributes["name"], let rid = attributes["r:id"] ?? attributes["id"] { sheets.append((n, rid)) }
        }
    }

    final class SharedStringsParser: NSObject, XMLParserDelegate {
        var strings: [String] = []
        private var current = "", inItem = false, inText = false
        func parse(_ data: Data) { let x = XMLParser(data: data); x.delegate = self; x.shouldProcessNamespaces = true; x.parse() }
        func parser(_ parser: XMLParser, didStartElement name: String, namespaceURI: String?, qualifiedName: String?, attributes: [String: String]) {
            if name == "si" { inItem = true; current = "" } else if name == "t" && inItem { inText = true }
        }
        func parser(_ parser: XMLParser, foundCharacters s: String) { if inText { current += s } }
        func parser(_ parser: XMLParser, didEndElement name: String, namespaceURI: String?, qualifiedName: String?) {
            if name == "t" { inText = false } else if name == "si" { strings.append(current); inItem = false }
        }
    }

    /// Which cell styles (`cellXfs` indices) show dates: the built-in date formats and custom ones with day/month/year.
    final class StylesParser: NSObject, XMLParserDelegate {
        var dateStyles: Set<Int> = []
        private var customDateFormats: Set<Int> = []
        private var inCellXfs = false, xfIndex = 0
        static let builtInDates: Set<Int> = Set(14...22).union(45...47)
        func parse(_ data: Data) { let x = XMLParser(data: data); x.delegate = self; x.shouldProcessNamespaces = true; x.parse() }
        func parser(_ parser: XMLParser, didStartElement name: String, namespaceURI: String?, qualifiedName: String?, attributes: [String: String]) {
            switch name {
            case "numFmt":
                if let id = Int(attributes["numFmtId"] ?? ""), let code = attributes["formatCode"]?.lowercased() {
                    let stripped = code.replacingOccurrences(of: "\\[[^\\]]*\\]", with: "", options: .regularExpression).replacingOccurrences(of: "\"[^\"]*\"", with: "", options: .regularExpression)
                    if stripped.contains("y") || stripped.contains("d") || (stripped.contains("m") && !stripped.contains("0")) { customDateFormats.insert(id) }
                }
            case "cellXfs": inCellXfs = true; xfIndex = 0
            case "xf" where inCellXfs:
                if let id = Int(attributes["numFmtId"] ?? ""), Self.builtInDates.contains(id) || customDateFormats.contains(id) { dateStyles.insert(xfIndex) }
                xfIndex += 1
            default: break
            }
        }
        func parser(_ parser: XMLParser, didEndElement name: String, namespaceURI: String?, qualifiedName: String?) { if name == "cellXfs" { inCellXfs = false } }
    }

    final class SheetParser: NSObject, XMLParserDelegate {
        let shared: [String]; let dateStyles: Set<Int>
        var rows: [[String]] = []
        private var row: [String] = []
        private var column = 0, cellType = "", cellStyle = 0, value = "", inValue = false, inInline = false
        init(shared: [String], dateStyles: Set<Int>) { self.shared = shared; self.dateStyles = dateStyles }
        func parse(_ data: Data) { let x = XMLParser(data: data); x.delegate = self; x.shouldProcessNamespaces = true; x.parse() }

        static func columnIndex(_ ref: String) -> Int? {
            var n = 0, any = false
            for ch in ref.uppercased() {
                guard let a = ch.asciiValue, a >= 65, a <= 90 else { break }
                n = n * 26 + Int(a - 64); any = true
            }
            return any ? n - 1 : nil
        }

        func parser(_ parser: XMLParser, didStartElement name: String, namespaceURI: String?, qualifiedName: String?, attributes: [String: String]) {
            switch name {
            case "row": row = []
            case "c":
                column = Self.columnIndex(attributes["r"] ?? "") ?? row.count
                cellType = attributes["t"] ?? ""; cellStyle = Int(attributes["s"] ?? "") ?? 0; value = ""
            case "v": inValue = true
            case "is": inInline = true
            case "t" where inInline: inValue = true
            default: break
            }
        }
        func parser(_ parser: XMLParser, foundCharacters s: String) { if inValue { value += s } }
        func parser(_ parser: XMLParser, didEndElement name: String, namespaceURI: String?, qualifiedName: String?) {
            switch name {
            case "v", "t": inValue = false
            case "is": inInline = false
            case "c":
                while row.count < column { row.append("") }
                let text: String
                switch cellType {
                case "s": text = Int(value).flatMap { $0 < shared.count ? shared[$0] : nil } ?? ""
                case "b": text = value == "1" ? "True" : "False"
                case "inlineStr", "str", "e": text = value
                default:
                    if dateStyles.contains(cellStyle), let serial = Double(value) { text = Self.date(serial) }
                    else { text = value }
                }
                if row.count == column { row.append(text) } else { row[column] = text }
            case "row": rows.append(row)
            default: break
            }
        }

        /// An Excel serial day as pandas prints a Timestamp.
        static func date(_ serial: Double) -> String {
            let seconds = (serial - 25569) * 86400                      // 1899-12-30 epoch to 1970-01-01
            let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.timeZone = TimeZone(identifier: "UTC"); f.dateFormat = "yyyy-MM-dd HH:mm:ss"
            return f.string(from: Date(timeIntervalSince1970: seconds.rounded()))
        }
    }
}

// MARK: - .pptx

/// A deck as python-pptx read it for the old app: `=== Slide N ===`, each shape's text (paragraphs on their own
/// lines), tables as sentences, then `Speaker Notes:`, with a blank line between slides.
enum PPTXReader {
    static func read(_ url: URL) throws -> String {
        let archive = try Archive(url: url, accessMode: .read)
        guard let pres = try OfficePackage.part(archive, "ppt/presentation.xml") else { throw LabError.model("\(url.lastPathComponent) has no presentation inside") }
        let order = SlideListParser(); order.parse(pres)
        let rels = try OfficePackage.relationships(archive, relsPath: "ppt/_rels/presentation.xml.rels", base: "ppt")
        var parts: [String] = []
        var number = 0
        for rid in order.slideIds {
            guard let path = rels[rid], let data = try OfficePackage.part(archive, path) else { continue }
            number += 1
            var slide = ["=== Slide \(number) ==="]
            let p = SlideParser(); p.parse(data)
            for shape in p.shapes {
                switch shape {
                case .text(let t): if !t.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { slide.append(t) }
                case .table(let rows):
                    guard !rows.isEmpty else { continue }
                    let table = rows.count > 1 ? Table.fromRows(rows) : Table(columns: rows[0].indices.map { String($0) }, rows: rows)
                    slide.append(TabularText.sentences(table, sheet: "Slide Table"))
                }
            }
            let file = (path as NSString).lastPathComponent
            let dir = (path as NSString).deletingLastPathComponent
            let slideRels = try OfficePackage.relationships(archive, relsPath: "\(dir)/_rels/\(file).rels", base: dir)
            if let notesPath = slideRels.values.first(where: { $0.contains("notesSlides/") }), let notes = try OfficePackage.part(archive, notesPath) {
                let n = SlideParser(); n.parse(notes)
                let text = n.shapes.compactMap { if case .text(let t) = $0 { return t } else { return nil } }
                    .joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
                // The notes placeholder also carries the slide number; python-pptx reads the body placeholder only.
                let body = text.split(separator: "\n").filter { Int($0.trimmingCharacters(in: .whitespaces)) == nil }.joined(separator: "\n")
                if !body.isEmpty { slide.append("Speaker Notes: \(body)") }
            }
            parts.append(contentsOf: slide)
            parts.append("")
        }
        return parts.joined(separator: "\n")
    }

    final class SlideListParser: NSObject, XMLParserDelegate {
        var slideIds: [String] = []
        func parse(_ data: Data) { let x = XMLParser(data: data); x.delegate = self; x.shouldProcessNamespaces = true; x.parse() }
        func parser(_ parser: XMLParser, didStartElement name: String, namespaceURI: String?, qualifiedName: String?, attributes: [String: String]) {
            if name == "sldId", let rid = attributes["r:id"] ?? attributes["id"] { slideIds.append(rid) }
        }
    }

    /// Shapes in slide order: a text frame's paragraphs, or a table's cells. (`shouldProcessNamespaces` strips the
    /// `a:` / `p:` prefixes, so names are bare.)
    final class SlideParser: NSObject, XMLParserDelegate {
        enum Shape { case text(String); case table([[String]]) }
        var shapes: [Shape] = []
        private var paragraphs: [String] = []
        private var paragraph = ""
        private var inText = false, inShape = false, inTable = false, inCell = false
        private var tableRows: [[String]] = [], tableRow: [String] = [], cellParagraphs: [String] = []

        func parse(_ data: Data) { let x = XMLParser(data: data); x.delegate = self; x.shouldProcessNamespaces = true; x.parse() }

        func parser(_ parser: XMLParser, didStartElement name: String, namespaceURI: String?, qualifiedName: String?, attributes: [String: String]) {
            switch name {
            case "sp": inShape = true; paragraphs = []
            case "tbl": inTable = true; tableRows = []
            case "tr" where inTable: tableRow = []
            case "tc" where inTable: inCell = true; cellParagraphs = []
            case "p" where inShape || inCell: paragraph = ""
            case "t": inText = true
            case "br": paragraph += "\u{0B}"
            default: break
            }
        }
        func parser(_ parser: XMLParser, foundCharacters s: String) { if inText { paragraph += s } }
        func parser(_ parser: XMLParser, didEndElement name: String, namespaceURI: String?, qualifiedName: String?) {
            switch name {
            case "t": inText = false
            case "p" where inCell: cellParagraphs.append(paragraph)
            case "p" where inShape: paragraphs.append(paragraph)
            case "tc" where inTable: tableRow.append(cellParagraphs.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)); inCell = false
            case "tr" where inTable: tableRows.append(tableRow)
            case "tbl": shapes.append(.table(tableRows)); inTable = false
            case "sp": shapes.append(.text(paragraphs.joined(separator: "\n"))); inShape = false
            default: break
            }
        }
    }
}
