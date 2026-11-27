import Foundation
import Testing
@testable import LocalBookCore

@Suite struct FeedParserTests {
    static let rss = """
    <?xml version="1.0"?>
    <rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/" xmlns:dc="http://purl.org/dc/elements/1.1/">
    <channel><title>Harbor Lights News</title><link>https://example.com/news</link>
    <item><title>Harbor Lights opens Denver roastery</title><link>https://example.com/news/denver</link>
      <description><![CDATA[<p>The company&#39;s third roastery &amp; café opens in <b>Denver</b>.</p>]]></description>
      <dc:creator>Rosa Alvarez</dc:creator><pubDate>Mon, 24 Nov 2026 09:30:00 GMT</pubDate><guid>https://example.com/news/denver</guid></item>
    <item><title>Shipping policy update</title><guid isPermaLink="true">https://example.com/news/shipping</guid>
      <content:encoded><![CDATA[Free shipping over $40 from December.]]></content:encoded><pubDate>Tue, 25 Nov 2026 08:00:00 +0000</pubDate></item>
    </channel></rss>
    """
    static let atom = """
    <?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"><title>Roasting Notes</title><link rel="self" href="https://example.org/atom.xml"/><link rel="alternate" href="https://example.org/"/>
    <entry><title>Light roasts and acidity</title><link rel="alternate" href="https://example.org/posts/light"/><id>urn:uuid:1</id>
      <updated>2026-11-20T10:00:00Z</updated><author><name>Priya Raman</name></author><summary>Why lighter roasts taste brighter.</summary></entry>
    </feed>
    """

    @Test func readsRSS() throws {
        let feed = try #require(FeedParser.parse(Data(Self.rss.utf8)))
        #expect(feed.title == "Harbor Lights News" && feed.link == "https://example.com/news")
        #expect(feed.items.count == 2)
        let a = feed.items[0]
        #expect(a.title == "Harbor Lights opens Denver roastery" && a.link == "https://example.com/news/denver" && a.author == "Rosa Alvarez")
        #expect(a.summary == "The company's third roastery & café opens in Denver.")
        #expect(a.published != nil && Calendar(identifier: .gregorian).component(.day, from: a.published!) >= 24)
        let b = feed.items[1]
        #expect(b.link == "https://example.com/news/shipping" && b.summary.hasPrefix("Free shipping"))
    }

    @Test func readsAtom() throws {
        let feed = try #require(FeedParser.parse(Data(Self.atom.utf8)))
        #expect(feed.title == "Roasting Notes" && feed.link == "https://example.org/")
        #expect(feed.items.count == 1)
        #expect(feed.items[0].link == "https://example.org/posts/light" && feed.items[0].author == "Priya Raman" && feed.items[0].summary == "Why lighter roasts taste brighter.")
        #expect(feed.items[0].published == ISO8601DateFormatter().date(from: "2026-11-20T10:00:00Z"))
    }

    @Test func notAFeed() {
        #expect(FeedParser.parse(Data("<html><body>hello</body></html>".utf8)) == nil)
        #expect(FeedParser.parse(Data("not xml at all".utf8)) == nil)
    }

    @Test func findsFeedLinksInAPage() {
        let html = "<html><head><link rel=\"stylesheet\" href=\"/a.css\"><link rel=\"alternate\" type=\"application/rss+xml\" title=\"RSS\" href=\"/feed.xml\"><LINK REL='alternate' TYPE='application/atom+xml' HREF='https://other.example/atom'></head></html>"
        let links = FeedParser.feedLinks(inHTML: html, base: URL(string: "https://example.com/blog/")!)
        #expect(links.map(\.absoluteString) == ["https://example.com/feed.xml", "https://other.example/atom"])
    }

    @Test func dates() {
        #expect(FeedParser.date("Mon, 24 Nov 2026 09:30:00 GMT") != nil)
        #expect(FeedParser.date("2026-11-20T10:00:00.123Z") != nil)
        #expect(FeedParser.date("2026-11-20") != nil)
        #expect(FeedParser.date("yesterday") == nil)
    }
}

@Suite struct CollectorTests {
    @Test func oldProfileReads() throws {
        let json = """
        {"name":"Scout","notebook_purpose":"company_intel","subject":"Harbor Lights","intent":"Track the coffee company","focus_areas":["roasting","expansion"],
         "excluded_topics":["recipes"],"collection_mode":"hybrid","approval_mode":"mixed",
         "sources":{"rss_feeds":["https://example.com/feed.xml"],"web_pages":["https://example.com/about"],"news_keywords":["Harbor Lights"]},
         "schedule":{"frequency":"daily","max_items_per_run":10},"filters":{"max_age_days":30,"min_relevance":0.5,"language":"en"},
         "created_at":"2026-08-01T10:00:00Z","updated_at":"2026-08-02T10:00:00Z"}
        """
        let d = JSONDecoder(); d.dateDecodingStrategy = .iso8601
        let c = try d.decode(CollectorConfig.self, from: Data(json.utf8))
        #expect(c.subject == "Harbor Lights" && c.focusAreas == ["roasting", "expansion"] && c.approvalMode == .mixed && c.frequency == .daily)
        #expect(c.rssFeeds == ["https://example.com/feed.xml"] && c.webPages.count == 1 && c.newsKeywords == ["Harbor Lights"] && c.maxAgeDays == 30)
        #expect(c.intentText == "Harbor Lights. Track the coffee company. roasting, expansion")
        let e = JSONEncoder(); e.dateEncodingStrategy = .iso8601
        let text = String(decoding: try e.encode(c), as: UTF8.self)
        #expect(text.contains("\"rss_feeds\"") && text.contains("\"approval_mode\":\"mixed\"") && text.contains("\"max_items_per_run\":10"))
        let again = try d.decode(CollectorConfig.self, from: try e.encode(c))
        #expect(again.rssFeeds == c.rssFeeds && again.frequency == c.frequency)
    }

    @Test func scoringAndDecisions() {
        let now = Date(timeIntervalSince1970: 1_764_000_000)
        var item = CollectedItem(title: "Harbor Lights opens Denver roastery", url: "https://example.com/news/denver", content: "The third roastery opens in Denver.", sourceName: "News", sourceType: "rss", sourceURL: "https://example.com/feed.xml", published: now.addingTimeInterval(-3_600))
        var health = SourceHealthRecord(url: "https://example.com/feed.xml")
        health.succeeded(items: 12, at: now)
        CollectorScoring.score(&item, cosine: 0.84, health: health, userAdded: true, learnedTopics: ["denver"], rejectedBefore: false, maxAgeDays: 30, now: now)
        #expect(item.relevanceScore == 0.9 && item.sourceTrust == 0.9 && item.freshnessScore == 1)
        #expect(item.overallConfidence > 0.95 && item.confidenceReasons.contains { $0.contains("Published today") } && item.confidenceReasons.contains { $0.contains("denver") })
        #expect(CollectorScoring.decision(for: item, mode: .mixed, minRelevance: 0.5) == .store)
        #expect(CollectorScoring.decision(for: item, mode: .showMe, minRelevance: 0.5) == .queue)
        #expect(CollectorScoring.decision(for: item, mode: .trustMe, minRelevance: 0.5) == .store)

        var stale = item
        stale.published = now.addingTimeInterval(-60 * 86_400)
        CollectorScoring.score(&stale, cosine: 0.84, health: health, userAdded: true, learnedTopics: [], rejectedBefore: true, maxAgeDays: 30, now: now)
        #expect(stale.freshnessScore == 0 && stale.overallConfidence <= 0.35 && stale.confidenceReasons.contains { $0.contains("rejected") })
        #expect(CollectorScoring.decision(for: stale, mode: .mixed, minRelevance: 0.5) == .queue)

        var weak = item
        CollectorScoring.score(&weak, cosine: 0.4, health: SourceHealthRecord(url: "x"), userAdded: false, learnedTopics: [], rejectedBefore: false, maxAgeDays: 30, now: now)
        #expect(weak.relevanceScore < 0.2 && CollectorScoring.decision(for: weak, mode: .trustMe, minRelevance: 0.5) == .skip)
        // No intent: relevance unknown, nothing is skipped on it.
        var unknown = item
        CollectorScoring.score(&unknown, cosine: nil, health: health, userAdded: true, learnedTopics: [], rejectedBefore: false, maxAgeDays: 30, now: now)
        #expect(unknown.relevanceScore == 0.5 && CollectorScoring.decision(for: unknown, mode: .mixed, minRelevance: 0) == .queue)
    }

    @Test func filtersHealthExpiryAndSchedule() {
        #expect(CollectorScoring.passesFilters(title: "Denver roastery", summary: "", keywords: ["denver", "austin"], excluded: ["recipes"]))
        #expect(!CollectorScoring.passesFilters(title: "Cold brew recipes", summary: "", keywords: [], excluded: ["recipes"]))
        #expect(!CollectorScoring.passesFilters(title: "Unrelated", summary: "", keywords: ["denver"], excluded: []))
        var h = SourceHealthRecord(url: "u")
        h.failed("timeout"); #expect(h.health == .degraded)
        h.failed("timeout"); h.failed("timeout"); #expect(h.health == .failing)
        h.succeeded(items: 2); #expect(h.health == .healthy && h.consecutiveFailures == 0 && h.itemsCollected == 2)
        var s = CollectorState()
        var old = CollectedItem(title: "old", url: nil, content: "", sourceName: "", sourceType: "rss", sourceURL: "")
        old.expiresAt = Date().addingTimeInterval(-10)
        s.items = [old]
        s.expire()
        #expect(s.items[0].status == "expired" && s.pending.isEmpty)
        #expect(s.nextRun() == nil)                                       // nothing to watch
        s.config.rssFeeds = ["https://example.com/feed.xml"]
        #expect(s.isDue())                                                // never run: due now
        s.lastRun = Date()
        #expect(!s.isDue() && s.nextRun()! > Date())
        s.config.frequency = .manual
        #expect(s.nextRun() == nil)
        #expect(CollectorScoring.hash("a", "b") == CollectorScoring.hash("A", "B") && CollectorScoring.hash("a") != CollectorScoring.hash("b"))
    }
}

@Suite struct CuratorTests {
    @Test func rawDataQuotesNumbers() {
        let since = Date(timeIntervalSince1970: 1_764_000_000), now = since.addingTimeInterval(2 * 86_400)
        let facts = [CuratorBrief.NotebookFacts(id: "a", title: "Harbor Insights", totalSources: 12, newSources: ["Press clipping", "Wellness memo"], collectorSources: ["Wellness memo"],
                                                 questions: ["Who founded it?", "Where is HQ?"], gaps: ["Who is the CFO?"], studio: ["Quiz: Harbor"], insights: ["Timeline"], pending: 3),
                     CuratorBrief.NotebookFacts(id: "b", title: "Quiet one", totalSources: 4, newSources: [], collectorSources: [], questions: [], gaps: [], studio: [], insights: [], pending: 0)]
        let raw = CuratorBrief.rawData(facts, since: since, now: now)
        #expect(raw.contains("2 sources added (1 by the Collector, 1 by you), 2 questions asked, 1 Studio items made, 3 Collector items waiting"))
        #expect(raw.contains("## Harbor Insights (12 sources in all)") && raw.contains("\"Who is the CFO?\"") && raw.contains("Quiet notebooks: Quiet one"))
        #expect(raw.contains("(2 days)"))
        let p = CuratorBrief.briefPrompt(profile: CuratorProfile(name: "Ada"), rawData: raw, now: now, awayFor: "2 days")
        #expect(p.instructions.hasPrefix("You are Ada") && p.prompt.contains("ACTIVITY DATA") && p.prompt.contains(raw))
        #expect(CuratorBrief.weeklyPrompt(profile: CuratorProfile(), rawData: raw, now: now).prompt.contains("WEEKLY WRAP UP STRUCTURE"))
    }

    @Test func patternLabelsAndPairs() {
        let labels = CuratorBrief.patternLabels(from: "Here you go:\n```json\n{\"pairs\":[{\"n\":1,\"label\":\"Denver expansion\",\"why\":\"Both track the move.\"},{\"n\":\"2\",\"label\":\"Shipping costs\"}]}\n```")
        #expect(labels[1]?.label == "Denver expansion" && labels[1]?.why == "Both track the move." && labels[2]?.label == "Shipping costs")
        func s(_ nb: String, _ src: String, _ v: [Float]) -> CuratorBrief.Sample { .init(notebookId: nb, notebookTitle: nb.uppercased(), sourceId: src, filename: src + ".md", text: "text of " + src, vector: VectorMath.normalized(v)) }
        let samples = [s("a", "a1", [1, 0, 0]), s("a", "a2", [0, 1, 0]), s("b", "b1", [0.99, 0.05, 0]), s("b", "b2", [0, 0, 1]), s("a", "a3", [0.98, 0.1, 0])]
        let pairs = CuratorBrief.crossNotebookPairs(samples)
        #expect(pairs.count == 2)                                   // a1–b1 and a3–b1; a1–a3 is the same notebook
        #expect(pairs.allSatisfy { $0.notebookA != $0.notebookB && $0.strength >= 0.74 })
        #expect(pairs[0].strength >= pairs[1].strength)
        #expect(pairs.map(\.id) == Array(Set(pairs.map(\.id))).sorted(by: { a, b in pairs.firstIndex { $0.id == a }! < pairs.firstIndex { $0.id == b }! }))
    }

    @Test func devilsAdvocateCites() {
        let p = CuratorBrief.devilsAdvocatePrompt(profile: CuratorProfile(), thesis: "Harbor Lights should open in Seattle", passages: [("Harbor", "memo.md", "Seattle rents are high.")])
        #expect(p.prompt.contains("[1] (Harbor · memo.md)") && p.prompt.contains("strongest case against"))
    }

    @Test func profileRoundTrip() throws {
        var p = CuratorProfile(name: "Ada", personality: "dry", voice: .executiveBrief, excludedNotebooks: ["x"])
        p.lastBrief = Date(timeIntervalSince1970: 1_764_000_000)
        let e = JSONEncoder(); e.dateEncodingStrategy = .iso8601
        let d = JSONDecoder(); d.dateDecodingStrategy = .iso8601
        let back = try d.decode(CuratorProfile.self, from: try e.encode(p))
        #expect(back == p)
        let old = try d.decode(CuratorProfile.self, from: Data("{\"name\":\"Hal\",\"voice\":\"conversational_analyst\"}".utf8))
        #expect(old.name == "Hal" && old.voice == .conversationalAnalyst && old.personality.contains("thoughtful"))
    }
}

@Suite struct EventLogAndResearchTests {
    @Test func eventsPageByCursor() {
        let saved = AppFolders.base
        AppFolders.base = FileManager.default.temporaryDirectory.appending(path: "lb-events-\(UUID().uuidString)", directoryHint: .isDirectory)
        defer { try? FileManager.default.removeItem(at: AppFolders.base); AppFolders.base = saved }
        try? FileManager.default.createDirectory(at: AppFolders.base, withIntermediateDirectories: true)
        let t0 = Date(timeIntervalSince1970: 1_764_000_000)
        for i in 0..<5 { EventLog.append(LBEvent(kind: "source_added", notebookId: "nb", title: "s\(i)", at: t0.addingTimeInterval(Double(i)))) }
        let first = EventLog.since(cursor: nil, limit: 2)
        #expect(first.events.map(\.title) == ["s0", "s1"])
        let second = EventLog.since(cursor: first.cursor, limit: 10)
        #expect(second.events.map(\.title) == ["s2", "s3", "s4"])
        let none = EventLog.since(cursor: second.cursor)
        #expect(none.events.isEmpty && none.cursor == second.cursor)
        #expect(EventLog.since(t0.addingTimeInterval(3)).count == 2)
        #expect(EventLog.kinds.contains { $0.kind == "collector_found" })
    }

    @Test func researchQuality() {
        let good = ResearchQuality.score(wordCount: 1_800, outboundLinks: 12, ageDays: 5, topicHits: 3, topicWords: 3)
        let thin = ResearchQuality.score(wordCount: 120, outboundLinks: 0, ageDays: 900, topicHits: 0, topicWords: 3)
        #expect(good == 1 && thin < 0.1)
        #expect(ResearchQuality.score(wordCount: 1_800, outboundLinks: 12, ageDays: nil, topicHits: 3, topicWords: 3) < good)
        #expect(ResearchQuality.topicWords("The history of Harbor Lights coffee") == ["history", "harbor", "lights", "coffee"])
        #expect(ResearchQuality.hits("Harbor Lights sells coffee in Denver", words: ["harbor", "coffee", "tea"]) == 2)
        #expect(ResearchQuality.wordCount("one two  three\nfour") == 4)
        let job = ResearchJob(topic: "x", notebookId: "nb", mode: .deep)
        #expect(job.status == .queued && job.id.count == 36)
    }
}
