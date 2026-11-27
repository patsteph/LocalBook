import Foundation

/// RSS 2.0, Atom and RDF feeds read with Foundation's XMLParser: the title, each item's title, link, summary, author
/// and date. Also finds the feed a web page advertises in its head. Pure, so the Collector's reading is tested in CI.
public enum FeedParser {
    public struct Item: Sendable, Hashable, Identifiable {
        public var id: String { link.isEmpty ? title : link }
        public var title: String
        public var link: String
        public var summary: String
        public var author: String
        public var published: Date?
        public init(title: String, link: String, summary: String = "", author: String = "", published: Date? = nil) {
            self.title = title; self.link = link; self.summary = summary; self.author = author; self.published = published
        }
    }
    public struct Feed: Sendable, Hashable {
        public var title: String
        public var link: String
        public var items: [Item]
        public init(title: String, link: String = "", items: [Item]) { self.title = title; self.link = link; self.items = items }
    }

    /// Nil when the data is not a feed at all.
    public static func parse(_ data: Data) -> Feed? {
        let reader = Reader()
        let parser = XMLParser(data: data)
        parser.delegate = reader
        parser.shouldProcessNamespaces = false
        parser.parse()
        guard reader.sawFeed else { return nil }
        return Feed(title: clean(reader.feedTitle), link: reader.feedLink.trimmingCharacters(in: .whitespacesAndNewlines), items: reader.items.filter { !$0.title.isEmpty || !$0.link.isEmpty })
    }

    /// `<link rel="alternate" type="application/rss+xml" href=…>` in a page's head, resolved against the page.
    public static func feedLinks(inHTML html: String, base: URL) -> [URL] {
        var out: [URL] = []
        let pattern = /<link\b[^>]*>/.ignoresCase()
        for m in html.matches(of: pattern) {
            let tag = String(m.output)
            guard tag.range(of: "alternate", options: .caseInsensitive) != nil,
                  tag.range(of: "rss+xml", options: .caseInsensitive) != nil || tag.range(of: "atom+xml", options: .caseInsensitive) != nil,
                  let href = attribute("href", in: tag), let url = URL(string: href, relativeTo: base)?.absoluteURL else { continue }
            if !out.contains(url) { out.append(url) }
        }
        return out
    }

    static func attribute(_ name: String, in tag: String) -> String? {
        guard let r = tag.range(of: name + "=", options: .caseInsensitive) else { return nil }
        var rest = tag[r.upperBound...]
        guard let q = rest.first, q == "\"" || q == "'" else {
            let v = rest.prefix { !$0.isWhitespace && $0 != ">" }
            return v.isEmpty ? nil : String(v)
        }
        rest = rest.dropFirst()
        guard let end = rest.firstIndex(of: q) else { return nil }
        return String(rest[..<end])
    }

    /// Tags stripped, entities resolved, whitespace folded.
    public static func clean(_ s: String) -> String {
        var t = s.replacingOccurrences(of: "<[^>]+>", with: " ", options: .regularExpression)
        for (e, c) in [("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", "\""), ("&#39;", "'"), ("&apos;", "'"), ("&nbsp;", " ")] { t = t.replacingOccurrences(of: e, with: c) }
        t = t.replacingOccurrences(of: "&#(\\d+);", with: "", options: .regularExpression)
        t = t.split(whereSeparator: \.isWhitespace).joined(separator: " ")
        // A tag replaced by a space can leave "Denver ." behind.
        return t.replacingOccurrences(of: " ([.,;:!?)])", with: "$1", options: .regularExpression)
    }

    /// RFC 822 (RSS) and RFC 3339 (Atom) dates.
    public static func date(_ raw: String) -> Date? {
        let s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty else { return nil }
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = iso.date(from: s) { return d }
        iso.formatOptions = [.withInternetDateTime]
        if let d = iso.date(from: s) { return d }
        let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX")
        for fmt in ["EEE, dd MMM yyyy HH:mm:ss Z", "EEE, dd MMM yyyy HH:mm:ss zzz", "dd MMM yyyy HH:mm:ss Z", "EEE, dd MMM yyyy HH:mm Z", "yyyy-MM-dd HH:mm:ss", "yyyy-MM-dd"] {
            f.dateFormat = fmt
            if let d = f.date(from: s) { return d }
        }
        return nil
    }

    private final class Reader: NSObject, XMLParserDelegate {
        var sawFeed = false
        var feedTitle = "", feedLink = ""
        var items: [Item] = []
        private var path: [String] = []
        private var text = ""
        private var current: (title: String, link: String, summary: String, content: String, author: String, date: String)?
        private var inItem: Bool { current != nil }

        func parser(_ parser: XMLParser, didStartElement name: String, namespaceURI: String?, qualifiedName: String?, attributes: [String: String]) {
            let local = name.split(separator: ":").last.map(String.init) ?? name
            path.append(local.lowercased())
            text = ""
            switch local.lowercased() {
            case "rss", "feed", "rdf": sawFeed = true
            case "item", "entry": current = ("", "", "", "", "", "")
            case "link":
                // Atom: <link href="…" rel="alternate">; RSS has the address as text.
                if let href = attributes["href"], attributes["rel"] == nil || attributes["rel"] == "alternate" {
                    if inItem, current?.link.isEmpty == true { current?.link = href } else if !inItem, feedLink.isEmpty { feedLink = href }
                }
            case "enclosure":
                if inItem, current?.link.isEmpty == true, let u = attributes["url"] { current?.link = u }
            default: break
            }
        }

        func parser(_ parser: XMLParser, foundCharacters string: String) { text += string }
        func parser(_ parser: XMLParser, foundCDATA CDATABlock: Data) { text += String(decoding: CDATABlock, as: UTF8.self) }

        func parser(_ parser: XMLParser, didEndElement name: String, namespaceURI: String?, qualifiedName: String?) {
            let local = (name.split(separator: ":").last.map(String.init) ?? name).lowercased()
            let value = text.trimmingCharacters(in: .whitespacesAndNewlines)
            defer { _ = path.popLast(); text = "" }
            if var c = current {
                switch local {
                case "title": if c.title.isEmpty { c.title = value }
                case "link": if c.link.isEmpty, !value.isEmpty { c.link = value }
                case "guid", "id": if c.link.isEmpty, value.hasPrefix("http") { c.link = value }
                case "description", "summary", "subtitle": if c.summary.isEmpty { c.summary = value }
                case "encoded", "content": if c.content.isEmpty { c.content = value }
                case "author", "creator", "name": if c.author.isEmpty, path.contains("author") || local != "name" { c.author = value }
                case "pubdate", "published", "updated", "date", "issued": if c.date.isEmpty { c.date = value }
                case "item", "entry":
                    let summary = FeedParser.clean(c.summary.isEmpty ? c.content : c.summary)
                    items.append(Item(title: FeedParser.clean(c.title), link: c.link.trimmingCharacters(in: .whitespacesAndNewlines), summary: String(summary.prefix(2_000)),
                                      author: FeedParser.clean(c.author), published: FeedParser.date(c.date)))
                    current = nil
                    return
                default: break
                }
                current = c
            } else {
                switch local {
                case "title": if feedTitle.isEmpty, path.count <= 3 { feedTitle = value }
                case "link": if feedLink.isEmpty, !value.isEmpty, path.count <= 3 { feedLink = value }
                default: break
                }
            }
        }
    }
}
