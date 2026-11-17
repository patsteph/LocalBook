import Foundation
import Testing
@testable import LocalBookCore

/// Source discovery's pure half: the prompt, the plan read from the model's reply, and the merge of the hits.
@Suite("Source discovery")
struct SourceDiscoveryTests {
    @Test("the prompt names the intent, the notebook and its sources, and asks for JSON")
    func prompt() {
        let p = SourceDiscovery.prompt(intent: "Track Costco", notebookTitle: "Retail", sourceNames: ["Q3 call.pdf", "Costco 10-K"])
        #expect(p.contains("Notebook: Retail\nSources already in it: Q3 call.pdf; Costco 10-K\nIntent: Track Costco"))
        #expect(p.contains("\"notebook_purpose\"") && p.contains("hackerNews") && p.contains("Respond with JSON only"))
    }

    @Test("a reply becomes a plan; prose around it and odd site spellings are tolerated")
    func plan() {
        let raw = """
        Here you go: {"primary_topic": "Costco", "notebook_purpose": "company_research", "keywords": ["Costco earnings", "costco EARNINGS", "Costco membership"],
        "sites": ["Hacker News", "wikipedia.org", "reddit", "nowhere"], "scholarly": false}
        """
        let plan = SourceDiscovery.plan(from: raw, intent: "Track Costco")
        #expect(plan.topic == "Costco" && plan.purpose == .companyResearch)
        #expect(plan.keywords == ["Costco earnings", "Costco membership"])
        #expect(plan.sites == [.hackerNews, .wikipedia, .reddit])
        #expect(plan.searches.count == 9 && plan.searches[0].query == "Costco" && plan.searches[0].site == .hackerNews)
    }

    @Test("no JSON falls back to the intent as the topic and the purpose's sites; scholarly adds arXiv")
    func fallback() {
        let plan = SourceDiscovery.plan(from: "I cannot help with that.", intent: " sparse attention ")
        #expect(plan.topic == "sparse attention" && plan.purpose == .topicResearch && plan.sites == SourceDiscovery.Purpose.topicResearch.sites)
        let s = SourceDiscovery.plan(from: #"{"primary_topic":"Sleep","notebook_purpose":"personal_interests","sites":["reddit"],"scholarly":true}"#, intent: "x")
        #expect(s.sites == [.reddit, .arxiv] && s.scholarly)
        #expect(SourceDiscovery.plan(from: #"{"primary_topic":"","keywords":["a","b","c","d","e","f"]}"#, intent: "q").keywords.count == 5)
    }

    @Test("the merge interleaves sites, drops a repeated address or title, and stops at the cap")
    func merge() {
        func r(_ title: String, _ url: String, _ site: SiteSearch.Site) -> SiteSearch.Result { .init(title: title, url: url, snippet: "", site: site) }
        let hn = [r("A", "https://a.com/1", .hackerNews), r("B", "https://b.com", .hackerNews), r("C", "https://c.com", .hackerNews)]
        let wiki = [r("a", "https://www.a.com/1/", .wikipedia), r("D", "http://d.com", .wikipedia), r("B", "https://b2.com", .wikipedia)]
        let merged = SourceDiscovery.merge([hn, wiki])
        #expect(merged.map(\.title) == ["A", "B", "D", "C"])
        #expect(SourceDiscovery.merge([hn, wiki], max: 2).count == 2)
        #expect(SourceDiscovery.normalized("HTTPS://www.Example.com/x/") == "example.com/x")
    }
}
