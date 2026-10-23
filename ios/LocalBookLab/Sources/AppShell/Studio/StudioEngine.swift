import AVFoundation
import FluidAudio
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
    /// 0…1 of the way through making the item, for the progress visual.
    private(set) var progress = 0.0
    /// The latest line the model has written, shown live while making.
    private(set) var caption = ""
    fileprivate var stopped = false
    private var task: Task<Void, any Error>?

    func stop() { stopped = true; task?.cancel() }

    /// Returns the saved item, or nil (with `error` set) when it could not be made.
    func make(_ kind: StudioItem.Kind, in notebook: LibraryNotebookInfo, topic: String, count: Int, notebookOnly: Bool, style: AudioStyle = .casual, accent: AudioAccent = .british, layout: VisualSpec.Layout = .mindmap, detail: VisualSpec.Detail = .standard, library: LibraryModel) async -> StudioItem? {
        guard !working else { return nil }
        working = true; error = nil; preview = ""; progress = 0; caption = ""; stopped = false
        try? await Task.sleep(for: .milliseconds(200))              // let the making screen appear before the heavy work starts
        let began = Date()
        if kind == .audio { working = false; return await makeAudio(in: notebook, topic: topic, segments: count, notebookOnly: notebookOnly, style: style, accent: accent, library: library) }
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
        passages = Array(passages.prefix(kind == .doc ? 12 : (kind == .visual ? detail.passages : (kind == .quiz ? min(40, max(16, count * 2)) : min(24, max(10, count + 4))))))
        SoakTrace.log("studio \(kind.rawValue): read \(passages.count) passages in \(String(format: "%.1f", Date().timeIntervalSince(began))) s")
        guard !passages.isEmpty else { error = "Nothing in this notebook matches that yet."; return nil }

        func numbered(_ list: [LibraryHit]) -> String {
            list.enumerated().map { i, h in "[\(i + 1)] (\(h.filename))\n\(String(h.text.prefix(900)))" }.joined(separator: "\n\n")
        }
        let (instructions, request, tokens) = Self.prompt(kind, notebookTitle: notebook.title, topic: topic, count: count, notebookOnly: notebookOnly, layout: layout, detail: detail)

        /// Streams one request to the model; returns its text, or nil if it was stopped.
        func run(_ prompt: String, tokens: Int) async -> String? {
            preview = ""
            let runStart = Date()
            let session = ChatSession(gemma, instructions: instructions, generateParameters: GenerateParameters(maxTokens: tokens))
            let work = Task { @MainActor in
                for try await item in session.streamDetails(to: prompt) {
                    if case .chunk(let s) = item {
                        if self.preview.isEmpty { SoakTrace.log("studio \(kind.rawValue): first words after \(String(format: "%.1f", Date().timeIntervalSince(runStart))) s") }
                        self.preview += s
                        if kind == .doc || kind == .visual || kind == .cards { self.progress = min(0.95, Double(self.preview.count) / (Double(tokens) * 2.6)) }
                    }
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
                progress = Double(made.count) / Double(max(1, count))
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
            status = kind == .doc ? "Writing…" : (kind == .visual ? "Drawing…" : "Making the cards…")
            guard let out = await run("Passages:\n\n\(numbered(passages))\n\n\(request)", tokens: tokens) else { error = "Stopped."; return nil }
            raw = out
        }

        if kind == .visual {
            guard let spec = VisualSpec.parse(raw, layout: layout), let data = try? JSONEncoder().encode(spec) else {
                error = "The model's answer couldn't be drawn. Try again."; return nil
            }
            let item = StudioItem(id: UUID().uuidString, notebookId: notebook.id, kind: .visual,
                                  title: spec.title.isEmpty ? "\(layout.label): \(notebook.title)" : "\(layout.label): \(spec.title)",
                                  created: Date(), body: String(decoding: data, as: UTF8.self))
            StudioStore.save(item)
            return item
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

    nonisolated static func prompt(_ kind: StudioItem.Kind, notebookTitle: String, topic: String, count: Int, notebookOnly: Bool, layout: VisualSpec.Layout = .mindmap, detail: VisualSpec.Detail = .standard) -> (String, String, Int) {
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
        case .visual:
            return (base + " Keep every label short. You reply with JSON only.", VisualSpec.request(layout, about: about, detail: detail), detail.tokens)
        case .cards, .audio, .video:
            return (base + " You reply with JSON only.",
                    "Write exactly \(count) flashcards about \(about). Reply with ONLY a JSON array, no other text. Each item: {\"front\": a short question or term, \"back\": a short answer from the passages}.",
                    max(500, count * 110))
        }
    }

    /// The text to keep: the piece itself, or the cleaned JSON for quiz and cards. nil if it cannot be read.
    /// Reads each {...} object on its own, so one broken or cut-off item doesn't lose the rest.
    nonisolated static func body(for kind: StudioItem.Kind, from raw: String) -> String? {
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
        case .doc, .audio, .video:
            return raw
        case .visual:
            return nil     // handled by the caller, which knows the layout
        }
    }

    /// Every complete top-level {...} object in the text (string-aware), parsed.
    nonisolated static func jsonObjects(in raw: String) -> [[String: Any]] {
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

    nonisolated static func title(_ kind: StudioItem.Kind, notebook: String, topic: String, body: String) -> String {
        if kind == .doc, let line = body.split(separator: "\n").first(where: { $0.hasPrefix("# ") }) {
            return String(line.dropFirst(2)).trimmingCharacters(in: .whitespaces)
        }
        return topic.isEmpty ? "\(kind.label): \(notebook)" : "\(kind.label): \(topic)"
    }
}

// MARK: audio overview

extension StudioEngine {
    nonisolated static let hostNames = ["Alex", "Sam"]
    /// Voices for Alex and Sam. They download once on first use.
    nonisolated static let linesPerSegment = 8

    /// How fast a line is spoken for the feeling it carries.
    nonisolated static func pace(for mood: String) -> Float {
        switch mood {
        case "excited": 1.12
        case "amused", "surprised": 1.07
        case "curious": 1.02
        case "thoughtful": 0.92
        case "serious": 0.95
        default: 1.0
        }
    }

    /// A two-host spoken overview of one notebook, made entirely on the phone: the answer model writes the
    /// conversation a segment at a time from different parts of the notebook, then Kokoro voices it in two voices
    /// into one audio file.
    func makeAudio(in notebook: LibraryNotebookInfo, topic: String, segments: Int, notebookOnly: Bool, style: AudioStyle, accent: AudioAccent, library: LibraryModel) async -> StudioItem? {
        guard !working else { return nil }
        working = true; error = nil; preview = ""; progress = 0; caption = ""; stopped = false
        defer { working = false; status = "" }
        guard let gemma = library.gemma else { error = "The answer model is still getting ready. Try again in a moment."; return nil }
        guard GPUGuard.canStart else { error = "Open the app and try again: this can't run while the screen is off."; return nil }
        UIApplication.shared.isIdleTimerDisabled = true
        defer { UIApplication.shared.isIdleTimerDisabled = false }

        let topic = topic.trimmingCharacters(in: .whitespacesAndNewlines)
        status = "Reading the notebook…"
        let t = topic.isEmpty ? "" : topic + " "
        var seeds = topic.isEmpty ? ["the main ideas and key points"] : [topic]
        seeds += ["\(t)important facts, names, dates and numbers", "\(t)definitions and explanations", "\(t)examples, processes and steps",
                  "\(t)conclusions, decisions and open questions", "\(t)people, organizations and relationships", "\(t)problems, risks and recommendations"]
        var pool: [LibraryHit] = []
        var seen = Set<String>()
        for seed in seeds {
            if let found = try? await library.retrieve(question: seed, notebook: notebook.id) {
                for hit in found.hits where seen.insert(hit.id).inserted { pool.append(hit) }
            }
        }
        pool = Array(pool.prefix(min(40, max(16, segments * 8))))
        guard !pool.isEmpty else { error = "Nothing in this notebook matches that yet."; return nil }

        // 1. The script, a segment at a time.
        let about = topic.isEmpty ? "the notebook “\(notebook.title)”" : "“\(topic)” in the notebook “\(notebook.title)”"
        let source = notebookOnly
            ? "Use ONLY facts from the numbered passages from the user's own notes."
            : "Use mainly the numbered passages from the user's own notes; you may add well-established general knowledge about the subject."
        let instructions = """
        You write the script of a podcast between two real, distinct people, Alex (A) and Sam (B). \(source) \
        \(style.direction) \
        Alex is curious and enthusiastic, asks the questions a smart listener would, and loves a good analogy. \
        Sam knows the material well, is warm and grounded, and has a dry sense of humor. \
        Make it sound spoken, never read: contractions, natural reactions ("Wait, really?", "Huh.", "Okay, that's wild."), \
        interruptions and quick back-and-forth, questions, the occasional laugh ("ha"), and varied sentence length. \
        Use punctuation to carry feeling: ! for energy, ? for curiosity, … for a thinking pause, a dash for a sudden turn. \
        Never sound like a newsreader: no "welcome to today's episode" formula, no lists, no summary voice, no markdown, \
        no citations or bracketed numbers, no stage directions or sound effects. Each line says one thing. You reply with JSON only.
        """
        var lines: [(host: Int, text: String, mood: String)] = []
        for k in 0..<segments {
            if stopped { error = "Stopped."; return nil }
            status = "Alex and Sam are writing the script… part \(k + 1) of \(segments)"
            let window = 8
            let start = (k * window) % pool.count
            let slice = (0..<min(window, pool.count)).map { pool[(start + $0) % pool.count] }
            let numbered = slice.enumerated().map { i, h in "[\(i + 1)] (\(h.filename))\n\(String(h.text.prefix(900)))" }.joined(separator: "\n\n")
            let place = k == 0 && segments == 1 ? "Open with a warm welcome, cover the points, and close with a short recap and goodbye."
                : k == 0 ? "This is the opening: welcome listeners and introduce the topic, then begin."
                : k == segments - 1 ? "This is the final part: finish the discussion, then give a short recap and a friendly goodbye."
                : "This is the middle of the conversation: carry on naturally. Do not greet or say goodbye."
            let before = lines.suffix(3).map { "\(Self.hostNames[$0.host]): \($0.text)" }.joined(separator: "\n")
            let continuity = before.isEmpty ? "" : "\n\nThe conversation so far ended with:\n\(before)\nDo not repeat what was already said."
            let prompt = """
            Passages:

            \(numbered)

            Write part \(k + 1) of \(segments) of a podcast conversation about \(about). \(place) \
            Reply with ONLY a JSON array of exactly \(Self.linesPerSegment) items, alternating hosts. Each item: {"host": "A" or "B", "mood": one of "excited", "amused", "curious", "surprised", "thoughtful", "serious", "warm", "text": one to three short spoken sentences, or just a quick reaction}.\(continuity)
            """
            guard let out = await generate(gemma, instructions: instructions, prompt: prompt, tokens: 1_100) else { error = "Stopped."; return nil }
            let got: [(host: Int, text: String, mood: String)] = Self.jsonObjects(in: out).compactMap { o in
                guard let text = (o["text"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else { return nil }
                let h = (o["host"] as? String)?.uppercased() ?? ""
                return (h.hasPrefix("B") || h.hasPrefix("SAM") ? 1 : 0, text, (o["mood"] as? String)?.lowercased() ?? "warm")
            }
            lines += got
            progress = 0.5 * Double(k + 1) / Double(segments)
        }
        guard lines.count >= 4 else { error = "The script didn't come out right. Try again."; return nil }

        // 2. The voices.
        status = "Warming up the microphones…"
        caption = ""
        // The voice engine is tried in two versions: "legacy" (what Read aloud uses) and "v3", which FluidAudio's
        // maintainers report running clean on iOS 27.2 (their issue #889). The audio is made with the app in front,
        // so v3's use of the GPU is fine here. Each failure is logged so the cause is visible.
        var engine: KokoroAneManager?
        var lastFailure = ""
        for version in [KokoroAneVersion.legacy, .v3] {
            let k = KokoroAneManager(version: version)
            do {
                do { try await k.initialize(preloadVoices: Set(accent.voices)) } catch { try await k.initialize() }
                _ = try await k.synthesizeDetailed(text: "Hello there.", voice: nil)
                engine = k
                SoakTrace.log("studio audio: voice engine \(version) ready")
                break
            } catch {
                lastFailure = error.localizedDescription
                SoakTrace.log("studio audio: voice engine \(version) failed its check: \(error)")
                await k.cleanup()
            }
        }
        guard let kokoro = engine else { self.error = "Couldn't start the voices: \(lastFailure)"; return nil }
        // Check each host's voice with a short phrase; one that fails falls back to the default voice (nil).
        status = "Checking the voices…"
        var voiceFor: [String?] = [accent.voices[0], accent.voices[1]]
        for i in 0..<2 {
            do { _ = try await kokoro.synthesizeDetailed(text: "Hello there.", voice: voiceFor[i]) }
            catch {
                SoakTrace.log("studio audio: voice \(voiceFor[i] ?? "default") failed its check: \(error)")
                voiceFor[i] = nil
            }
        }
        let id = UUID().uuidString
        let file = id + ".m4a"
        let url = StudioStore.audioURL(file)
        let rate = Double(KokoroAneConstants.sampleRate)
        var out: AVAudioFile? = try? AVAudioFile(forWriting: url, settings: [AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: rate,
                                                                    AVNumberOfChannelsKey: 1, AVEncoderBitRateKey: 56_000],
                                         commonFormat: .pcmFormatFloat32, interleaved: false)
        guard out != nil, let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: rate, channels: 1, interleaved: false) else {
            error = "Couldn't create the audio file."; return nil
        }
        func write(_ samples: [Float]) {
            guard !samples.isEmpty, let buf = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(samples.count)) else { return }
            buf.frameLength = AVAudioFrameCount(samples.count)
            samples.withUnsafeBufferPointer { buf.floatChannelData![0].update(from: $0.baseAddress!, count: samples.count) }
            try? out?.write(from: buf)
        }
        var written = 0
        var script: [AudioScript.Line] = []
        let gap = [Float](repeating: 0, count: Int(rate * 0.30))
        let shortGap = [Float](repeating: 0, count: Int(rate * 0.08))
        do {
            for (i, line) in lines.enumerated() {
                if stopped { try? FileManager.default.removeItem(at: url); error = "Stopped."; return nil }
                caption = line.text
                status = "\(Self.hostNames[line.host]) is recording… \(i + 1) of \(lines.count)"
                script.append(.init(host: line.host, text: line.text, start: Double(written) / rate))
                for part in SpeechText.chunks(from: line.text) {
                    let base: Float = voiceFor[0] == voiceFor[1] && line.host == 1 ? 1.06 : 1.0
                    let speed = base * Self.pace(for: line.mood)
                    let r: KokoroAneSynthesisResult
                    do { r = try await kokoro.synthesizeDetailed(text: part, voice: voiceFor[line.host], speed: speed) }
                    catch {
                        SoakTrace.log("studio audio: line \(i + 1) failed on \(voiceFor[line.host] ?? "default"): \(error)")
                        r = try await kokoro.synthesizeDetailed(text: part, voice: nil, speed: speed)               // one retry on the default voice
                    }
                    write(r.samples); written += r.samples.count
                }
                let pause = line.text.count < 28 ? shortGap : gap          // quick reactions come in right away
                write(pause); written += pause.count
                progress = 0.5 + 0.5 * Double(i + 1) / Double(lines.count)
            }
        } catch {
            try? FileManager.default.removeItem(at: url)
            self.error = "Couldn't voice the script: \(error.localizedDescription)"; return nil
        }
        await kokoro.cleanup()
        out = nil                                    // closing the writer is what completes the file
        let body = AudioScript(file: file, duration: Double(written) / rate, lines: script)
        guard let data = try? JSONEncoder().encode(body) else { return nil }
        let title = topic.isEmpty ? "Audio overview: \(notebook.title)" : "Audio overview: \(topic)"
        let item = StudioItem(id: id, notebookId: notebook.id, kind: .audio, title: title, created: Date(), body: String(decoding: data, as: UTF8.self))
        StudioStore.save(item)
        progress = 1
        return item
    }

    /// Streams one request to the answer model; nil if it was stopped. Updates `caption` with the latest spoken line.
    private func generate(_ gemma: ModelContainer, instructions: String, prompt: String, tokens: Int) async -> String? {
        preview = ""
        let session = ChatSession(gemma, instructions: instructions, generateParameters: GenerateParameters(maxTokens: tokens))
        let work = Task { @MainActor in
            for try await item in session.streamDetails(to: prompt) {
                if case .chunk(let s) = item {
                    self.preview += s
                    if let m = self.preview.range(of: #""text"\s*:\s*"([^"]*)$"#, options: .regularExpression) {
                        self.caption = String(self.preview[m]).replacingOccurrences(of: #"^"text"\s*:\s*""#, with: "", options: .regularExpression)
                    }
                }
            }
        }
        task = work
        let observer = GPUGuard.protect(work, label: "studio")
        do { try await work.value } catch {}
        NotificationCenter.default.removeObserver(observer)
        task = nil
        return work.isCancelled || stopped ? nil : preview.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

/// How the two hosts present: each style changes who leads, the energy, and how they relate.
enum AudioStyle: String, CaseIterable, Identifiable, Sendable {
    case casual, debate, interview, storyteller
    var id: String { rawValue }
    var label: String {
        switch self { case .casual: "Casual chat"; case .debate: "Friendly debate"; case .interview: "Interview"; case .storyteller: "Storyteller" }
    }
    var direction: String {
        switch self {
        case .casual: "Style: two friends who find this fascinating, chatting over coffee. Relaxed, funny, quick to react to each other."
        case .debate: "Style: a spirited but friendly debate. Alex takes the optimistic or favorable reading, Sam the skeptical or cautious one, both strictly based on what the material supports. They push back, concede points, and land somewhere real."
        case .interview: "Style: Alex interviews Sam, who is the expert on this material. Alex asks sharp, curious follow-ups and reacts honestly; Sam answers with stories, examples and clear explanations."
        case .storyteller: "Style: Alex tells the story of this material as a gripping narrative with scenes, stakes and a through-line; Sam reacts, asks what happened next, and adds the telling detail."
        }
    }
}

/// Which accent the two hosts speak with. Voices download once on first use; a voice that can't load falls back to the default.
enum AudioAccent: String, CaseIterable, Identifiable, Sendable {
    case british, american
    var id: String { rawValue }
    var label: String { self == .british ? "British" : "American" }
    /// Alex, then Sam.
    var voices: [String] { self == .british ? ["bf_emma", "bm_george"] : ["af_heart", "am_michael"] }
}
