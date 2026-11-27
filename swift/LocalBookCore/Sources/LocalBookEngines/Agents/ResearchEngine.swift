import Foundation
import LocalBookCore
import Observation

/// `@research`: a web search across the sites the app can search, a site search, or a deep dive that reads the top
/// pages, scores their quality and summarizes the keepers. Results go to the notebook's Collector queue, and a job
/// can be asked about by id (the local API's `start_research` / `get_job`).
@MainActor @Observable
public final class ResearchEngine {
    public private(set) var jobs: [ResearchJob] = ResearchStore.load()
    public private(set) var running: String?
    private weak var library: LibraryModel?

    public init() {}

    public func job(_ id: String) -> ResearchJob? { jobs.first { $0.id == id } }
    public func jobs(for notebook: String) -> [ResearchJob] { jobs.filter { $0.notebookId == notebook }.sorted { $0.created > $1.created } }

    /// Queues a job and runs it when nothing else is running. Returns the job id at once.
    @discardableResult
    public func start(topic: String, notebook: String, mode: ResearchJob.Mode, site: SiteSearch.Site? = nil, library: LibraryModel) -> String {
        self.library = library
        let job = ResearchJob(topic: topic.trimmingCharacters(in: .whitespacesAndNewlines), notebookId: notebook, mode: mode, site: site?.rawValue)
        jobs.append(job); jobs = Array(jobs.suffix(200)); ResearchStore.save(jobs)
        Task { await runNext() }
        return job.id
    }

    private func set(_ id: String, _ change: (inout ResearchJob) -> Void) {
        guard let i = jobs.firstIndex(where: { $0.id == id }) else { return }
        change(&jobs[i]); ResearchStore.save(jobs)
    }

    private func runNext() async {
        guard running == nil, let library, let next = jobs.first(where: { $0.status == .queued }) else { return }
        running = next.id
        set(next.id) { $0.status = .running; $0.progress = "Searching…" }
        await run(next, library: library)
        running = nil
        await runNext()
    }

    private func run(_ job: ResearchJob, library: LibraryModel) async {
        let words = ResearchQuality.topicWords(job.topic)
        var results: [ResearchJob.Result] = []
        var problems: [String] = []
        // 1. Search.
        if job.mode == .site, let raw = job.site, let site = SiteSearch.Site(rawValue: raw) {
            let (batches, p) = await SiteSearchClient.searchAll([site], query: job.topic, max: 12)
            problems = p
            results = batches.flatMap { $0 }.map { .init(title: $0.title, url: $0.url, snippet: $0.snippet, site: $0.site.label, published: $0.published) }
        } else {
            let d = await library.discoverSources(intent: job.topic, notebook: job.notebookId)
            problems = d.problems
            results = d.results.map { .init(title: $0.title, url: $0.url, snippet: $0.snippet, site: $0.site.label, published: $0.published) }
        }
        // Drop what the notebook already has.
        let existing = await CollectorEngine.existingURLs(notebook: job.notebookId, library: library)
        results.removeAll { existing.contains(CollectorEngine.normalized($0.url)) }
        var seen = Set<String>()
        results = results.filter { seen.insert(CollectorEngine.normalized($0.url)).inserted }
        set(job.id) { $0.results = results; $0.problems = problems; $0.progress = "\(results.count) found" }

        // 2. A deep dive reads the top pages and keeps the best.
        if job.mode == .deep, !results.isEmpty {
            let gemma = await AgentText.model(library)
            var read: [ResearchJob.Result] = []
            for (i, found) in results.prefix(10).enumerated() {
                var r = found
                set(job.id) { $0.progress = "Reading \(i + 1) of \(min(10, results.count))…" }
                guard let url = URL(string: r.url), case .text(let title, let text, _, _, let date)? = try? await WebExtract.fetch(url) else { continue }
                r.wordCount = ResearchQuality.wordCount(text)
                let links = text.components(separatedBy: "http").count - 1
                let age: Int? = (date ?? r.published).flatMap(FeedParser.date).map { Int(Date().timeIntervalSince($0) / 86_400) }
                r.quality = ResearchQuality.score(wordCount: r.wordCount, outboundLinks: links, ageDays: age, topicHits: ResearchQuality.hits(text, words: words), topicWords: words.count)
                if r.title.isEmpty { r.title = title }
                guard r.wordCount >= ResearchQuality.minWordCount / 2, r.quality >= ResearchQuality.minQuality * 0.75 else { continue }
                if let gemma, let s = await AgentText.generate(gemma, instructions: "You summarize web pages for a researcher. Plain prose, no preamble.", prompt: ResearchQuality.summaryPrompt(topic: job.topic, title: r.title, text: text), tokens: 220, label: "research") { r.summary = s }
                read.append(r)
            }
            results = read.sorted { $0.quality > $1.quality }.prefix(ResearchQuality.maxResults).map { $0 }
        }
        let queued = Agents.collector.enqueue(results, topic: job.topic, notebook: job.notebookId)
        set(job.id) { $0.results = results; $0.status = results.isEmpty && !problems.isEmpty ? .failed : .done; $0.finished = Date(); $0.queuedToCollector = queued
            $0.progress = results.isEmpty ? (problems.first ?? "Nothing found") : "\(results.count) result\(results.count == 1 ? "" : "s"), \(queued) queued for approval" }
        EventLog.record("research_done", notebook: job.notebookId, title: job.topic, detail: "\(results.count) results")
    }
}
