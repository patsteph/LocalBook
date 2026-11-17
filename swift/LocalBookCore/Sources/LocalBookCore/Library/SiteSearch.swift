import Foundation

/// Searching the research sites the old app's `site_search.py` knew, without a key: arXiv, Wikipedia, Hacker News,
/// GitHub, Reddit, Stack Overflow, Semantic Scholar, OpenAlex and PubMed. This is the pure half: the request for each
/// site and the reading of its reply, so Swift Testing can hold the parsers still on saved replies. YouTube and the
/// Brave fallback needed API keys there and wait for Settings › Keys.
public enum SiteSearch {
    public struct Result: Sendable, Identifiable, Hashable {
        public var id: String { url }
        public let title: String
        public let url: String
        public let snippet: String
        public let site: Site
        public var published: String? = nil
        public var author: String? = nil
    }

    public enum Site: String, CaseIterable, Identifiable, Sendable {
        case arxiv, wikipedia, hackerNews, github, reddit, stackOverflow, semanticScholar, openAlex, pubmed
        public var id: String { rawValue }
        public var domain: String {
            switch self {
            case .arxiv: "arxiv.org"; case .wikipedia: "wikipedia.org"; case .hackerNews: "news.ycombinator.com"; case .github: "github.com"
            case .reddit: "reddit.com"; case .stackOverflow: "stackoverflow.com"; case .semanticScholar: "semanticscholar.org"
            case .openAlex: "openalex.org"; case .pubmed: "pubmed.ncbi.nlm.nih.gov"
            }
        }
        public var label: String {
            switch self {
            case .arxiv: "arXiv"; case .wikipedia: "Wikipedia"; case .hackerNews: "Hacker News"; case .github: "GitHub"; case .reddit: "Reddit"
            case .stackOverflow: "Stack Overflow"; case .semanticScholar: "Semantic Scholar"; case .openAlex: "OpenAlex"; case .pubmed: "PubMed"
            }
        }
    }

    public enum TimeRange: String, CaseIterable, Identifiable, Sendable {
        case all, day = "24h", week = "7d", twoWeeks = "14d", month = "30d", quarter = "90d", year = "1y"
        public var id: String { rawValue }
        public var label: String {
            switch self {
            case .all: "Any time"; case .day: "Past day"; case .week: "Past week"; case .twoWeeks: "Past two weeks"
            case .month: "Past month"; case .quarter: "Past three months"; case .year: "Past year"
            }
        }
        public var days: Int? {
            switch self { case .all: nil; case .day: 1; case .week: 7; case .twoWeeks: 14; case .month: 30; case .quarter: 90; case .year: 365 }
        }
        /// The earliest date the range admits, or nil for all time.
        public func since(_ now: Date = Date()) -> Date? { days.map { now.addingTimeInterval(-Double($0) * 86400) } }
    }

    // MARK: requests

    static func url(_ base: String, _ items: [(String, String)]) -> URL {
        var c = URLComponents(string: base)!
        c.queryItems = items.map { URLQueryItem(name: $0.0, value: $0.1) }
        return c.url!
    }

    static func iso(_ date: Date) -> String {
        let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.timeZone = TimeZone(identifier: "UTC"); f.dateFormat = "yyyy-MM-dd"
        return f.string(from: date)
    }

    /// The old handlers' requests, one site at a time. PubMed needs two calls: see `pubmedFetch`.
    public static func request(_ site: Site, query: String, range: TimeRange = .all, max: Int = 10, now: Date = Date()) -> URL {
        let since = range.since(now)
        switch site {
        case .arxiv:
            return url("https://export.arxiv.org/api/query", [("search_query", "all:\(query)"), ("start", "0"), ("max_results", "\(max)"),
                                                              ("sortBy", "relevance"), ("sortOrder", "descending")])
        case .wikipedia:
            return url("https://en.wikipedia.org/w/api.php", [("action", "query"), ("list", "search"), ("srsearch", query), ("srlimit", "\(max)"),
                                                             ("srprop", "snippet|timestamp"), ("format", "json")])
        case .hackerNews:
            var items = [("query", query), ("tags", "story"), ("hitsPerPage", "\(max)")]
            if let since { items.append(("numericFilters", "created_at_i>\(Int(since.timeIntervalSince1970))")) }
            return url("https://hn.algolia.com/api/v1/search", items)
        case .github:
            var q = query
            if let since { q += " pushed:>\(iso(since))" }
            return url("https://api.github.com/search/repositories", [("q", q), ("sort", "stars"), ("order", "desc"), ("per_page", "\(max)")])
        case .reddit:
            let t: String
            switch range { case .all: t = "all"; case .day: t = "day"; case .week, .twoWeeks: t = "week"; case .month, .quarter: t = "month"; case .year: t = "year" }
            return url("https://www.reddit.com/search.json", [("q", query), ("sort", "relevance"), ("t", t), ("limit", "\(max)")])
        case .stackOverflow:
            var items = [("order", "desc"), ("sort", "relevance"), ("intitle", query), ("site", "stackoverflow"), ("pagesize", "\(max)")]
            if let since { items.append(("fromdate", "\(Int(since.timeIntervalSince1970))")) }
            return url("https://api.stackexchange.com/2.3/search", items)
        case .semanticScholar:
            var items = [("query", query), ("limit", "\(max)"), ("fields", "title,abstract,url,year,authors,citationCount,publicationDate")]
            if let since { items.append(("year", "\(Calendar(identifier: .gregorian).component(.year, from: since))-")) }
            return url("https://api.semanticscholar.org/graph/v1/paper/search", items)
        case .openAlex:
            var items = [("search", query), ("per-page", "\(max)"), ("sort", "relevance_score:desc")]
            if let since { items.append(("filter", "from_publication_date:\(iso(since))")) }
            return url("https://api.openalex.org/works", items)
        case .pubmed:
            var items = [("db", "pubmed"), ("term", query), ("retmax", "\(max)"), ("retmode", "json"), ("sort", "relevance")]
            if let since { items += [("mindate", iso(since).replacingOccurrences(of: "-", with: "/")), ("maxdate", iso(now).replacingOccurrences(of: "-", with: "/")), ("datetype", "pdat")] }
            return url("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi", items)
        }
    }

    /// PubMed's second call: the summaries of the ids the search returned.
    public static func pubmedFetch(ids: [String]) -> URL {
        url("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi", [("db", "pubmed"), ("id", ids.joined(separator: ",")), ("retmode", "json")])
    }

    /// The ids in PubMed's search reply.
    public static func pubmedIds(_ data: Data) -> [String] {
        let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        return ((json?["esearchresult"] as? [String: Any])?["idlist"] as? [String]) ?? []
    }

    // MARK: replies

    static func strip(_ html: String) -> String {
        html.replacingOccurrences(of: "<[^>]+>", with: "", options: .regularExpression)
            .replacingOccurrences(of: "&quot;", with: "\"").replacingOccurrences(of: "&amp;", with: "&").replacingOccurrences(of: "&#39;", with: "'")
            .replacingOccurrences(of: "&lt;", with: "<").replacingOccurrences(of: "&gt;", with: ">")
            .replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression).trimmingCharacters(in: .whitespacesAndNewlines)
    }

    static func cut(_ s: String, _ n: Int = 300) -> String { s.count > n ? String(s.prefix(n)).trimmingCharacters(in: .whitespaces) + "…" : s }

    /// The results in a site's reply (for PubMed, the summaries reply).
    public static func parse(_ site: Site, _ data: Data) -> [Result] {
        switch site {
        case .arxiv: return parseArxiv(data)
        case .wikipedia:
            let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            let items = ((json?["query"] as? [String: Any])?["search"] as? [[String: Any]]) ?? []
            return items.compactMap { item in
                guard let title = item["title"] as? String else { return nil }
                let slug = title.replacingOccurrences(of: " ", with: "_").addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? title
                return Result(title: title, url: "https://en.wikipedia.org/wiki/\(slug)", snippet: strip(item["snippet"] as? String ?? ""), site: site,
                              published: item["timestamp"] as? String)
            }
        case .hackerNews:
            let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            return ((json?["hits"] as? [[String: Any]]) ?? []).compactMap { hit in
                guard let title = hit["title"] as? String, let id = hit["objectID"] as? String else { return nil }
                let url = (hit["url"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? "https://news.ycombinator.com/item?id=\(id)"
                return Result(title: title, url: url, snippet: "\(hit["points"] as? Int ?? 0) points • \(hit["num_comments"] as? Int ?? 0) comments", site: site,
                              published: hit["created_at"] as? String, author: hit["author"] as? String)
            }
        case .github:
            let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            return ((json?["items"] as? [[String: Any]]) ?? []).compactMap { repo in
                guard let name = repo["full_name"] as? String, let url = repo["html_url"] as? String else { return nil }
                let stars = repo["stargazers_count"] as? Int ?? 0
                return Result(title: name, url: url, snippet: cut((repo["description"] as? String) ?? "No description") + " • ★ \(stars)", site: site,
                              published: repo["pushed_at"] as? String, author: (repo["owner"] as? [String: Any])?["login"] as? String)
            }
        case .reddit:
            let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            let children = ((json?["data"] as? [String: Any])?["children"] as? [[String: Any]]) ?? []
            return children.compactMap { child in
                guard let post = child["data"] as? [String: Any], let title = post["title"] as? String, let permalink = post["permalink"] as? String else { return nil }
                let text = (post["selftext"] as? String) ?? ""
                var snippet = text.isEmpty ? "Link post" : cut(text)
                if let sub = post["subreddit"] as? String { snippet = "r/\(sub) • \(post["score"] as? Int ?? 0) points • " + snippet }
                let created = (post["created_utc"] as? Double).map { iso(Date(timeIntervalSince1970: $0)) }
                return Result(title: title, url: "https://reddit.com\(permalink)", snippet: snippet, site: site, published: created, author: post["author"] as? String)
            }
        case .stackOverflow:
            let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            return ((json?["items"] as? [[String: Any]]) ?? []).compactMap { item in
                guard let title = item["title"] as? String, let link = item["link"] as? String else { return nil }
                let answered = (item["is_answered"] as? Bool) == true ? "answered" : "open"
                let tags = (item["tags"] as? [String] ?? []).prefix(4).joined(separator: ", ")
                let created = (item["creation_date"] as? Double).map { iso(Date(timeIntervalSince1970: $0)) }
                return Result(title: strip(title), url: link, snippet: "\(item["answer_count"] as? Int ?? 0) answers • \(answered)" + (tags.isEmpty ? "" : " • \(tags)"),
                              site: site, published: created, author: (item["owner"] as? [String: Any])?["display_name"] as? String)
            }
        case .semanticScholar:
            let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            return ((json?["data"] as? [[String: Any]]) ?? []).compactMap { paper in
                guard let title = paper["title"] as? String else { return nil }
                let id = paper["paperId"] as? String ?? ""
                let url = (paper["url"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? "https://www.semanticscholar.org/paper/\(id)"
                let authors = ((paper["authors"] as? [[String: Any]]) ?? []).compactMap { $0["name"] as? String }
                var snippet = (paper["abstract"] as? String).map { cut($0) } ?? "No abstract available"
                if let c = paper["citationCount"] as? Int { snippet += " • \(c) citations" }
                return Result(title: title, url: url, snippet: snippet, site: site, published: paper["publicationDate"] as? String ?? (paper["year"] as? Int).map(String.init),
                              author: authorLine(authors))
            }
        case .openAlex:
            let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            return ((json?["results"] as? [[String: Any]]) ?? []).compactMap { work in
                guard let title = (work["display_name"] ?? work["title"]) as? String else { return nil }
                let doi = work["doi"] as? String
                let landing = ((work["primary_location"] as? [String: Any])?["landing_page_url"] as? String)
                let url = landing ?? doi ?? (work["id"] as? String ?? "")
                guard !url.isEmpty else { return nil }
                let authors = ((work["authorships"] as? [[String: Any]]) ?? []).compactMap { ($0["author"] as? [String: Any])?["display_name"] as? String }
                var snippet = abstract(from: work["abstract_inverted_index"] as? [String: [Int]]).map { cut($0) } ?? "No abstract available"
                if let c = work["cited_by_count"] as? Int { snippet += " • \(c) citations" }
                return Result(title: title, url: url, snippet: snippet, site: site, published: work["publication_date"] as? String, author: authorLine(authors))
            }
        case .pubmed:
            let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            guard let result = json?["result"] as? [String: Any], let ids = result["uids"] as? [String] else { return [] }
            return ids.compactMap { id in
                guard let item = result[id] as? [String: Any], let title = item["title"] as? String else { return nil }
                let authors = ((item["authors"] as? [[String: Any]]) ?? []).compactMap { $0["name"] as? String }
                let journal = item["fulljournalname"] as? String ?? item["source"] as? String ?? ""
                return Result(title: strip(title), url: "https://pubmed.ncbi.nlm.nih.gov/\(id)/", snippet: journal, site: site,
                              published: item["pubdate"] as? String, author: authorLine(authors))
            }
        }
    }

    static func authorLine(_ names: [String]) -> String? {
        guard !names.isEmpty else { return nil }
        return names.prefix(3).joined(separator: ", ") + (names.count > 3 ? " et al." : "")
    }

    /// OpenAlex stores abstracts as word → positions.
    static func abstract(from index: [String: [Int]]?) -> String? {
        guard let index, !index.isEmpty else { return nil }
        var words: [(Int, String)] = []
        for (word, positions) in index { for p in positions { words.append((p, word)) } }
        return words.sorted { $0.0 < $1.0 }.map(\.1).joined(separator: " ")
    }

    /// arXiv's Atom feed.
    static func parseArxiv(_ data: Data) -> [Result] {
        let reader = AtomReader()
        let parser = XMLParser(data: data)
        parser.delegate = reader
        parser.parse()
        return reader.entries.compactMap { e in
            guard let title = e["title"], let id = e["id"] else { return nil }
            let authors = e["authors"]?.components(separatedBy: "\u{1F}").filter { !$0.isEmpty } ?? []
            let url = e["pdf"] ?? id
            return Result(title: title.replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression).trimmingCharacters(in: .whitespaces),
                          url: url, snippet: cut((e["summary"] ?? "").replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression).trimmingCharacters(in: .whitespaces)),
                          site: .arxiv, published: e["published"].map { String($0.prefix(10)) }, author: authorLine(authors))
        }
    }

    final class AtomReader: NSObject, XMLParserDelegate {
        var entries: [[String: String]] = []
        private var current: [String: String]?
        private var text = ""
        private var inAuthor = false

        func parser(_ parser: XMLParser, didStartElement name: String, namespaceURI: String?, qualifiedName: String?, attributes: [String: String]) {
            text = ""
            switch name {
            case "entry": current = [:]
            case "author": inAuthor = true
            case "link":
                if current != nil, attributes["title"] == "pdf", let href = attributes["href"] { current?["pdf"] = href }
            default: break
            }
        }

        func parser(_ parser: XMLParser, foundCharacters string: String) { text += string }

        func parser(_ parser: XMLParser, didEndElement name: String, namespaceURI: String?, qualifiedName: String?) {
            guard current != nil else { return }
            switch name {
            case "entry": if let c = current { entries.append(c) }; current = nil
            case "author": inAuthor = false
            case "name": if inAuthor { current?["authors", default: ""] += text.trimmingCharacters(in: .whitespacesAndNewlines) + "\u{1F}" }
            case "title", "summary", "published", "id": current?[name] = text.trimmingCharacters(in: .whitespacesAndNewlines)
            default: break
            }
        }
    }
}
