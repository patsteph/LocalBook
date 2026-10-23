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
        let url = StudioStore.audioURL(script.file)
        Task {
            let p = await Task.detached { try? AVAudioPlayer(contentsOf: url) }.value
            guard let p, self.script == script else { return }
            player = p; duration = p.duration; time = 0; current = 0
        }
    }

    /// Category and activation happen off the main thread (Apple flags them there as a hang risk).
    @concurrent static func prepareSession() async {
        let session = AVAudioSession.sharedInstance()
        try? session.setCategory(.playback, mode: .spokenAudio)
        _ = try? await session.activate(options: [])
    }

    func toggle() { playing ? pause() : play() }

    func play() {
        guard player != nil else { return }
        playing = true                                   // the button flips at once; the session starts without blocking the UI
        Task {
            await Self.prepareSession()
            begin()
        }
    }

    private func begin() {
        guard let player, playing else { return }
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
        Task.detached { _ = try? await AVAudioSession.sharedInstance().deactivate(options: .notifyOthersOnDeactivation) }
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
        case .video: ["Setting up the lights…", "Drawing the slides…", "Recording the narration…", "Editing it together…"]
        case .doc: ["Gathering the best bits…", "Finding the right words…", "Adding the headings…", "Polishing…"]
        }
    }

    var body: some View {
        VStack(spacing: 16) {
            MakingGlyph(kind: kind).padding(.top, 24)

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


/// The animation shown while an item is made: each kind has its own, so it matches what is being built.
struct MakingGlyph: View {
    let kind: StudioItem.Kind

    var body: some View {
        TimelineView(.animation) { ctx in
            let t = ctx.date.timeIntervalSinceReferenceDate
            ZStack {
                switch kind {
                case .audio, .video: waves(t)
                case .visual: network(t)
                case .doc: lines(t)
                case .quiz: bubbles(t)
                case .cards: deck(t)
                }
            }
            .frame(width: 220, height: 110)
        }
    }

    private func waves(_ t: Double) -> some View {
        HStack(alignment: .center, spacing: 5) {
            ForEach(0..<19, id: \.self) { i in
                let phase = t * 3 + Double(i) * 0.55
                Capsule().fill(LinearGradient(colors: [.orange, .pink, .purple], startPoint: .bottom, endPoint: .top))
                    .frame(width: 7, height: 14 + 46 * abs(sin(phase) * cos(phase * 0.43 + Double(i))))
            }
        }
    }

    /// A hub with nodes that pop out on lines and settle: a diagram being drawn. One Canvas, so it costs one draw call per frame.
    private func network(_ t: Double) -> some View {
        Canvas { ctx, size in
            let colors: [Color] = [.blue, .orange, .green, .pink, .purple, .teal]
            let c = CGPoint(x: size.width / 2, y: size.height / 2)
            for i in 0..<6 {
                let a = Double(i) / 6 * 2 * .pi + t * 0.25
                let grow = 0.5 + 0.5 * sin(t * 1.6 + Double(i) * 1.1)
                let r = 30 + 38 * grow
                let p = CGPoint(x: c.x + cos(a) * r * 1.5, y: c.y + sin(a) * r * 0.9)
                var line = Path(); line.move(to: c); line.addLine(to: p)
                ctx.stroke(line, with: .color(colors[i].opacity(0.45)),
                           style: StrokeStyle(lineWidth: 2, lineCap: .round, dash: [4, 4], dashPhase: -t * 16))
                let d = 10 + 12 * grow
                ctx.fill(Path(ellipseIn: CGRect(x: p.x - d / 2, y: p.y - d / 2, width: d, height: d)), with: .color(colors[i]))
            }
            let hub = 30 + 4 * sin(t * 3)
            ctx.fill(Path(ellipseIn: CGRect(x: c.x - hub / 2, y: c.y - hub / 2, width: hub, height: hub)), with: .color(.accentColor))
        }
    }

    /// Text lines being typed out.
    private func lines(_ t: Double) -> some View {
        VStack(alignment: .leading, spacing: 9) {
            ForEach(0..<5, id: \.self) { i in
                let w = 40 + 150 * abs(sin(t * 0.9 + Double(i) * 0.8))
                Capsule().fill(i == 0 ? Color.blue : Color.blue.opacity(0.35)).frame(width: i == 0 ? 90 : w, height: i == 0 ? 10 : 7)
            }
        }.frame(maxWidth: .infinity, alignment: .leading).padding(.leading, 14)
    }

    /// Question bubbles bouncing in turn.
    private func bubbles(_ t: Double) -> some View {
        HStack(spacing: 14) {
            ForEach(0..<3, id: \.self) { i in
                let hop = max(0, sin(t * 4 - Double(i) * 1.0))
                Text(i == 2 ? "✓" : "?").font(.title.bold()).foregroundStyle(.white)
                    .frame(width: 46, height: 46).background((i == 2 ? Color.green : Color.orange).gradient, in: Circle())
                    .offset(y: -22 * hop).scaleEffect(1 + 0.15 * hop)
            }
        }
    }

    /// A pair of cards flipping over.
    private func deck(_ t: Double) -> some View {
        ZStack {
            ForEach(0..<3, id: \.self) { i in
                RoundedRectangle(cornerRadius: 12).fill([Color.green, .mint, .teal][i].gradient)
                    .frame(width: 96, height: 64)
                    .rotation3DEffect(.degrees(sin(t * 2 - Double(i) * 0.6) * 70), axis: (x: 0, y: 1, z: 0))
                    .offset(x: CGFloat(i - 1) * 26, y: CGFloat(i) * -4)
                    .opacity(1 - Double(i) * 0.2)
            }
        }
    }
}
