import AVFoundation
import MediaPlayer
import Observation
import SwiftUI

/// Plays a phone-made audio overview, with lock-screen and Control Center controls, and tells the transcript which
/// line is being spoken.
@MainActor @Observable
final class AudioOverviewPlayer {
    private(set) var playing = false
    private(set) var time = 0.0
    private(set) var duration = 0.0
    private(set) var current = 0

    private var player: AVAudioPlayer?
    private var script: AudioScript?
    private var title = ""
    private var ticker: Task<Void, Never>?
    private var tokens: [(MPRemoteCommand, Any)] = []

    func load(_ script: AudioScript, title: String) {
        stop()
        self.script = script; self.title = title
        guard let p = try? AVAudioPlayer(contentsOf: StudioStore.audioURL(script.file)) else { return }
        p.prepareToPlay()
        player = p; duration = p.duration; time = 0; current = 0
    }

    func toggle() { playing ? pause() : play() }

    func play() {
        guard let player else { return }
        let session = AVAudioSession.sharedInstance()
        try? session.setCategory(.playback, mode: .spokenAudio)
        try? session.setActive(true)
        installCommands()
        if time >= duration - 0.2 { player.currentTime = 0 }
        player.play(); playing = true
        ticker?.cancel()
        ticker = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(for: .milliseconds(250))
                self?.tick()
            }
        }
        nowPlaying()
    }

    func pause() { player?.pause(); playing = false; nowPlaying() }

    func seek(to t: Double) {
        guard let player else { return }
        player.currentTime = max(0, min(duration, t)); tick()
    }

    func skip(_ seconds: Double) { seek(to: time + seconds) }

    func stop() {
        ticker?.cancel(); ticker = nil
        player?.stop(); player = nil; playing = false
        for (command, token) in tokens { command.removeTarget(token) }
        tokens = []
        MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    private func tick() {
        guard let player else { return }
        time = player.currentTime
        if let script, let i = script.lines.lastIndex(where: { $0.start <= time + 0.05 }) { current = i }
        if playing && !player.isPlaying { playing = false; nowPlaying() }       // reached the end
    }

    private func nowPlaying() {
        MPNowPlayingInfoCenter.default().nowPlayingInfo = [
            MPMediaItemPropertyTitle: title, MPMediaItemPropertyArtist: "LocalBook",
            MPMediaItemPropertyPlaybackDuration: duration, MPNowPlayingInfoPropertyElapsedPlaybackTime: time,
            MPNowPlayingInfoPropertyPlaybackRate: playing ? 1.0 : 0.0,
        ]
    }

    private func installCommands() {
        guard tokens.isEmpty else { return }
        let c = MPRemoteCommandCenter.shared()
        c.skipForwardCommand.preferredIntervals = [15]; c.skipBackwardCommand.preferredIntervals = [15]
        c.skipForwardCommand.isEnabled = true; c.skipBackwardCommand.isEnabled = true; c.changePlaybackPositionCommand.isEnabled = true
        tokens = [
            (c.playCommand, c.playCommand.addTarget { [weak self] _ in Task { @MainActor in self?.play() }; return .success }),
            (c.pauseCommand, c.pauseCommand.addTarget { [weak self] _ in Task { @MainActor in self?.pause() }; return .success }),
            (c.skipForwardCommand, c.skipForwardCommand.addTarget { [weak self] _ in Task { @MainActor in self?.skip(15) }; return .success }),
            (c.skipBackwardCommand, c.skipBackwardCommand.addTarget { [weak self] _ in Task { @MainActor in self?.skip(-15) }; return .success }),
            (c.changePlaybackPositionCommand, c.changePlaybackPositionCommand.addTarget { [weak self] event in
                if let e = event as? MPChangePlaybackPositionCommandEvent { let t = e.positionTime; Task { @MainActor in self?.seek(to: t) } }
                return .success
            }),
        ]
    }
}

/// The audio overview screen: a transcript that follows the voices, and playback controls.
struct AudioOverviewView: View {
    let script: AudioScript
    let title: String
    @State private var player = AudioOverviewPlayer()

    private func clock(_ t: Double) -> String { String(format: "%d:%02d", Int(t) / 60, Int(t) % 60) }

    var body: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 14) {
                        ForEach(script.lines.indices, id: \.self) { i in
                            let line = script.lines[i]
                            VStack(alignment: .leading, spacing: 2) {
                                Text(StudioEngine.hostNames[line.host]).font(.caption.bold()).foregroundStyle(line.host == 0 ? Color.orange : Color.teal)
                                Text(line.text).font(.body)
                            }
                            .opacity(i == player.current ? 1 : 0.5)
                            .id(i)
                            .onTapGesture { player.seek(to: line.start); if !player.playing { player.play() } }
                        }
                    }.padding()
                }
                .onChange(of: player.current) { _, i in withAnimation { proxy.scrollTo(i, anchor: .center) } }
            }
            VStack(spacing: 10) {
                Slider(value: Binding(get: { player.time }, set: { player.seek(to: $0) }), in: 0...max(1, player.duration))
                HStack { Text(clock(player.time)); Spacer(); Text(clock(player.duration)) }.font(.caption).monospacedDigit().foregroundStyle(.secondary)
                HStack(spacing: 36) {
                    Button { player.skip(-15) } label: { Image(systemName: "gobackward.15").font(.title2) }
                    Button { player.toggle() } label: { Image(systemName: player.playing ? "pause.circle.fill" : "play.circle.fill").font(.system(size: 56)) }
                    Button { player.skip(15) } label: { Image(systemName: "goforward.15").font(.title2) }
                }
            }
            .padding().background(.bar)
        }
        .onAppear { player.load(script, title: title) }
        .onDisappear { player.stop() }
    }
}

/// The "making it" screen: a lively animated visual with playful progress lines, shown while any Studio item is made.
struct MakingView: View {
    let kind: StudioItem.Kind
    let status: String
    let progress: Double
    let caption: String
    let stop: () -> Void

    private var quips: [String] {
        switch kind {
        case .audio: ["Tuning the microphones…", "Alex is clearing their throat…", "Sam is sipping coffee…", "Sharpening the banter…", "Rolling tape…"]
        case .quiz: ["Sharpening pencils…", "Hiding the answers…", "Thinking up tricky ones…", "Checking it twice…"]
        case .cards: ["Shuffling the deck…", "Writing both sides…", "Trimming the corners…"]
        case .visual: ["Sketching the layout…", "Picking colors…", "Connecting the dots…", "Tidying the lines…"]
        case .doc: ["Gathering the best bits…", "Finding the right words…", "Adding the headings…", "Polishing…"]
        }
    }

    var body: some View {
        VStack(spacing: 16) {
            TimelineView(.animation) { ctx in
                let t = ctx.date.timeIntervalSinceReferenceDate
                HStack(alignment: .center, spacing: 5) {
                    ForEach(0..<19, id: \.self) { i in
                        let phase = t * 3 + Double(i) * 0.55
                        let h = 14 + 46 * abs(sin(phase) * cos(phase * 0.43 + Double(i)))
                        Capsule()
                            .fill(LinearGradient(colors: [.orange, .pink, .purple], startPoint: .bottom, endPoint: .top))
                            .frame(width: 7, height: h)
                    }
                }
                .frame(height: 72)
                .overlay(alignment: .top) {
                    Image(systemName: kind.symbol).font(.title).foregroundStyle(.tint)
                        .offset(y: -34 + 5 * sin(t * 2))
                }
            }
            .padding(.top, 30)

            TimelineView(.periodic(from: .now, by: 3)) { ctx in
                let i = Int(ctx.date.timeIntervalSinceReferenceDate / 3) % max(1, quips.count)
                Text(quips[i]).font(.headline).id(i).transition(.opacity)
            }
            Text(status).font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center)
            ProgressView(value: min(1, max(0.02, progress))).tint(.pink).padding(.horizontal, 24)
            if !caption.isEmpty {
                Text("“\(caption.suffix(160))”").font(.callout.italic()).foregroundStyle(.secondary)
                    .multilineTextAlignment(.center).padding(.horizontal).transition(.opacity)
            }
            Label("Keep LocalBook open on screen while this is made.", systemImage: "iphone.gen3").font(.caption).foregroundStyle(.secondary)
            Button("Stop", role: .cancel, action: stop).buttonStyle(.bordered)
        }
        .frame(maxWidth: .infinity).padding(.vertical, 8)
    }
}
