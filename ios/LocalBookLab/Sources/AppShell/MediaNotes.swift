import AVFoundation
import FluidAudio
import PhotosUI
import SwiftUI
import UIKit
import UniformTypeIdentifiers

/// Turns a voice memo, an audio file or a video into text on this phone (Parakeet, on the Neural Engine).
/// Nothing leaves the phone. The model downloads once and is released again afterwards to leave room for the answer model.
@MainActor @Observable
final class MediaTranscriber {
    static let shared = MediaTranscriber()

    private(set) var busy = false
    private(set) var status = ""
    /// 0…1 of the way through the recording.
    private(set) var progress = 0.0
    private var cancelled = false

    struct Failure: Error, LocalizedError { let text: String; var errorDescription: String? { text } }

    func cancel() { cancelled = true }

    /// The words spoken in the file. Works for audio files and for videos (their sound track is used).
    func transcribe(_ source: URL) async throws -> String {
        guard !busy else { throw Failure(text: "Already transcribing something.") }
        busy = true; cancelled = false; progress = 0; status = "Getting ready…"
        UIApplication.shared.isIdleTimerDisabled = true
        defer { busy = false; status = ""; UIApplication.shared.isIdleTimerDisabled = false }

        var audioURL = source
        var extracted: URL?
        defer { if let extracted { try? FileManager.default.removeItem(at: extracted) } }
        if ["mov", "mp4", "m4v"].contains(source.pathExtension.lowercased()) {
            status = "Taking the sound from the video…"
            let asset = AVURLAsset(url: source)
            guard try await !asset.loadTracks(withMediaType: .audio).isEmpty else { throw Failure(text: "This video has no sound to transcribe.") }
            let temp = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".m4a")
            guard let export = AVAssetExportSession(asset: asset, presetName: AVAssetExportPresetAppleM4A) else { throw Failure(text: "Couldn't read the video's sound.") }
            try await export.export(to: temp, as: .m4a)
            audioURL = temp; extracted = temp
        }

        status = "Getting the transcription model (the first time takes a moment)…"
        let models = try await AsrModels.downloadAndLoad(version: .v3)
        let asr = AsrManager(config: .default)
        try await asr.loadModels(models)

        status = "Transcribing…"
        let flag = CancelBox()
        let text = try await withTaskCancellationHandler {
            try await Self.run(audioURL, asr: asr, flag: flag) { fraction in
                Task { @MainActor in
                    MediaTranscriber.shared.progress = fraction
                    if MediaTranscriber.shared.cancelled { flag.set() }
                }
            }
        } onCancel: { flag.set() }
        if cancelled { throw Failure(text: "Stopped.") }
        return text
    }

    final class CancelBox: @unchecked Sendable {
        private let lock = NSLock()
        private var value = false
        func set() { lock.lock(); value = true; lock.unlock() }
        var isSet: Bool { lock.lock(); defer { lock.unlock() }; return value }
    }

    /// Reads the file in 30-second windows at 16 kHz mono and transcribes each, so a long recording never sits in memory whole.
    @concurrent private static func run(_ url: URL, asr: AsrManager, flag: CancelBox, report: @escaping @Sendable (Double) -> Void) async throws -> String {
        let file = try AVAudioFile(forReading: url)
        let inFormat = file.processingFormat
        guard let outFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16_000, channels: 1, interleaved: false),
              let converter = AVAudioConverter(from: inFormat, to: outFormat),
              let outBuffer = AVAudioPCMBuffer(pcmFormat: outFormat, frameCapacity: 16_000 * 30),
              let inBuffer = AVAudioPCMBuffer(pcmFormat: inFormat, frameCapacity: 32_768)
        else { throw Failure(text: "Couldn't read this audio.") }
        let totalFrames = max(1, file.length)
        var pieces: [String] = []
        var finished = false
        while !finished && !flag.isSet {
            var failure: NSError?
            let status = converter.convert(to: outBuffer, error: &failure) { _, inputStatus in
                do { try file.read(into: inBuffer, frameCount: 32_768) } catch { inBuffer.frameLength = 0 }
                if inBuffer.frameLength == 0 { inputStatus.pointee = .endOfStream; return nil }
                inputStatus.pointee = .haveData
                return inBuffer
            }
            if status == .error { throw Failure(text: failure?.localizedDescription ?? "Couldn't read this audio.") }
            if status == .endOfStream { finished = true }
            let count = Int(outBuffer.frameLength)
            if count > 16_000 / 2, let channel = outBuffer.floatChannelData?[0] {          // ignore slivers under half a second
                let samples = Array(UnsafeBufferPointer(start: channel, count: count))
                var decoder = TdtDecoderState.make()
                let result = try await asr.transcribe(samples, decoderState: &decoder)
                let text = result.text.trimmingCharacters(in: .whitespacesAndNewlines)
                if !text.isEmpty { pieces.append(text) }
            }
            report(min(1, Double(file.framePosition) / Double(totalFrames)))
        }
        return pieces.joined(separator: " ")
    }
}

/// Records a voice memo to a temporary file; the note keeps the words, not the audio.
@MainActor @Observable
final class VoiceMemo {
    private(set) var recording = false
    private(set) var elapsed = 0.0
    private(set) var failure: String?
    private var recorder: AVAudioRecorder?
    private var ticker: Task<Void, Never>?
    private var url: URL?

    @concurrent private static func prepare() async {
        let session = AVAudioSession.sharedInstance()
        try? session.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker])
        _ = try? await session.activate(options: [])
    }

    func start() async {
        failure = nil
        guard await AVAudioApplication.requestRecordPermission() else { failure = "Allow the microphone for LocalBook in Settings to record."; return }
        await Self.prepare()
        let file = FileManager.default.temporaryDirectory.appendingPathComponent("memo-" + UUID().uuidString + ".m4a")
        do {
            let r = try AVAudioRecorder(url: file, settings: [AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: 16_000,
                                                              AVNumberOfChannelsKey: 1, AVEncoderBitRateKey: 48_000])
            guard r.record() else { failure = "Couldn't start recording."; return }
            recorder = r; url = file; recording = true; elapsed = 0
            ticker = Task { [weak self] in
                while !Task.isCancelled {
                    try? await Task.sleep(for: .milliseconds(250))
                    guard let self, let r = self.recorder else { return }
                    self.elapsed = r.currentTime
                }
            }
        } catch { failure = "Couldn't start recording: \(error.localizedDescription)" }
    }

    /// Stops and returns the recording, or nil if there was none.
    func stop() -> URL? {
        ticker?.cancel(); ticker = nil
        recorder?.stop(); recorder = nil
        recording = false
        defer { url = nil }
        return url
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
