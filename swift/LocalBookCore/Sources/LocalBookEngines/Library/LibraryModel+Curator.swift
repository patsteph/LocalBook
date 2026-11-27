import Foundation
import LocalBookCore
import MLXLMCommon

/// The Curator's one ability in this build: a question answered across every notebook, each citation naming its
/// notebook. Everything else in Ask stays scoped to the notebook on screen.
extension LibraryModel {
    public struct AcrossNotebooks: Sendable {
        public let hits: [LibraryHit]
        public let notebooksSearched: Int
        public let seconds: Double
    }

    /// The best passages across `notebookIds` (nil: every notebook), `CrossNotebook.perNotebook` from each, vectors
    /// and keywords fused as the notebook search does. No reranker: the scores must compare across notebooks, and
    /// cosine does; the reranker would re-score within one notebook only.
    public func searchAcrossNotebooks(_ question: String, notebookIds: [String]? = nil, topK: Int = CrossNotebook.topK) async throws -> AcrossNotebooks {
        guard let store, let engine, let tokenizer else { throw LibraryError.badResponse("search model not ready") }
        let t0 = Date()
        let text = queryTemplate.replacingOccurrences(of: "{text}", with: recipe?.expand(question) ?? question)
        let vector = try await engine.embed(tokenIds: TokenRepair.spaceFallback(tokenizer.encode(text: text)))
        let keywords = recipe?.keywordQuery(question) ?? question
        let wanted = notebookIds.map(Set.init)
        var perNotebook: [[LibraryHit]] = []
        var searched = 0
        for nb in notebooks where wanted?.contains(nb.id) ?? true {
            guard nb.chunks > 0 else { continue }
            searched += 1
            var params = LibraryStore.SearchParams()
            params.hybridK = CrossNotebook.perNotebook
            if let found = try? await store.search(query: vector, queryText: keywords, params: params, notebook: nb.id), !found.hits.isEmpty {
                perNotebook.append(found.hits)
            }
        }
        await store.warmUp(notebook: scopeToNotebook)      // leave the notebook on screen loaded, as before the sweep
        return AcrossNotebooks(hits: CrossNotebook.merge(perNotebook, topK: topK), notebooksSearched: searched, seconds: Date().timeIntervalSince(t0))
    }

    public struct CuratorAnswer: Sendable {
        public let answer: String
        public let sources: [LibraryHit]
        public let notebooksSearched: Int
        public let seconds: Double
    }

    /// A written answer across notebooks (UI-free; `ask_notebook` with no notebook named uses it too).
    public func answerAcrossNotebooks(_ question: String, topK: Int = CrossNotebook.topK) async throws -> CuratorAnswer {
        let found = try await searchAcrossNotebooks(question, topK: topK)
        guard !found.hits.isEmpty else {
            return CuratorAnswer(answer: "I couldn't find anything about that in any notebook. Try other words, or add a source about it.",
                                 sources: [], notebooksSearched: found.notebooksSearched, seconds: found.seconds)
        }
        let t0 = Date()
        if gemma == nil { gemma = try await AnswerModels.loadContainer() }
        guard let gemma else { throw LibraryError.badResponse("answer model not loaded") }
        let params = GenerateParameters(maxTokens: AnswerLength.current.maxTokens, temperature: 0.3)
        let session = ChatSession(gemma, instructions: CrossNotebook.instructions, generateParameters: params)
        var text = ""
        for try await item in session.streamDetails(to: CrossNotebook.prompt(question: question, hits: found.hits, notebooksSearched: found.notebooksSearched)) {
            if case .chunk(let c) = item { text += c }
        }
        let cleaned = recipe.map { $0.fixCitations($0.clean(text), count: found.hits.count) } ?? text
        return CuratorAnswer(answer: cleaned, sources: found.hits, notebooksSearched: found.notebooksSearched, seconds: found.seconds + Date().timeIntervalSince(t0))
    }

    /// `@curator …` in the Ask field: the answer shows in the live area of the notebook on screen, its sources
    /// carrying their notebooks' names. `@curator ?` shows the help.
    public func askCurator(_ ask: String) async {
        guard !answering else { return }
        let question = ask.trimmingCharacters(in: .whitespacesAndNewlines)
        beginExternalAnswer(question: "@curator " + (question.isEmpty ? "?" : question), status: "Curator is reading every notebook…", notebook: scopeToNotebook)
        defer { endExternalAnswer() }
        if question.isEmpty || question == "?" {
            showExternalAnswer(text: CrossNotebook.help, sources: [], status: "Curator")
            return
        }
        do {
            let found = try await searchAcrossNotebooks(question)
            if found.hits.isEmpty {
                showExternalAnswer(text: "I couldn't find anything about that in any notebook. Try other words, or add a source about it.",
                                   sources: [], status: CrossNotebook.status(notebooksSearched: found.notebooksSearched, hits: [], seconds: found.seconds))
                return
            }
            setAnswerStatus("Curator is writing from \(found.hits.count) passages…")
            let t0 = Date()
            if gemma == nil { gemma = try await AnswerModels.loadContainer() }
            guard let gemma else { return }
            let session = ChatSession(gemma, instructions: CrossNotebook.instructions,
                                      generateParameters: GenerateParameters(maxTokens: AnswerLength.current.maxTokens, temperature: 0.3))
            var text = ""
            for try await item in session.streamDetails(to: CrossNotebook.prompt(question: question, hits: found.hits, notebooksSearched: found.notebooksSearched)) {
                if case .chunk(let c) = item {
                    text += c
                    showExternalAnswer(text: text, sources: found.hits, status: "Curator is writing…")
                }
            }
            let cleaned = recipe.map { $0.fixCitations($0.clean(text), count: found.hits.count) } ?? text
            showExternalAnswer(text: cleaned, sources: found.hits,
                               status: CrossNotebook.status(notebooksSearched: found.notebooksSearched, hits: found.hits, seconds: found.seconds + Date().timeIntervalSince(t0)))
        } catch {
            showExternalAnswer(text: "", sources: [], status: "Curator could not answer: \(error.localizedDescription)")
        }
    }
}
