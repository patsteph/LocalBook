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
    func make(_ kind: StudioItem.Kind, in notebook: LibraryNotebookInfo, topic: String, count: Int, notebookOnly: Bool, library: LibraryModel) async -> StudioItem? {
        guard !working else { return nil }
        working = true; error = nil; preview = ""
        defer { working = false; status = "" }
        guard let gemma = library.gemma else { error = "The answer model is still getting ready. Try again in a moment."; return nil }
        guard GPUGuard.canStart else { error = "Open the app and try again: this can't run while the screen is off."; return nil }

        let topic = topic.trimmingCharacters(in: .whitespacesAndNewlines)
        status = "Reading the notebook…"
        var seeds = ["the main ideas and key points", "important facts, names, dates and numbers", "conclusions, decisions and open questions"]
        if !topic.isEmpty { seeds = [topic, "\(topic) key facts and details", "\(topic) background and context"] }
        if kind == .quiz {
            let t = topic.isEmpty ? "" : topic + " "
            seeds += ["\(t)definitions and explanations", "\(t)examples, processes and steps", "\(t)people, organizations and relationships", "\(t)problems, risks and recommendations"]
        }
        var passages: [LibraryHit] = []
        var seen = Set<String>()
        for seed in seeds {
            if let found = try? await library.retrieve(question: seed, notebook: notebook.id) {
                for hit in found.hits where seen.insert(hit.id).inserted { passages.append(hit) }
            }
        }
        passages = Array(passages.prefix(kind == .doc ? 12 : (kind == .quiz ? min(40, max(16, count * 2)) : min(24, max(10, count + 4)))))
        guard !passages.isEmpty else { error = "Nothing in this notebook matches that yet."; return nil }

        func numbered(_ list: [LibraryHit]) -> String {
            list.enumerated().map { i, h in "[\(i + 1)] (\(h.filename))\n\(String(h.text.prefix(900)))" }.joined(separator: "\n\n")
        }
        let (instructions, request, tokens) = Self.prompt(kind, notebookTitle: notebook.title, topic: topic, count: count, notebookOnly: notebookOnly)

        /// Streams one request to the model; returns its text, or nil if it was stopped.
        func run(_ prompt: String, tokens: Int) async -> String? {
            preview = ""
            let session = ChatSession(gemma, instructions: instructions, generateParameters: GenerateParameters(maxTokens: tokens))
            let work = Task { @MainActor in
                for try await item in session.streamDetails(to: prompt) {
                    if case .chunk(let s) = item { self.preview += s }
                }
            }
            task = work
            let observer = GPUGuard.protect(work, label: "studio")
            do { try await work.value } catch {}
            NotificationCenter.default.removeObserver(observer)
            task = nil
            return work.isCancelled ? nil : preview.trimmingCharacters(in: .whitespacesAndNewlines)
        }

        UIApplication.shared.isIdleTimerDisabled = true
        defer { UIApplication.shared.isIdleTimerDisabled = false }

        var raw = ""
        if kind == .quiz {
            // Built in small batches, each from a different part of the notebook, until there are enough questions.
            let batch = 5, window = 8
            var made: [String] = []
            var misses = 0, round = 0
            while made.count < count && misses < 2 && round < 12 {
                let want = min(batch, count - made.count)
                status = "Making the questions… \(made.count) of \(count)"
                let start = (round * window) % max(1, passages.count)
                let slice = (0..<min(window, passages.count)).map { passages[(start + $0) % passages.count] }
                let avoid = made.isEmpty ? "" : "\n\nQuestions already written (do not repeat them or their topics):\n" + made.map { "- " + $0 }.joined(separator: "\n")
                let (_, req, toks) = Self.prompt(.quiz, notebookTitle: notebook.title, topic: topic, count: want, notebookOnly: notebookOnly)
                guard let out = await run("Passages:\n\n\(numbered(slice))\n\n\(req)\(avoid)", tokens: toks) else { error = "Stopped."; return nil }
                let fresh = Self.jsonObjects(in: out).compactMap { $0["question"] as? String }.filter { q in !made.contains { $0.lowercased() == q.lowercased() } }
                if fresh.isEmpty { misses += 1 } else { misses = 0 }
                made += fresh
                raw += out + "\n"
                round += 1
            }
        } else {
            status = kind == .doc ? "Writing…" : "Making the cards…"
            guard let out = await run("Passages:\n\n\(numbered(passages))\n\n\(request)", tokens: tokens) else { error = "Stopped."; return nil }
            raw = out
        }

        guard var body = Self.body(for: kind, from: raw) else {
            error = "The model's answer couldn't be read as \(kind == .quiz ? "a quiz" : "flashcards"). Try again."
            return nil
        }
        if kind == .quiz, let data = body.data(using: .utf8), var qs = try? JSONDecoder().decode([StudioQuestion].self, from: data) {
            var seen = Set<String>()
            qs = qs.filter { seen.insert($0.question.lowercased()).inserted }
            if qs.count > count { qs = Array(qs.prefix(count)) }
            if let out = try? JSONEncoder().encode(qs) { body = String(decoding: out, as: UTF8.self) }
        }
        let title = Self.title(kind, notebook: notebook.title, topic: topic, body: body)
        let item = StudioItem(id: UUID().uuidString, notebookId: notebook.id, kind: kind, title: title, created: Date(), body: body)
        StudioStore.save(item)
        return item
    }

    // MARK: prompts and parsing

    static func prompt(_ kind: StudioItem.Kind, notebookTitle: String, topic: String, count: Int, notebookOnly: Bool) -> (String, String, Int) {
        let about = topic.isEmpty ? "the notebook “\(notebookTitle)”" : "“\(topic)” in the notebook “\(notebookTitle)”"
        let base = (notebookOnly
            ? "You write ONLY from the numbered passages from the user's own notes. Never add facts that are not in them."
            : "You write mainly from the numbered passages from the user's own notes, and may add well-established general knowledge about the subject.") + " Plain text: no LaTeX."
        switch kind {
        case .doc:
            return (base + " Cite the passages you used like [1] or [2].",
                    "Write a clear briefing about \(about). Start with a one-line title beginning with “# ”. Then short sections with “## ” headings and bullet points where they help. About 450 words.",
                    1_100)
        case .quiz:
            return (base + " You reply with JSON only.",
                    "Write exactly \(count) multiple-choice questions about \(about). Reply with ONLY a JSON array, no other text. Each item: {\"question\": string, \"choices\": [4 short strings], \"answer\": index 0-3 of the correct choice, \"why\": one sentence from the passages}.",
                    max(600, count * 230))
        case .cards:
            return (base + " You reply with JSON only.",
                    "Write exactly \(count) flashcards about \(about). Reply with ONLY a JSON array, no other text. Each item: {\"front\": a short question or term, \"back\": a short answer from the passages}.",
                    max(500, count * 110))
        }
    }

    /// The text to keep: the piece itself, or the cleaned JSON for quiz and cards. nil if it cannot be read.
    /// Reads each {...} object on its own, so one broken or cut-off item doesn't lose the rest.
    static func body(for kind: StudioItem.Kind, from raw: String) -> String? {
        if kind == .doc { return raw.isEmpty ? nil : raw }
        let objects = jsonObjects(in: raw)
        let encoder = JSONEncoder()
        switch kind {
        case .quiz:
            let qs: [StudioQuestion] = objects.compactMap { o in
                guard let q = o["question"] as? String, let choices = o["choices"] as? [String], choices.count >= 2 else { return nil }
                var answer = -1
                if let n = o["answer"] as? Int { answer = n }
                else if let t = (o["answer"] as? String)?.trimmingCharacters(in: .whitespaces) {
                    if let n = Int(t) { answer = n }
                    else if let c = t.uppercased().first, let i = "ABCDEFGH".firstIndex(of: c) { answer = "ABCDEFGH".distance(from: "ABCDEFGH".startIndex, to: i) }
                    else if let i = choices.firstIndex(of: t) { answer = i }
                }
                guard answer >= 0, answer < choices.count else { return nil }
                return StudioQuestion(question: q, choices: choices, answer: answer, why: o["why"] as? String)
            }
            guard !qs.isEmpty, let out = try? encoder.encode(qs) else { return nil }
            return String(decoding: out, as: UTF8.self)
        case .cards:
            let cs: [StudioCard] = objects.compactMap { o in
                guard let f = o["front"] as? String, let b = o["back"] as? String else { return nil }
                return StudioCard(front: f, back: b)
            }
            guard !cs.isEmpty, let out = try? encoder.encode(cs) else { return nil }
            return String(decoding: out, as: UTF8.self)
        case .doc:
            return raw
        }
    }

    /// Every complete top-level {...} object in the text (string-aware), parsed.
    static func jsonObjects(in raw: String) -> [[String: Any]] {
        var out: [[String: Any]] = []
        var depth = 0, inString = false, escaped = false
        var start: String.Index?
        for i in raw.indices {
            let c = raw[i]
            if inString {
                if escaped { escaped = false } else if c == "\\" { escaped = true } else if c == "\"" { inString = false }
                continue
            }
            if c == "\"" { inString = true }
            else if c == "{" { if depth == 0 { start = i }; depth += 1 }
            else if c == "}" && depth > 0 {
                depth -= 1
                if depth == 0, let st = start,
                   let obj = try? JSONSerialization.jsonObject(with: Data(raw[st...i].utf8)) as? [String: Any] { out.append(obj) }
            }
        }
        return out
    }

    static func title(_ kind: StudioItem.Kind, notebook: String, topic: String, body: String) -> String {
        if kind == .doc, let line = body.split(separator: "\n").first(where: { $0.hasPrefix("# ") }) {
            return String(line.dropFirst(2)).trimmingCharacters(in: .whitespaces)
        }
        return topic.isEmpty ? "\(kind.label): \(notebook)" : "\(kind.label): \(topic)"
    }
}
