import AVFoundation
import FluidAudio
import LocalBookCore
import LocalBookEngines
import Observation

/// Reads an answer aloud with the same on-device voice the phone uses: Kokoro's "legacy" pipeline on the Neural
/// Engine and CPU. Speech is produced a sentence group at a time and queued gaplessly, so sound starts after the first
/// group, not after the whole answer. The phone's `ReadAloud` without the audio session and lock-screen controls,
/// which the Mac does not have.
@MainActor @Observable
final class ReadAloud {
    enum State: Equatable { case idle, preparing, speaking, paused, failed(String) }

    private(set) var state: State = .idle
    /// 0…1 of the answer already played.
    private(set) var fraction = 0.0

    private var kokoro: KokoroAneManager?
    private var player: SpeechPlayer?
    private var task: Task<Void, Never>?

    var isActive: Bool { state == .speaking || state == .paused || state == .preparing }

    func toggle(_ answer: String) {
        switch state {
        case .speaking: pause()
        case .paused: resume()
        case .preparing: stop()
        case .idle, .failed: start(answer)
        }
    }

    func start(_ answer: String) {
        stop()
        let chunks = SpeechText.chunks(from: answer)
        guard !chunks.isEmpty else { return }
        state = .preparing
        fraction = 0
        task = Task { [weak self] in await self?.run(chunks) }
    }

    func pause() { player?.pause(); if state == .speaking { state = .paused } }
    func resume() { player?.resume(); if state == .paused { state = .speaking } }

    func stop() {
        task?.cancel(); task = nil
        player?.stop(); player = nil
        if state != .idle { state = .idle }
        fraction = 0
    }

    private func run(_ chunks: [String]) async {
        do {
            if kokoro == nil {
                let k = KokoroAneManager(version: .legacy)
                try await k.initialize()
                kokoro = k
            }
            guard let kokoro, !Task.isCancelled else { return }
            let voice = ReadAloudVoice.current.id, speed = ReadAloudVoice.speed
            let player = SpeechPlayer(sampleRate: Double(KokoroAneConstants.sampleRate))
            self.player = player
            try player.start()
            let total = chunks.count
            player.onProgress = { [weak self] played in
                Task { @MainActor [weak self] in
                    guard let self, self.player === player else { return }
                    self.fraction = Double(played) / Double(total)
                    if played >= total { self.finish() }
                }
            }
            for (i, text) in chunks.enumerated() {
                if Task.isCancelled { return }
                let result = try await kokoro.synthesizeDetailed(text: text, voice: voice, speed: speed)
                if Task.isCancelled { return }
                player.schedule(result.samples)
                if i == 0 { state = .speaking }
            }
        } catch is CancellationError {
        } catch {
            state = .failed("Couldn't read this aloud: \(error.localizedDescription)")
            player?.stop(); player = nil
        }
    }

    private func finish() {
        player?.stop(); player = nil
        state = .idle
        fraction = 0
    }
}

/// Plays 24 kHz mono chunks gaplessly and reports how many have finished.
final class SpeechPlayer: @unchecked Sendable {
    private let engine = AVAudioEngine()
    private let node = AVAudioPlayerNode()
    private let format: AVAudioFormat
    private let lock = NSLock()
    private var _played = 0
    var onProgress: (@Sendable (Int) -> Void)?

    init(sampleRate: Double) {
        format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: sampleRate, channels: 1, interleaved: false)!
    }

    func start() throws {
        engine.attach(node)
        engine.connect(node, to: engine.mainMixerNode, format: format)
        try engine.start()
        node.play()
    }

    func schedule(_ samples: [Float]) {
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(samples.count)) else { return }
        buffer.frameLength = AVAudioFrameCount(samples.count)
        samples.withUnsafeBufferPointer { buffer.floatChannelData![0].update(from: $0.baseAddress!, count: samples.count) }
        node.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            guard let self else { return }
            let n = self.lock.withLock { self._played += 1; return self._played }
            self.onProgress?(n)
        }
    }

    func pause() { node.pause() }
    func resume() { node.play() }

    func stop() {
        node.stop()
        engine.stop()
    }
}
