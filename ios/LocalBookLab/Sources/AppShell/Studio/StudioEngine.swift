import Foundation
import MLXLMCommon
import Observation
import UIKit

/// Makes Studio items on the phone: gathers passages from one notebook with the same search as questions, then
/// has the answer model write from only those passages. Foreground only (like answers): the GPU is not available
/// to a locked phone, so leaving the app stops the work.
@MainActor @Observable
final class StudioEngine {
    private(set) var working = false
    private(set) var status = ""
    private(set) var preview = ""
    private(set) var error: String?
    private var task: Task<Void, any Error>?

    func stop() { task?.cancel() }

    /// Returns the saved item, or nil (with `error` set) when it could not be made.
    func make(_ kind: StudioItem.Kind, in notebook: LibraryNotebookInfo, topic: String, library: LibraryModel) async -> StudioItem? {
        guard !working else { return nil }
        working = true; error = nil; preview = ""
        defer { working = false; status = "" }
        guard let gemma = library.gemma else { error = "The answer model is still getting ready. Try again in a moment."; return nil }
        guard GPUGuard.canStart else { error = "Open the app and try again: this can't run while the screen is off."; return nil }

        let topic = topic.trimmingCharacters(in: .whitespacesAndNewlines)
        status = "Reading the notebook…"
        var seeds = ["the main ideas and key points", "important facts, names, dates and numbers", "conclusions, decisions and open questions"]
        if !topic.isEmpty { seeds = [topic, "\(topic) key facts and details", "\(topic) background and context"] }
        var passages: [LibraryHit] = []
        var seen = Set<String>()
        for seed in seeds {
            if let found = try? await library.retrieve(question: seed, notebook: notebook.id) {
                for hit in found.hits where seen.insert(hit.id).inserted { passages.append(hit) }
            }
        }
        passages = Array(passages.prefix(kind == .doc ? 12 : 10))
        guard !passages.isEmpty else { error = "Nothing in this notebook matches that yet."; return nil }

        let numbered = passages.enumerated().map { i, h in "[\(i + 1)] (\(h.filename))\n\(String(h.text.prefix(900)))" }.joined(separator: "\n\n")
        let (instructions, request, tokens) = Self.prompt(kind, notebookTitle: notebook.title, topic: topic)
        let prompt = "Passages:\n\n\(numbered)\n\n\(request)"

        status = kind == .doc ? "Writing…" : "Making the \(kind == .quiz ? "questions" : "cards")…"
        let session = ChatSession(gemma, instructions: instructions, generateParameters: GenerateParameters(maxTokens: tokens))
        let work = Task { @MainActor in
            for try await item in session.streamDetails(to: prompt) {
                if case .chunk(let s) = item { self.preview += s }
            }
        }
        task = work
        let observer = GPUGuard.protect(work, label: "studio")
        UIApplication.shared.isIdleTimerDisabled = true
        defer { UIApplication.shared.isIdleTimerDisabled = false }
        do { try await work.value } catch {}
        NotificationCenter.default.removeObserver(observer)
        task = nil
        if work.isCancelled { error = "Stopped."; return nil }

        let raw = preview.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let body = Self.body(for: kind, from: raw) else {
            error = "The model's answer couldn't be read as \(kind == .quiz ? "a quiz" : "flashcards"). Try again."
            return nil
        }
        let title = Self.title(kind, notebook: notebook.title, topic: topic, body: body)
        let item = StudioItem(id: UUID().uuidString, notebookId: notebook.id, kind: kind, title: title, created: Date(), body: body)
        StudioStore.save(item)
        return item
    }

    // MARK: prompts and parsing

    static func prompt(_ kind: StudioItem.Kind, notebookTitle: String, topic: String) -> (String, String, Int) {
        let about = topic.isEmpty ? "the notebook “\(notebookTitle)”" : "“\(topic)” in the notebook “\(notebookTitle)”"
        let base = "You write ONLY from the numbered passages from the user's own notes. Never add facts that are not in them. Plain text: no LaTeX."
        switch kind {
        case .doc:
            return (base + " Cite the passages you used like [1] or [2].",
                    "Write a clear briefing about \(about). Start with a one-line title beginning with “# ”. Then short sections with “## ” headings and bullet points where they help. About 450 words.",
                    1_100)
        case .quiz:
            return (base + " You reply with JSON only.",
                    "Write 6 multiple-choice questions about \(about). Reply with ONLY a JSON array, no other text. Each item: {\"question\": string, \"choices\": [4 short strings], \"answer\": index 0-3 of the correct choice, \"why\": one sentence from the passages}.",
                    1_400)
        case .cards:
            return (base + " You reply with JSON only.",
                    "Write 10 flashcards about \(about). Reply with ONLY a JSON array, no other text. Each item: {\"front\": a short question or term, \"back\": a short answer from the passages}.",
                    1_000)
        }
    }

    /// The text to keep: the piece itself, or the cleaned JSON for quiz and cards. nil if it cannot be read.
    static func body(for kind: StudioItem.Kind, from raw: String) -> String? {
        if kind == .doc { return raw.isEmpty ? nil : raw }
        guard let start = raw.firstIndex(of: "["), let end = raw.lastIndex(of: "]"), start < end,
              let data = String(raw[start...end]).data(using: .utf8) else { return nil }
        let decoder = JSONDecoder()
        switch kind {
        case .quiz:
            guard let qs = try? decoder.decode([StudioQuestion].self, from: data) else { return nil }
            let good = qs.filter { $0.choices.count >= 2 && $0.answer >= 0 && $0.answer < $0.choices.count }
            guard !good.isEmpty, let out = try? JSONEncoder().encode(good) else { return nil }
            return String(decoding: out, as: UTF8.self)
        case .cards:
            guard let cs = try? decoder.decode([StudioCard].self, from: data), !cs.isEmpty,
                  let out = try? JSONEncoder().encode(cs) else { return nil }
            return String(decoding: out, as: UTF8.self)
        case .doc:
            return raw
        }
    }

    static func title(_ kind: StudioItem.Kind, notebook: String, topic: String, body: String) -> String {
        if kind == .doc, let line = body.split(separator: "\n").first(where: { $0.hasPrefix("# ") }) {
            return String(line.dropFirst(2)).trimmingCharacters(in: .whitespaces)
        }
        return topic.isEmpty ? "\(kind.label): \(notebook)" : "\(kind.label): \(topic)"
    }
}
