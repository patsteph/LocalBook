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
    fileprivate(set) var status = ""
    /// 0…1 of the way through the recording.
    fileprivate(set) var progress = 0.0
    private var stopper = LongTranscriber.Stopper()

    struct Failure: Error, LocalizedError { let text: String; var errorDescription: String? { text } }

    func cancel() { stopper.stop() }

    /// The words spoken in the file. Works for audio files and for videos (their sound track is used).
    func transcribe(_ source: URL) async throws -> String {
        guard !busy else { throw Failure(text: "Already transcribing something.") }
        busy = true; progress = 0; status = "Getting ready…"; stopper = LongTranscriber.Stopper()
        UIApplication.shared.isIdleTimerDisabled = true
        defer { busy = false; status = ""; UIApplication.shared.isIdleTimerDisabled = false }

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
        let stop = stopper
        let words = try await LongTranscriber.transcribe(audioURL, total: total, stopper: stop,
            step: { text in Task { @MainActor in MediaTranscriber.shared.status = text } },
            progress: { fraction in Task { @MainActor in MediaTranscriber.shared.progress = fraction } })
        var text = ""
        for w in words { text = LongTranscriber.join(text, w.text) }
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
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
