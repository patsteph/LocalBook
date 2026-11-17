import Foundation
import LocalBookCore

/// The network half of site search: one request per site (two for PubMed), the old scraper's User-Agent, a short
/// timeout, and `SiteSearch.parse` on the reply. Sites that fail return nothing rather than failing the whole search.
public enum SiteSearchClient {
    public static let timeout: TimeInterval = 12

    /// One site's hits for a query.
    public static func search(_ site: SiteSearch.Site, query: String, range: SiteSearch.TimeRange = .all, max: Int = 10) async throws -> [SiteSearch.Result] {
        let data = try await fetch(SiteSearch.request(site, query: query, range: range, max: max))
        guard site == .pubmed else { return SiteSearch.parse(site, data) }
        let ids = SiteSearch.pubmedIds(data)
        guard !ids.isEmpty else { return [] }
        return SiteSearch.parse(.pubmed, try await fetch(SiteSearch.pubmedFetch(ids: ids)))
    }

    /// Several sites at once for one query, each site's hits in its own list (in the order the sites were given); a
    /// site that fails or times out contributes an empty list and a line in `problems`.
    public static func searchAll(_ sites: [SiteSearch.Site], query: String, range: SiteSearch.TimeRange = .all, max: Int = 10)
        async -> (batches: [[SiteSearch.Result]], problems: [String]) {
        await withTaskGroup(of: (Int, [SiteSearch.Result], String?).self) { group in
            for (i, site) in sites.enumerated() {
                group.addTask {
                    do { return (i, try await search(site, query: query, range: range, max: max), nil) }
                    catch { return (i, [], "\(site.label): \(error.localizedDescription)") }
                }
            }
            var batches = Array(repeating: [SiteSearch.Result](), count: sites.count)
            var problems: [String] = []
            for await (i, hits, problem) in group {
                batches[i] = hits
                if let problem { problems.append(problem) }
            }
            return (batches, problems)
        }
    }

    static func fetch(_ url: URL) async throws -> Data {
        var request = URLRequest(url: url, timeoutInterval: timeout)
        request.setValue(WebExtract.userAgent, forHTTPHeaderField: "User-Agent")
        request.setValue("application/json, application/atom+xml, text/xml;q=0.9, */*;q=0.8", forHTTPHeaderField: "Accept")
        let (data, response) = try await URLSession.shared.data(for: request)
        if let http = response as? HTTPURLResponse, http.statusCode >= 400 {
            throw LibraryError.badResponse("\(url.host() ?? "site") answered \(http.statusCode)")
        }
        return data
    }
}

extension LibraryModel {
    public struct Discovery: Sendable {
        public let plan: SourceDiscovery.Plan
        public let results: [SiteSearch.Result]
        public let problems: [String]
    }

    /// Sources for a notebook from one sentence: the answer model reads the intent into a plan (`SourceDiscovery`),
    /// the plan's searches run together, and the hits come back as one list. The model's failure is not fatal: the
    /// intent itself is then the only search term.
    public func discoverSources(intent: String, notebook: String?) async -> Discovery {
        var title: String?, names: [String] = []
        if let notebook {
            title = notebooks.first { $0.id == notebook }?.title
            names = await sources(in: notebook).map(\.name)
        }
        let raw = (try? await generateText(SourceDiscovery.prompt(intent: intent, notebookTitle: title, sourceNames: names), maxTokens: 300)) ?? ""
        let plan = SourceDiscovery.plan(from: raw, intent: intent)
        SoakTrace.log("discover: \(plan.topic) (\(plan.purpose.rawValue)) on \(plan.sites.map(\.label).joined(separator: ", ")), \(plan.searches.count) searches")
        let outcome = await withTaskGroup(of: (Int, [SiteSearch.Result], String?).self) { group in
            for (i, s) in plan.searches.enumerated() {
                group.addTask {
                    do { return (i, try await SiteSearchClient.search(s.site, query: s.query, max: 8), nil) }
                    catch { return (i, [], "\(s.site.label) for “\(s.query)”: \(error.localizedDescription)") }
                }
            }
            var batches = Array(repeating: [SiteSearch.Result](), count: plan.searches.count)
            var problems: [String] = []
            for await (i, hits, problem) in group { batches[i] = hits; if let problem { problems.append(problem) } }
            return (batches, problems)
        }
        return Discovery(plan: plan, results: SourceDiscovery.merge(outcome.0), problems: outcome.1)
    }
}
