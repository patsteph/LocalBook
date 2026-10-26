import AVFoundation
import Foundation
import Speech

/// Words from a recording of any length, on this phone, with the time each was said.
/// Uses Apple's SpeechAnalyzer (iOS 26+), which is built for long recordings such as meetings and lectures.
/// The older SFSpeechRecognizer drops most of a long recording; a 10 minute call came back as its last few sentences.
enum LongTranscriber {
    struct Word: Sendable { var start: Double; var end: Double; var text: String }

    /// Set from anywhere to end a transcription early.
    final class Stopper: @unchecked Sendable {
        private let lock = NSLock()
        private var stopped = false
        func stop() { lock.lock(); stopped = true; lock.unlock() }
        var isStopped: Bool { lock.lock(); defer { lock.unlock() }; return stopped }
    }

    struct Failure: Error, LocalizedError { let text: String; var errorDescription: String? { text } }

    /// `progress` gets 0…1 as words arrive. `step` gets a short note when the speech model must be downloaded first.
    static func transcribe(_ url: URL, total: Double, stopper: Stopper,
                           step: @escaping @Sendable (String) -> Void,
                           progress: @escaping @Sendable (Double) -> Void) async throws -> [Word] {
        guard let locale = await SpeechTranscriber.supportedLocale(equivalentTo: Locale.current) else {
            throw Failure(text: "Transcription isn't available in this language on this phone.")
        }
        SoakTrace.log("transcribe: locale \(locale.identifier), \(Int(total)) s")
        let transcriber = SpeechTranscriber(locale: locale, transcriptionOptions: [], reportingOptions: [], attributeOptions: [.audioTimeRange])
        if let install = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
            step("Downloading the speech model (once)…")
            SoakTrace.log("transcribe: downloading speech model")
            try await install.downloadAndInstall()
            step("Writing down the words…")
        }

        // Results arrive while the file is being read; collect them as they come.
        let collector = Task { () -> [Word] in
            var words: [Word] = []
            for try await result in transcriber.results {
                for run in result.text.runs {
                    let piece = String(result.text[run.range].characters)
                    if let range = run.audioTimeRange {
                        let start = range.start.seconds, end = range.end.seconds
                        words.append(Word(start: start, end: end.isFinite ? end : start, text: piece))
                        progress(min(1, end / max(1, total)))
                    } else if !words.isEmpty {
                        words[words.count - 1].text += piece          // spacing or punctuation belongs to the word before it
                    }
                }
            }
            return words
        }

        let analyzer = SpeechAnalyzer(modules: [transcriber])
        let watcher = Task {                                             // honours Stop within a second
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(1))
                if stopper.isStopped { await analyzer.cancelAndFinishNow(); return }
            }
        }
        defer { watcher.cancel() }
        do {
            let file = try AVAudioFile(forReading: url)
            if let last = try await analyzer.analyzeSequence(from: file) {
                try await analyzer.finalizeAndFinish(through: last)
            } else {
                await analyzer.cancelAndFinishNow()
            }
        } catch {
            collector.cancel()
            if stopper.isStopped { throw Failure(text: "Stopped. The recording is still on your phone.") }
            throw error
        }
        let words = (try? await collector.value) ?? []
        if stopper.isStopped { throw Failure(text: "Stopped. The recording is still on your phone.") }
        SoakTrace.log("transcribe: \(words.count) words, last at \(Int(words.last?.end ?? 0)) s")
        return words
    }

    /// Joins two pieces of speech with a space only where one is needed.
    static func join(_ a: String, _ b: String) -> String {
        guard let l = a.last, let f = b.first else { return a + b }
        if l.isWhitespace || f.isWhitespace || ",.;:!?)’'".contains(f) { return a + b }
        return a + " " + b
    }
}
