import Foundation
import LocalBookCore
import ZIPFoundation

// MARK: - EPUB

/// An e-book as the old app read it (`_extract_from_epub`): `Title:` and `Author:` from the package metadata, then
/// every spine document in reading order with headings as Markdown and each text node on its own line, chapters
/// separated by blank lines. DRM-protected books are refused, as there.
enum EPUBReader {
    static func read(_ url: URL) throws -> String {
        let archive = try Archive(url: url, accessMode: .read)
        if archive["META-INF/encryption.xml"] != nil { throw LabError.model("This EPUB is DRM-protected and cannot be read. Please use a DRM-free copy.") }
        guard let containerXML = try OfficePackage.part(archive, "META-INF/container.xml") else { throw LabError.model("Not a valid EPUB — missing META-INF/container.xml.") }
        let container = AttributeFinder(element: "rootfile", attribute: "full-path"); container.parse(containerXML)
        guard let opfPath = container.values.first?.removingPercentEncoding, let opfData = try OfficePackage.part(archive, opfPath) else {
            throw LabError.model("Not a valid EPUB — container.xml names no package document.")
        }
        let opf = OPFParser(); opf.parse(opfData)
        let opfDir = (opfPath as NSString).deletingLastPathComponent

        var parts: [String] = []
        if !opf.title.isEmpty { parts.append("Title: \(opf.title)") }
        if !opf.author.isEmpty { parts.append("Author: \(opf.author)") }
        if !parts.isEmpty { parts.append("") }

        var chapters = 0
        for idref in opf.spine {
            guard let item = opf.manifest[idref] else { continue }
            if !item.media.isEmpty, item.media != "application/xhtml+xml", item.media != "text/html" { continue }
            let clean = (item.href.split(separator: "#").first.map(String.init) ?? item.href).removingPercentEncoding ?? item.href
            let path = opfDir.isEmpty ? clean : OfficePackage.normalize(opfDir + "/" + clean)
            guard let data = try OfficePackage.part(archive, path) else { continue }
            let text = XHTMLText.lines(data).joined(separator: "\n")
            if !text.isEmpty { parts.append(text); chapters += 1 }
        }
        guard chapters > 0 else { throw LabError.model("EPUB contained no readable text.") }
        return parts.joined(separator: "\n\n")
    }

    final class AttributeFinder: NSObject, XMLParserDelegate {
        let element: String, attribute: String
        var values: [String] = []
        init(element: String, attribute: String) { self.element = element; self.attribute = attribute; super.init() }
        func parse(_ data: Data) { let x = XMLParser(data: data); x.delegate = self; x.shouldProcessNamespaces = true; x.parse() }
        func parser(_ parser: XMLParser, didStartElement name: String, namespaceURI: String?, qualifiedName: String?, attributes: [String: String]) {
            if name == element, let v = attributes[attribute] { values.append(v) }
        }
    }

    final class OPFParser: NSObject, XMLParserDelegate {
        var title = "", author = ""
        var manifest: [String: (href: String, media: String)] = [:]
        var spine: [String] = []
        private var current = "", inMetadata = false, text = ""
        func parse(_ data: Data) { let x = XMLParser(data: data); x.delegate = self; x.shouldProcessNamespaces = true; x.parse() }
        func parser(_ parser: XMLParser, didStartElement name: String, namespaceURI: String?, qualifiedName: String?, attributes: [String: String]) {
            current = name; text = ""
            switch name {
            case "metadata": inMetadata = true
            case "item": if let id = attributes["id"], let href = attributes["href"] { manifest[id] = (href, (attributes["media-type"] ?? "").lowercased()) }
            case "itemref": if let idref = attributes["idref"] { spine.append(idref) }
            default: break
            }
        }
        func parser(_ parser: XMLParser, foundCharacters s: String) { text += s }
        func parser(_ parser: XMLParser, didEndElement name: String, namespaceURI: String?, qualifiedName: String?) {
            let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
            if inMetadata, name == "title", title.isEmpty, !value.isEmpty { title = value }
            if inMetadata, name == "creator", author.isEmpty, !value.isEmpty { author = value }
            if name == "metadata" { inMetadata = false }
            text = ""
        }
    }
}

/// XHTML (an EPUB chapter) as BeautifulSoup's `get_text("\n", strip=True)` gave it after headings were turned into
/// Markdown: scripts and styles dropped, a heading as `## text` on its own line, every other text node stripped on
/// its own line. Named HTML entities are mapped to characters first, since the XML parser knows only the five.
enum XHTMLText {
    static func lines(_ data: Data) -> [String] {
        var source = String(decoding: data, as: UTF8.self)
        if let r = source.range(of: "<!DOCTYPE[^>]*>", options: [.regularExpression, .caseInsensitive]) { source.removeSubrange(r) }
        source = HTMLEntities.numeric(source)
        let p = Walker(); p.parse(Data(source.utf8))
        if p.failed || p.lines.isEmpty {
            // Not well-formed XML: take the text between the tags.
            let stripped = source.replacingOccurrences(of: "<script[\\s\\S]*?</script>|<style[\\s\\S]*?</style>", with: "", options: [.regularExpression, .caseInsensitive])
                .replacingOccurrences(of: "<[^>]+>", with: "\n", options: .regularExpression)
            return stripped.split(separator: "\n").map { HTMLEntities.decode(String($0)).trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty }
        }
        return p.lines
    }

    final class Walker: NSObject, XMLParserDelegate {
        var lines: [String] = []
        var failed = false
        private var skip = 0, headingDepth = 0, heading: [String] = [], text = ""
        func parse(_ data: Data) { let x = XMLParser(data: data); x.delegate = self; x.shouldProcessNamespaces = true; x.parse() }

        private func flush() {
            let t = text.trimmingCharacters(in: .whitespacesAndNewlines); text = ""
            guard !t.isEmpty else { return }
            if headingDepth > 0 { heading.append(t) } else { lines.append(t) }
        }
        func parser(_ parser: XMLParser, didStartElement name: String, namespaceURI: String?, qualifiedName: String?, attributes: [String: String]) {
            flush()
            let tag = name.lowercased()
            if tag == "script" || tag == "style" { skip += 1 }
            if headingDepth > 0 { headingDepth += 1 }
            else if tag.count == 2, tag.first == "h", let level = Int(String(tag.last!)), (1...6).contains(level) { headingDepth = 1; heading = []; headingLevel = level }
        }
        private var headingLevel = 1
        func parser(_ parser: XMLParser, foundCharacters s: String) { if skip == 0 { text += s } }
        func parser(_ parser: XMLParser, didEndElement name: String, namespaceURI: String?, qualifiedName: String?) {
            flush()
            let tag = name.lowercased()
            if tag == "script" || tag == "style" { skip = max(0, skip - 1) }
            if headingDepth > 0 {
                headingDepth -= 1
                if headingDepth == 0 {
                    let h = heading.joined(separator: " ")
                    if !h.isEmpty { lines.append(String(repeating: "#", count: headingLevel) + " " + h) }
                }
            }
        }
        func parser(_ parser: XMLParser, parseErrorOccurred parseError: Error) { failed = true }
    }
}

extension HTMLEntities {
    /// Named entities beyond XML's five become numeric references the XML parser accepts.
    static let names: [String: Int] = [
        "nbsp": 160, "iexcl": 161, "cent": 162, "pound": 163, "curren": 164, "yen": 165, "brvbar": 166, "sect": 167, "uml": 168, "copy": 169,
        "ordf": 170, "laquo": 171, "not": 172, "shy": 173, "reg": 174, "macr": 175, "deg": 176, "plusmn": 177, "sup2": 178, "sup3": 179,
        "acute": 180, "micro": 181, "para": 182, "middot": 183, "cedil": 184, "sup1": 185, "ordm": 186, "raquo": 187, "frac14": 188, "frac12": 189,
        "frac34": 190, "iquest": 191, "times": 215, "divide": 247, "ndash": 8211, "mdash": 8212, "lsquo": 8216, "rsquo": 8217, "sbquo": 8218,
        "ldquo": 8220, "rdquo": 8221, "bdquo": 8222, "dagger": 8224, "Dagger": 8225, "bull": 8226, "hellip": 8230, "permil": 8240, "prime": 8242,
        "lsaquo": 8249, "rsaquo": 8250, "euro": 8364, "trade": 8482, "ensp": 8194, "emsp": 8195, "thinsp": 8201, "zwnj": 8204, "zwj": 8205,
        "lrm": 8206, "rlm": 8207, "larr": 8592, "rarr": 8594, "uarr": 8593, "darr": 8595, "hearts": 9829, "minus": 8722, "infin": 8734, "ne": 8800,
        "le": 8804, "ge": 8805, "Agrave": 192, "Aacute": 193, "Acirc": 194, "Atilde": 195, "Auml": 196, "Aring": 197, "AElig": 198, "Ccedil": 199,
        "Egrave": 200, "Eacute": 201, "Ecirc": 202, "Euml": 203, "Igrave": 204, "Iacute": 205, "Icirc": 206, "Iuml": 207, "ETH": 208, "Ntilde": 209,
        "Ograve": 210, "Oacute": 211, "Ocirc": 212, "Otilde": 213, "Ouml": 214, "Oslash": 216, "Ugrave": 217, "Uacute": 218, "Ucirc": 219, "Uuml": 220,
        "Yacute": 221, "THORN": 222, "szlig": 223, "agrave": 224, "aacute": 225, "acirc": 226, "atilde": 227, "auml": 228, "aring": 229, "aelig": 230,
        "ccedil": 231, "egrave": 232, "eacute": 233, "ecirc": 234, "euml": 235, "igrave": 236, "iacute": 237, "icirc": 238, "iuml": 239, "eth": 240,
        "ntilde": 241, "ograve": 242, "oacute": 243, "ocirc": 244, "otilde": 245, "ouml": 246, "oslash": 248, "ugrave": 249, "uacute": 250, "ucirc": 251,
        "uuml": 252, "yacute": 253, "thorn": 254, "yuml": 255, "OElig": 338, "oelig": 339, "Scaron": 352, "scaron": 353, "Yuml": 376, "fnof": 402,
        "circ": 710, "tilde": 732,
    ]

    static func numeric(_ s: String) -> String {
        guard s.contains("&") else { return s }
        var out = ""
        var i = s.startIndex
        while i < s.endIndex {
            if s[i] == "&", let semi = s[i...].firstIndex(of: ";"), s.distance(from: i, to: semi) <= 9 {
                let body = String(s[s.index(after: i)..<semi])
                if let code = names[body] { out += "&#\(code);"; i = s.index(after: semi); continue }
            }
            out.append(s[i]); i = s.index(after: i)
        }
        return out
    }
}

// MARK: - ODS

/// An OpenDocument spreadsheet as pandas (engine "odf") gave it to the old app's `_extract_from_ods`: each non-empty
/// sheet under `--- Sheet: name ---`, then its sentences.
enum ODSReader {
    static func read(_ url: URL) throws -> String {
        let archive = try Archive(url: url, accessMode: .read)
        guard let content = try OfficePackage.part(archive, "content.xml") else { throw LabError.model("\(url.lastPathComponent) has no content inside") }
        let p = ContentParser(); p.parse(content)
        var parts: [String] = []
        for sheet in p.sheets {
            let table = TextTable.fromRows(sheet.rows)
            guard !table.rows.isEmpty || !table.columns.isEmpty, !sheet.rows.isEmpty else { continue }
            parts.append("--- Sheet: \(sheet.name) ---")
            parts.append(TabularText.sentences(table, sheet: sheet.name))
            parts.append("\n\n")
        }
        guard !parts.isEmpty else { throw LabError.model("\(url.lastPathComponent) has no sheets with data") }
        return parts.joined(separator: "\n")
    }

    final class ContentParser: NSObject, XMLParserDelegate {
        var sheets: [(name: String, rows: [[String]])] = []
        private var rows: [[String]] = [], row: [String] = [], cell = "", cellText: [String] = [], paragraph = ""
        private var inCell = false, inP = false, cellRepeat = 1, rowRepeat = 1, valueType = "", value: String?
        private var name = ""
        func parse(_ data: Data) { let x = XMLParser(data: data); x.delegate = self; x.shouldProcessNamespaces = true; x.parse() }

        func parser(_ parser: XMLParser, didStartElement element: String, namespaceURI: String?, qualifiedName: String?, attributes: [String: String]) {
            switch element {
            case "table": name = attr(attributes, "table:name") ?? "Sheet\(sheets.count + 1)"; rows = []
            case "table-row": row = []; rowRepeat = min(Int(attr(attributes, "table:number-rows-repeated") ?? "1") ?? 1, 1000)
            case "table-cell", "covered-table-cell":
                inCell = true; cellText = []
                cellRepeat = min(Int(attr(attributes, "table:number-columns-repeated") ?? "1") ?? 1, 1024)
                valueType = attr(attributes, "office:value-type") ?? ""
                value = attr(attributes, "office:value") ?? attr(attributes, "office:date-value") ?? attr(attributes, "office:time-value") ?? attr(attributes, "office:boolean-value")
            case "p" where inCell: inP = true; paragraph = ""
            default: break
            }
        }
        func parser(_ parser: XMLParser, foundCharacters s: String) { if inP { paragraph += s } }
        func parser(_ parser: XMLParser, didEndElement element: String, namespaceURI: String?, qualifiedName: String?) {
            switch element {
            case "p" where inCell: inP = false; cellText.append(paragraph)
            case "table-cell", "covered-table-cell":
                inCell = false
                let text = render()
                for _ in 0..<cellRepeat { row.append(text) }
            case "table-row":
                while let last = row.last, last.isEmpty { row.removeLast() }
                for _ in 0..<rowRepeat { rows.append(row) }
            case "table":
                while let last = rows.last, last.isEmpty { rows.removeLast() }
                sheets.append((name, rows))
            default: break
            }
        }

        /// The parser reports attributes with their prefixes; accept either spelling.
        private func attr(_ attributes: [String: String], _ qualified: String) -> String? {
            attributes[qualified] ?? attributes[String(qualified.split(separator: ":").last ?? "")]
        }

        /// pandas' odf reader: numbers from the value attribute, dates as Timestamps, booleans as True/False, else the text.
        private func render() -> String {
            switch valueType {
            case "float", "percentage", "currency":
                if let v = value, let d = Double(v) { return d == d.rounded() && abs(d) < 1e15 ? String(Int64(d)) : "\(d)" }
            case "boolean":
                if let v = value { return v == "true" ? "True" : "False" }
            case "date":
                if let v = value {
                    // 2024-03-05 or 2024-03-05T13:45:00 → pandas' Timestamp text
                    let parts = v.split(separator: "T", maxSplits: 1).map(String.init)
                    let time = parts.count > 1 ? String(parts[1].prefix(8)) : "00:00:00"
                    return parts[0] + " " + (time.count == 8 ? time : "00:00:00")
                }
            default: break
            }
            return cellText.joined(separator: "\n")
        }
    }
}

// MARK: - SVG

/// The words in an SVG (`_extract_from_svg`): the text of `text`, `tspan`, `title` and `desc` elements, each run on
/// its own line, in document order.
enum SVGText {
    static func read(_ data: Data) -> String {
        let p = Parser(); p.parse(data)
        return p.parts.isEmpty ? "[SVG image with no extractable text: \(data.count) bytes]" : p.parts.joined(separator: "\n")
    }

    final class Parser: NSObject, XMLParserDelegate {
        var parts: [String] = []
        private var stack: [String] = [], text = ""
        static let wanted: Set<String> = ["text", "tspan", "title", "desc"]
        func parse(_ data: Data) { let x = XMLParser(data: data); x.delegate = self; x.shouldProcessNamespaces = true; x.parse() }
        private func flush() {
            let t = text.trimmingCharacters(in: .whitespacesAndNewlines); text = ""
            if !t.isEmpty, let top = stack.last, Self.wanted.contains(top) { parts.append(t) }
        }
        func parser(_ parser: XMLParser, didStartElement name: String, namespaceURI: String?, qualifiedName: String?, attributes: [String: String]) { flush(); stack.append(name) }
        func parser(_ parser: XMLParser, foundCharacters s: String) { text += s }
        func parser(_ parser: XMLParser, didEndElement name: String, namespaceURI: String?, qualifiedName: String?) { flush(); _ = stack.popLast() }
    }
}
