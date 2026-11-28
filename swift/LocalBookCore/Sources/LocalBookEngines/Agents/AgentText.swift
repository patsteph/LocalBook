import Foundation
import LocalBookCore
import MLXLMCommon

/// One prompt, one reply, from the loaded answer model, for the agents. Cancelled by the GPU guard when the screen
/// locks, like every other generation in the app.
enum AgentText {
    static func generate(_ gemma: ModelContainer, instructions: String, prompt: String, tokens: Int, temperature: Float = 0.3, label: String = "agent") async -> String? {
        let session = ChatSession(gemma, instructions: instructions, generateParameters: GenerateParameters(maxTokens: tokens, temperature: temperature))
        let box = Box()
        let work = Task { @MainActor in
            for try await item in session.streamDetails(to: prompt) { if case .chunk(let s) = item { box.append(s) } }
        }
        let observer = GPUGuard.protect(work, label: label)
        do { try await work.value } catch {}
        NotificationCenter.default.removeObserver(observer)
        let text = box.text.trimmingCharacters(in: .whitespacesAndNewlines)
        return work.isCancelled || text.isEmpty ? nil : text
    }

    /// The loaded model, or a load if the app has not loaded it yet.
    @MainActor static func model(_ library: LibraryModel) async -> ModelContainer? {
        if let g = library.gemma { return g }
        return try? await AnswerModels.loadContainer()
    }

    private final class Box: @unchecked Sendable {
        private let lock = NSLock()
        private var buffer = ""
        func append(_ s: String) { lock.lock(); buffer += s; lock.unlock() }
        var text: String { lock.lock(); defer { lock.unlock() }; return buffer }
    }

    /// A plain GET with the app's User-Agent and a short timeout.
    static func fetch(_ url: URL, accept: String = "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, text/html;q=0.8, */*;q=0.5", timeout: TimeInterval = 20) async throws -> (Data, String?) {
        var request = URLRequest(url: url, timeoutInterval: timeout)
        request.setValue(WebExtract.userAgent, forHTTPHeaderField: "User-Agent")
        request.setValue(accept, forHTTPHeaderField: "Accept")
        let (data, response) = try await URLSession.shared.data(for: request)
        if let http = response as? HTTPURLResponse, http.statusCode >= 400 { throw LibraryError.badResponse("\(url.host() ?? "site") answered \(http.statusCode)") }
        return (data, (response as? HTTPURLResponse)?.mimeType)
    }
}

/// The agents the app runs, one of each, shared by the panes, the Ask field and the local API.
@MainActor
public enum Agents {
    public static let collector = CollectorEngine()
    public static let curator = CuratorEngine()
    public static let research = ResearchEngine()
    public static let correspondent = CorrespondentEngine()
    public static let people = PeopleEngine()
}
