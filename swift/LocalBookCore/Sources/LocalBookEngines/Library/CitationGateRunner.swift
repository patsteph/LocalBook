import Foundation
import LocalBookCore

/// The citation gate's run: every question of the fixture asked of this Mac's library with the same retrieval Ask
/// uses (`retrieve`, no answer model), compared with what the old app cited. Nothing is written to the library.
extension LibraryModel {
    public func runCitationGate(_ fixture: CitationGate.Fixture, progress: (@MainActor (Int, Int, String) -> Void)? = nil) async -> CitationGate.Report {
        await waitForSearch()
        let known = Set(notebooks.map(\.id))
        var results: [CitationGate.Result] = []
        for (i, q) in fixture.questions.enumerated() {
            if Task.isCancelled { break }
            progress?(i, fixture.questions.count, q.question)
            guard known.contains(q.notebookId) else {
                results.append(CitationGate.Result(q, verdict: .notebookMissing, problem: "no notebook \(q.notebookId) on this Mac")); continue
            }
            let t0 = Date()
            do {
                let found = try await retrieve(question: q.question, notebook: q.notebookId)
                let ours = found.hits.map { CitationGate.Citation(sourceId: $0.sourceId, chunkIndex: $0.chunkIndex, filename: $0.filename, confidence: Double($0.score)) }
                results.append(CitationGate.Result(q, ours: ours, seconds: Date().timeIntervalSince(t0)))
            } catch {
                results.append(CitationGate.Result(q, verdict: .failed, problem: error.localizedDescription))
            }
        }
        progress?(fixture.questions.count, fixture.questions.count, "")
        let recipeLine = recipe.map { "recipe \($0.recipeVersion), reranker \(reranker == nil ? "off" : "on")" } ?? "no recipe (fused order, no boosts)"
        return CitationGate.Report(fixture: fixture, results: results, device: Self.deviceLine, recipe: recipeLine)
    }
}
