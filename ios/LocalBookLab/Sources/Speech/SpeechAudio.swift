import AVFoundation
import Foundation

/// Plays 24 kHz mono chunks gaplessly and reports how far ahead of the speaker the producer is.
/// `lead` is the seconds of audio still queued when a chunk arrives: zero or less means the
/// listener heard silence waiting for it (an underrun).
final class ChunkPlayer: @unchecked Sendable {
    private let engine = AVAudioEngine()
    private let node = AVAudioPlayerNode()
    private let format: AVAudioFormat
    private let lock = NSLock()
    private var queuedSeconds = 0.0
    private var lastScheduled: Date?

    init(sampleRate: Double) {
        format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: sampleRate, channels: 1, interleaved: false)!
    }

    func start() throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playback, mode: .spokenAudio)
        try session.setActive(true)
        engine.attach(node)
        engine.connect(node, to: engine.mainMixerNode, format: format)
        try engine.start()
        node.play()
    }

    /// Queue a chunk; returns the seconds of audio that were still waiting when it arrived.
    @discardableResult
    func schedule(_ samples: [Float]) -> Double {
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(samples.count)) else { return 0 }
        buffer.frameLength = AVAudioFrameCount(samples.count)
        samples.withUnsafeBufferPointer { buffer.floatChannelData![0].update(from: $0.baseAddress!, count: samples.count) }
        let seconds = Double(samples.count) / format.sampleRate
        let lead: Double = lock.withLock {
            // Time that has passed since the last chunk was queued has been spent playing it.
            if let last = lastScheduled { queuedSeconds = max(0, queuedSeconds - Date().timeIntervalSince(last)) }
            let before = queuedSeconds
            queuedSeconds += seconds
            lastScheduled = Date()
            return before
        }
        node.scheduleBuffer(buffer, completionHandler: nil)
        return lead
    }

    /// Seconds of audio not yet played.
    var remaining: Double {
        lock.withLock {
            guard let last = lastScheduled else { return 0 }
            return max(0, queuedSeconds - Date().timeIntervalSince(last))
        }
    }

    func stop() {
        node.stop()
        engine.stop()
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}

enum SpeechMath {
    /// 24 kHz → 16 kHz mono, the rate the transcriber wants.
    static func resampleTo16k(_ samples: [Float], from rate: Int) -> [Float] {
        guard rate != 16_000, !samples.isEmpty else { return samples }
        let src = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: Double(rate), channels: 1, interleaved: false)!
        let dst = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16_000, channels: 1, interleaved: false)!
        guard let converter = AVAudioConverter(from: src, to: dst),
              let input = AVAudioPCMBuffer(pcmFormat: src, frameCapacity: AVAudioFrameCount(samples.count)) else { return samples }
        input.frameLength = AVAudioFrameCount(samples.count)
        samples.withUnsafeBufferPointer { input.floatChannelData![0].update(from: $0.baseAddress!, count: samples.count) }
        let outCapacity = AVAudioFrameCount(Double(samples.count) * 16_000 / Double(rate)) + 64
        guard let output = AVAudioPCMBuffer(pcmFormat: dst, frameCapacity: outCapacity) else { return samples }
        var consumed = false
        var error: NSError?
        converter.convert(to: output, error: &error) { _, status in
            if consumed { status.pointee = .endOfStream; return nil }
            consumed = true
            status.pointee = .haveData
            return input
        }
        guard error == nil else { return samples }
        return Array(UnsafeBufferPointer(start: output.floatChannelData![0], count: Int(output.frameLength)))
    }

    /// Word error rate of `heard` against `reference` (lowercase, letters and apostrophes only).
    static func wordErrorRate(reference: String, heard: String) -> Double {
        func words(_ s: String) -> [Substring] {
            s.lowercased().map { $0.isLetter || $0 == "'" ? $0 : " " }.split(separator: " ").map { Substring(String($0)) }
        }
        let r = words(reference), h = words(heard)
        guard !r.isEmpty else { return h.isEmpty ? 0 : 1 }
        var prev = Array(0...h.count)
        for i in 1...r.count {
            var cur = [i] + Array(repeating: 0, count: h.count)
            for j in 1...max(h.count, 1) where h.count > 0 {
                cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r[i - 1] == h[j - 1] ? 0 : 1))
            }
            prev = cur
        }
        return Double(prev[h.count]) / Double(r.count)
    }
}
