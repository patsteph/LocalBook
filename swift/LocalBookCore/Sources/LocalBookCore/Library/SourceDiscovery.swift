import Foundation

/// Finding sources for a notebook from one sentence of intent: the old app's `source_discovery.py`, cut to what works
/// without API keys. The answer model reads the intent into a topic, a purpose and search terms (the old prompt's JSON,
/// with the sites added); the terms are searched on the sites that fit the purpose; the hits are merged into one list.
/// This is the pure half: the prompt, the reading of the model's reply, the plan of searches, and the merge.
public enum SourceDiscovery {
    public enum Purpose: String, CaseIterable, Sendable {
        case companyResearch = "company_research", topicResearch = "topic_research", productResearch = "product_research"
        case skillDevelopment = "skill_development", personTracking = "person_tracking", industryMonitoring = "industry_monitoring"
        case projectKnowledge = "project_knowledge", personalInterests = "personal_interests"

        /// The sites the old handlers reached for each purpose, in the order their hits are shown.
        public var sites: [SiteSearch.Site] {
            switch self {
            case .companyResearch, .industryMonitoring: [.wikipedia, .hackerNews, .reddit]
            case .topicResearch: [.wikipedia, .arxiv, .semanticScholar, .hackerNews]
            case .productResearch: [.github, .hackerNews, .stackOverflow, .wikipedia]
            case .skillDevelopment: [.wikipedia, .stackOverflow, .github, .reddit]
            case .personTracking: [.wikipedia, .hackerNews]
            case .projectKnowledge: [.github, .stackOverflow, .hackerNews]
            case .personalInterests: [.wikipedia, .reddit]
            }
        }
    }

    public struct Plan: Sendable, Equatable {
        public var topic: String
        public var purpose: Purpose
        public var keywords: [String]
        public var sites: [SiteSearch.Site]
        public var scholarly: Bool

        public init(topic: String, purpose: Purpose, keywords: [String], sites: [SiteSearch.Site], scholarly: Bool = false) {
            self.topic = topic; self.purpose = purpose; self.keywords = keywords; self.sites = sites; self.scholarly = scholarly
        }

        /// The searches to run: each term on each site, the topic first, at most `maxSearches`.
        public var searches: [(site: SiteSearch.Site, query: String)] {
            var terms = [topic] + keywords.filter { $0.caseInsensitiveCompare(topic) != .orderedSame }
            if terms.count > 3 { terms = Array(terms.prefix(3)) }
            var out: [(SiteSearch.Site, String)] = []
            for term in terms { for site in sites { out.append((site, term)) } }
            return Array(out.prefix(SourceDiscovery.maxSearches)).map { (site: $0.0, query: $0.1) }
        }
    }

    public static let maxSearches = 12
    public static let maxResults = 30

    // MARK: the prompt

    /// The old `_analyze_intent` prompt, trimmed to the fields the searches use, with the notebook's own sources as context.
    public static func prompt(intent: String, notebookTitle: String?, sourceNames: [String]) -> String {
        var context = ""
        if let notebookTitle, !notebookTitle.isEmpty { context += "Notebook: \(notebookTitle)\n" }
        if !sourceNames.isEmpty { context += "Sources already in it: \(sourceNames.prefix(12).joined(separator: "; "))\n" }
        let purposes = Purpose.allCases.map(\.rawValue).joined(separator: ", ")
        let sites = SiteSearch.Site.allCases.map(\.rawValue).joined(separator: ", ")
        return """
        Analyze this research intent and classify it for source discovery.
        \(context)Intent: \(intent)

        NOTEBOOK PURPOSE TYPES: \(purposes)
        - company_research: Track a specific company (e.g., Costco, Tesla, Apple)
        - topic_research: Broad topic like AI, Leadership, Renewable Energy
        - product_research: Specific product or technology (e.g., iPhone, Kubernetes, React)
        - skill_development: Learning a skill (e.g., Python programming, Public Speaking)
        - person_tracking: Following a person, coaching a team member, 1:1 prep
        - industry_monitoring: Monitor an industry sector (e.g., Fintech, Healthcare)
        - project_knowledge: Project-specific knowledge base
        - personal_interests: Hobbies and interests (e.g., Photography, Cooking)

        SITES: \(sites)

        Respond with JSON only:
        {
            "primary_topic": "main research topic, two to five words",
            "notebook_purpose": "one of the purpose types above",
            "keywords": ["three", "short search terms", "a person would type"],
            "sites": ["two to four of the sites above that would have good sources"],
            "scholarly": true/false (whether research papers belong in this notebook)
        }

        Examples:
        - "Track Costco" → company_research, primary_topic: Costco, keywords: [Costco earnings, Costco membership, Costco news]
        - "AI Research" → topic_research, keywords: [machine learning, deep learning, large language models], scholarly: true
        - "Learn Python" → skill_development, keywords: [Python tutorial, Python best practices, Python async]
        - "React development" → product_research, keywords: [React hooks, React server components, React performance]
        """
    }

    // MARK: reading the reply

    /// The model's JSON as a plan; anything missing or malformed falls back to the intent itself as the topic.
    public static func plan(from raw: String, intent: String) -> Plan {
        let fallbackTopic = intent.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let obj = JSONObjects.scan(in: raw).first else {
            return Plan(topic: fallbackTopic, purpose: .topicResearch, keywords: [], sites: Purpose.topicResearch.sites)
        }
        var topic = (obj["primary_topic"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if topic.isEmpty { topic = fallbackTopic }
        let purpose = (obj["notebook_purpose"] as? String).flatMap { Purpose(rawValue: $0.lowercased()) } ?? .topicResearch
        var seen = Set<String>()
        let keywords = ((obj["keywords"] as? [Any]) ?? []).compactMap { $0 as? String }
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty && seen.insert($0.lowercased()).inserted }
        var sites = ((obj["sites"] as? [Any]) ?? []).compactMap { $0 as? String }.compactMap(site(named:))
        sites = sites.reduce(into: []) { if !$0.contains($1) { $0.append($1) } }
        if sites.isEmpty { sites = purpose.sites }
        let scholarly = (obj["scholarly"] as? Bool) ?? false
        if scholarly, !sites.contains(.arxiv), !sites.contains(.semanticScholar) { sites.append(.arxiv) }
        return Plan(topic: topic, purpose: purpose, keywords: Array(keywords.prefix(5)), sites: Array(sites.prefix(4)), scholarly: scholarly)
    }

    /// A site by its id, label or domain, however the model spelled it.
    public static func site(named name: String) -> SiteSearch.Site? {
        let key = name.lowercased().replacingOccurrences(of: " ", with: "").replacingOccurrences(of: "_", with: "")
        guard !key.isEmpty else { return nil }
        return SiteSearch.Site.allCases.first {
            $0.rawValue.lowercased() == key || $0.label.lowercased().replacingOccurrences(of: " ", with: "") == key || $0.domain.contains(key) || key.contains($0.rawValue.lowercased())
        }
    }

    // MARK: the merge

    /// One list from many searches: the same address once, the same title once, sites interleaved so one site does
    /// not take every row, at most `maxResults`.
    public static func merge(_ batches: [[SiteSearch.Result]], max: Int = maxResults) -> [SiteSearch.Result] {
        var seenURL = Set<String>(), seenTitle = Set<String>()
        var out: [SiteSearch.Result] = []
        var queues = batches.map { Array($0) }
        while out.count < max, queues.contains(where: { !$0.isEmpty }) {
            for i in queues.indices where !queues[i].isEmpty && out.count < max {
                let r = queues[i].removeFirst()
                let url = normalized(r.url), title = r.title.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
                guard !url.isEmpty, seenURL.insert(url).inserted, title.isEmpty || seenTitle.insert(title).inserted else { continue }
                out.append(r)
            }
        }
        return out
    }

    static func normalized(_ url: String) -> String {
        var s = url.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
        for p in ["https://", "http://"] where s.hasPrefix(p) { s.removeFirst(p.count) }
        if s.hasPrefix("www.") { s.removeFirst(4) }
        while s.hasSuffix("/") { s.removeLast() }
        return s
    }
}
