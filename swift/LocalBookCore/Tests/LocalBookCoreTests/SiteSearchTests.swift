import Foundation
import Testing
@testable import LocalBookCore

/// Each research site's request and the reading of its reply, on saved replies.
@Suite("Site search")
struct SiteSearchTests {
    static let now = Date(timeIntervalSince1970: 1_763_337_600)   // 2025-11-17 00:00 UTC

    @Test("requests carry the query, the count and the time range as each site wants them")
    func requests() {
        let a = SiteSearch.request(.arxiv, query: "sparse attention", max: 5, now: Self.now).absoluteString
        #expect(a.hasPrefix("https://export.arxiv.org/api/query?search_query=all:sparse%20attention&start=0&max_results=5"))
        let hn = SiteSearch.request(.hackerNews, query: "swift", range: .week, max: 10, now: Self.now).absoluteString
        #expect(hn.contains("tags=story") && hn.contains("numericFilters=created_at_i%3E\(1_763_337_600 - 7 * 86400)"))
        let gh = SiteSearch.request(.github, query: "mlx", range: .month, now: Self.now).absoluteString
        #expect(gh.contains("q=mlx%20pushed:%3E2025-10-18") && gh.contains("sort=stars"))
        let so = SiteSearch.request(.stackOverflow, query: "actor isolation", now: Self.now).absoluteString
        #expect(so.contains("intitle=actor%20isolation") && so.contains("site=stackoverflow") && !so.contains("fromdate"))
        let pm = SiteSearch.request(.pubmed, query: "melatonin", range: .year, now: Self.now).absoluteString
        #expect(pm.contains("mindate=2024/11/17") && pm.contains("datetype=pdat"))
        #expect(SiteSearch.pubmedFetch(ids: ["1", "2"]).absoluteString.contains("id=1,2"))
        #expect(SiteSearch.request(.reddit, query: "x", range: .twoWeeks).absoluteString.contains("t=week"))
    }

    @Test("arXiv's Atom feed gives title, summary, authors, date and the PDF link")
    func arxiv() {
        let xml = """
        <?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom"><title>ArXiv Query</title>
        <entry><id>http://arxiv.org/abs/2401.00001v1</id><published>2024-01-02T18:00:00Z</published>
        <title>Sparse  Attention
         Revisited</title><summary>We study
        sparse attention.</summary>
        <author><name>A. One</name></author><author><name>B. Two</name></author><author><name>C. Three</name></author><author><name>D. Four</name></author>
        <link href="http://arxiv.org/abs/2401.00001v1" rel="alternate" type="text/html"/>
        <link title="pdf" href="http://arxiv.org/pdf/2401.00001v1" rel="related" type="application/pdf"/>
        </entry></feed>
        """
        let r = SiteSearch.parse(.arxiv, Data(xml.utf8))
        #expect(r.count == 1)
        #expect(r[0].title == "Sparse Attention Revisited")
        #expect(r[0].url == "http://arxiv.org/pdf/2401.00001v1")
        #expect(r[0].snippet == "We study sparse attention.")
        #expect(r[0].published == "2024-01-02")
        #expect(r[0].author == "A. One, B. Two, C. Three et al.")
    }

    @Test("the JSON sites give a title, an address and a snippet each")
    func json() {
        let wiki = #"{"query":{"search":[{"title":"Sparse matrix","snippet":"a <span class=\"searchmatch\">sparse</span> &quot;matrix&quot;","timestamp":"2024-05-01T00:00:00Z"}]}}"#
        let w = SiteSearch.parse(.wikipedia, Data(wiki.utf8))
        #expect(w == [SiteSearch.Result(title: "Sparse matrix", url: "https://en.wikipedia.org/wiki/Sparse_matrix", snippet: "a sparse \"matrix\"", site: .wikipedia, published: "2024-05-01T00:00:00Z")])

        let hn = #"{"hits":[{"title":"Show HN: X","url":"","objectID":"42","points":10,"num_comments":3,"author":"pg","created_at":"2024-01-01T00:00:00Z"}]}"#
        let h = SiteSearch.parse(.hackerNews, Data(hn.utf8))
        #expect(h[0].url == "https://news.ycombinator.com/item?id=42" && h[0].snippet == "10 points • 3 comments")

        let gh = #"{"items":[{"full_name":"ml-explore/mlx","html_url":"https://github.com/ml-explore/mlx","description":"Arrays","stargazers_count":5,"owner":{"login":"ml-explore"},"pushed_at":"2024-01-01T00:00:00Z"}]}"#
        let g = SiteSearch.parse(.github, Data(gh.utf8))
        #expect(g[0].title == "ml-explore/mlx" && g[0].snippet == "Arrays • ★ 5" && g[0].author == "ml-explore")

        let rd = #"{"data":{"children":[{"data":{"title":"T","permalink":"/r/swift/comments/1/t/","selftext":"","subreddit":"swift","score":7,"author":"u","created_utc":1704067200}}]}}"#
        let r = SiteSearch.parse(.reddit, Data(rd.utf8))
        #expect(r[0].url == "https://reddit.com/r/swift/comments/1/t/" && r[0].snippet == "r/swift • 7 points • Link post" && r[0].published == "2024-01-01")

        let so = #"{"items":[{"title":"Why &amp; how?","link":"https://stackoverflow.com/q/1","is_answered":true,"answer_count":2,"tags":["swift"],"creation_date":1704067200,"owner":{"display_name":"Ann"}}]}"#
        let s = SiteSearch.parse(.stackOverflow, Data(so.utf8))
        #expect(s[0].title == "Why & how?" && s[0].snippet == "2 answers • answered • swift")

        let ss = #"{"data":[{"paperId":"abc","title":"P","abstract":"A long abstract.","url":"","year":2023,"citationCount":12,"authors":[{"name":"X"}]}]}"#
        let p = SiteSearch.parse(.semanticScholar, Data(ss.utf8))
        #expect(p[0].url == "https://www.semanticscholar.org/paper/abc" && p[0].snippet == "A long abstract. • 12 citations" && p[0].published == "2023")

        let oa = #"{"results":[{"id":"https://openalex.org/W1","display_name":"W","doi":"https://doi.org/10.1/x","publication_date":"2022-02-02","cited_by_count":3,"abstract_inverted_index":{"world":[1],"Hello":[0]},"authorships":[{"author":{"display_name":"Q"}}]}]}"#
        let o = SiteSearch.parse(.openAlex, Data(oa.utf8))
        #expect(o[0].url == "https://doi.org/10.1/x" && o[0].snippet == "Hello world • 3 citations" && o[0].author == "Q")

        let pmSearch = #"{"esearchresult":{"idlist":["111","222"]}}"#
        #expect(SiteSearch.pubmedIds(Data(pmSearch.utf8)) == ["111", "222"])
        let pm = #"{"result":{"uids":["111"],"111":{"title":"Sleep &amp; light","fulljournalname":"J Sleep","pubdate":"2021 Mar","authors":[{"name":"Lee J"}]}}}"#
        let m = SiteSearch.parse(.pubmed, Data(pm.utf8))
        #expect(m[0].url == "https://pubmed.ncbi.nlm.nih.gov/111/" && m[0].title == "Sleep & light" && m[0].snippet == "J Sleep")

        #expect(SiteSearch.parse(.github, Data("not json".utf8)).isEmpty)
    }
}
