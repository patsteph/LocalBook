import AVFoundation
import FluidAudio
import MediaPlayer
import Observation

/// Reads an answer aloud with the same on-device voice that was proven in the Lab with the screen locked: Kokoro's
/// "legacy" pipeline, which keeps every stage on the Neural Engine or the CPU (the GPU variant stops the moment the
/// screen locks). Speech is produced a sentence group at a time and queued gaplessly, so sound starts after the first
/// group, not after the whole answer.
@MainActor
@Observable
final class ReadAloud {
    enum State: Equatable { case idle, preparing, speaking, paused, failed(String) }

    private(set) var state: State = .idle
    /// 0…1 of the answer already played.
    private(set) var fraction = 0.0

    private var kokoro: KokoroAneManager?
    private var player: SpeechPlayer?
    private var task: Task<Void, Never>?
    private var remoteCommandsInstalled = false

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

    func pause() { player?.pause(); if state == .speaking { state = .paused; updateNowPlaying(rate: 0) } }
    func resume() { player?.resume(); if state == .paused { state = .speaking; updateNowPlaying(rate: 1) } }

    func stop() {
        task?.cancel(); task = nil
        let p = player; player = nil
        Task { await p?.stop() }
        if state != .idle { state = .idle }
        fraction = 0
        MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
    }

    // MARK: producing

    private func run(_ chunks: [String]) async {
        do {
            if kokoro == nil {
                let k = KokoroAneManager(version: .legacy)
                try await k.initialize()
                kokoro = k
            }
            guard let kokoro, !Task.isCancelled else { return }
            let player = SpeechPlayer(sampleRate: Double(KokoroAneConstants.sampleRate))
            self.player = player
            try await player.start()
            installRemoteCommands()
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
                let result = try await kokoro.synthesizeDetailed(text: text)
                if Task.isCancelled { return }
                player.schedule(result.samples)
                if i == 0 { state = .speaking; updateNowPlaying(rate: 1) }
            }
            player.markAllScheduled()
        } catch is CancellationError {
        } catch {
            state = .failed("Couldn't read this aloud: \(error.localizedDescription)")
            let p = player; player = nil
            await p?.stop()
        }
    }

    private func finish() {
        let p = player; player = nil
        Task { await p?.stop() }
        state = .idle
        fraction = 0
        MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
    }

    // MARK: lock-screen controls

    private func installRemoteCommands() {
        guard !remoteCommandsInstalled else { return }
        remoteCommandsInstalled = true
        let c = MPRemoteCommandCenter.shared()
        c.playCommand.addTarget { [weak self] _ in Task { @MainActor in self?.resume() }; return .success }
        c.pauseCommand.addTarget { [weak self] _ in Task { @MainActor in self?.pause() }; return .success }
        c.togglePlayPauseCommand.addTarget { [weak self] _ in
            Task { @MainActor in if self?.state == .paused { self?.resume() } else { self?.pause() } }; return .success
        }
        NotificationCenter.default.addObserver(forName: AVAudioSession.interruptionNotification, object: nil, queue: .main) { [weak self] note in
            guard let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
                  AVAudioSession.InterruptionType(rawValue: raw) == .began else { return }
            Task { @MainActor in self?.pause() }
        }
    }

    private func updateNowPlaying(rate: Double) {
        MPNowPlayingInfoCenter.default().nowPlayingInfo = [
            MPMediaItemPropertyTitle: "LocalBook answer", MPMediaItemPropertyArtist: "Read aloud",
            MPNowPlayingInfoPropertyPlaybackRate: rate,
        ]
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

    func start() async throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playback, mode: .spokenAudio)
        _ = try await session.activate(options: [])
        engine.attach(node)
        try engine.connectNode(node, to: engine.mainMixerNode, format: format)
        try engine.start()
        try node.playAudio()
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

    func markAllScheduled() {}
    func pause() { node.pause() }
    func resume() { try? node.playAudio() }

    func stop() async {
        node.stop()
        engine.stop()
        _ = try? await AVAudioSession.sharedInstance().deactivate(options: .notifyOthersOnDeactivation)
    }
}

/// An answer as it should be SPOKEN: no markdown, no citation brackets, in sentence groups the voice can take.
enum SpeechText {
    static func chunks(from answer: String, maxCharacters: Int = 220) -> [String] {
        var lines: [String] = []
        for raw in answer.split(separator: "\n", omittingEmptySubsequences: true) {
            var t = String(raw)
            t = t.replacingOccurrences(of: #"\[\d+(?:\s*,\s*\d+)*\]"#, with: "", options: .regularExpression)     // [1], [2, 3]
            t = t.replacingOccurrences(of: #"^\s*#{1,6}\s*"#, with: "", options: .regularExpression)             // headings
            t = t.replacingOccurrences(of: #"^\s*([\*\-•]|\d+\.)\s+"#, with: "", options: .regularExpression)     // bullets
            t = t.replacingOccurrences(of: #"[\*_`]+"#, with: "", options: .regularExpression)                    // emphasis
            t = t.replacingOccurrences(of: " & ", with: " and ")
            t = t.replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
            t = t.replacingOccurrences(of: #"\s+([.,;:!?])"#, with: "$1", options: .regularExpression).trimmingCharacters(in: .whitespaces)
            if t.count > 1 { lines.append(t.last.map { ".!?:".contains($0) } == true ? t : t + ".") }
        }
        // sentences, then group up to maxCharacters
        var sentences: [String] = []
        for line in lines {
            line.enumerateSubstrings(in: line.startIndex..., options: .bySentences) { s, _, _, _ in
                if let s { sentences.append(s.trimmingCharacters(in: .whitespaces)) }
            }
        }
        var out: [String] = []
        var group = ""
        for s in sentences where !s.isEmpty {
            if !group.isEmpty && group.count + s.count + 1 > maxCharacters { out.append(group); group = s }
            else { group = group.isEmpty ? s : group + " " + s }
        }
        if !group.isEmpty { out.append(group) }
        return out
    }
}
