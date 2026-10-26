import AVFoundation
import PhotosUI
import Speech
import SwiftUI
import UIKit
import UniformTypeIdentifiers

/// Turns an audio file or a video into text on this phone, with the same on-device recognizer Dictate uses (Apple's,
/// so there is no extra model to download or keep in memory). Nothing leaves the phone where the language supports it.
@MainActor @Observable
final class MediaTranscriber {
    static let shared = MediaTranscriber()

    private(set) var busy = false
    private(set) var status = ""
    /// 0…1 of the way through the recording.
    private(set) var progress = 0.0
    private var task: SFSpeechRecognitionTask?

    struct Failure: Error, LocalizedError { let text: String; var errorDescription: String? { text } }

    func cancel() { task?.cancel() }

    /// The words spoken in the file. Works for audio files and for videos (their sound track is used).
    func transcribe(_ source: URL) async throws -> String {
        guard !busy else { throw Failure(text: "Already transcribing something.") }
        busy = true; progress = 0; status = "Getting ready…"
        UIApplication.shared.isIdleTimerDisabled = true
        defer { busy = false; status = ""; task = nil; UIApplication.shared.isIdleTimerDisabled = false }

        let allowed = await SpeechPermission.granted()
        guard allowed else { throw Failure(text: "Allow Speech Recognition for LocalBook in Settings to transcribe.") }
        guard let recognizer = SFSpeechRecognizer(locale: Locale.current), recognizer.isAvailable else { throw Failure(text: "Transcription isn't available right now.") }

        let asset = AVURLAsset(url: source)
        guard try await !asset.loadTracks(withMediaType: .audio).isEmpty else { throw Failure(text: "There is no sound in that file.") }
        let total = max(1, try await asset.load(.duration).seconds)

        var audioURL = source
        var extracted: URL?
        defer { if let extracted { try? FileManager.default.removeItem(at: extracted) } }
        if ["mov", "mp4", "m4v"].contains(source.pathExtension.lowercased()) {
            status = "Taking the sound from the video…"
            let temp = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".m4a")
            guard let export = AVAssetExportSession(asset: asset, presetName: AVAssetExportPresetAppleM4A) else { throw Failure(text: "Couldn't read the video's sound.") }
            try await export.export(to: temp, as: .m4a)
            audioURL = temp; extracted = temp
        }

        status = "Transcribing…"
        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.requiresOnDeviceRecognition = recognizer.supportsOnDeviceRecognition      // stays on the phone when it can
        let once = Once()
        return try await withCheckedThrowingContinuation { (cont: CheckedContinuation<String, Error>) in
            SpeechFeed.start(audioURL, into: request)
            task = recognizer.recognitionTask(with: request) { result, error in
                if let result {
                    let heard = result.bestTranscription
                    let end = (heard.segments.last).map { $0.timestamp + $0.duration } ?? 0
                    Task { @MainActor in MediaTranscriber.shared.progress = min(1, end / total) }
                    if result.isFinal, once.first() { cont.resume(returning: heard.formattedString) }
                } else if let error, once.first() {
                    cont.resume(throwing: Failure(text: (error as NSError).code == 301 ? "Stopped." : error.localizedDescription))
                }
            }
        }
    }

    private final class Once: @unchecked Sendable {
        private let lock = NSLock()
        private var done = false
        /// True the first time only.
        func first() -> Bool { lock.lock(); defer { lock.unlock() }; if done { return false }; done = true; return true }
    }
}

/// A video picked from Photos, copied to a temporary file.
struct PickedMovie: Transferable {
    let url: URL
    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(contentType: .movie) { movie in
            SentTransferredFile(movie.url)
        } importing: { received in
            let ext = received.file.pathExtension.isEmpty ? "mov" : received.file.pathExtension
            let dest = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + "." + ext)
            try FileManager.default.copyItem(at: received.file, to: dest)
            return PickedMovie(url: dest)
        }
    }
}

/// Feeds a recorded file to a speech request the way live audio arrives (the path Dictate uses and is known to work),
/// instead of handing the recognizer a file URL.
enum SpeechFeed {
    private struct Box: @unchecked Sendable { let request: SFSpeechAudioBufferRecognitionRequest }

    static func start(_ url: URL, into request: SFSpeechAudioBufferRecognitionRequest) {
        let box = Box(request: request)
        Task.detached(priority: .userInitiated) {
            do {
                let file = try AVAudioFile(forReading: url)
                let format = file.processingFormat
                SoakTrace.log("speech: feeding \(file.length) frames at \(Int(format.sampleRate)) Hz, \(format.channelCount) ch")
                while file.framePosition < file.length {
                    guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 16_384) else { break }
                    try file.read(into: buffer)
                    if buffer.frameLength == 0 { break }
                    box.request.append(buffer)
                }
            } catch { SoakTrace.log("speech: couldn't read the file: \(error)") }
            box.request.endAudio()
        }
    }
}
