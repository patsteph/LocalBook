import AVFoundation
import FluidAudio
import Speech
import SwiftUI
import UIKit

/// Records a meeting, then (afterwards, not live) works out who spoke and what they said, on this phone.
/// The audio is kept until the note is saved, so a meeting is never lost to a failed transcription.
@MainActor @Observable
final class MeetingSession {
    enum Phase: Equatable { case idle, recording, working, review, failed(String) }
    struct Turn: Identifiable { let id = UUID(); var speaker: String; var start: Double; var text: String }

    private(set) var phase: Phase = .idle
    private(set) var elapsed = 0.0
    private(set) var interrupted = false
    private(set) var step = ""
    private(set) var progress = 0.0
    private(set) var turns: [Turn] = []
    /// Speaker ids in the order they first spoke, and the name the person gave each.
    private(set) var speakers: [String] = []
    var names: [String: String] = [:]
    private(set) var pending: URL?

    private var recorder: AVAudioRecorder?
    private var ticker: Task<Void, Never>?
    private var file: URL?
    private var observers: [any NSObjectProtocol] = []

    static var folder: URL {
        let url = URL.applicationSupportDirectory.appending(path: "LocalBookLab/Meetings")
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    init() { pending = Self.latestRecording() }

    private static func latestRecording() -> URL? {
        let files = (try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: [.contentModificationDateKey])) ?? []
        return files.filter { $0.pathExtension == "m4a" }.max {
            ((try? $0.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast)
                < ((try? $1.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast)
        }
    }

    func displayName(_ id: String) -> String {
        let typed = names[id]?.trimmingCharacters(in: .whitespaces) ?? ""
        return typed.isEmpty ? "Speaker \((speakers.firstIndex(of: id) ?? 0) + 1)" : typed
    }

    /// The transcript as it will be saved: one paragraph per turn, led by the speaker's name.
    var transcript: String { turns.map { "\(displayName($0.speaker)): \($0.text)" }.joined(separator: "\n\n") }

    // MARK: recording

    @concurrent private static func prepareSession() async {
        let session = AVAudioSession.sharedInstance()
        try? session.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker, .allowBluetoothHFP])
        _ = try? await session.activate(options: [])
    }

    func start() async {
        guard await AVAudioApplication.requestRecordPermission() else { phase = .failed("Allow the microphone for LocalBook in Settings to record."); return }
        await Self.prepareSession()
        let url = Self.folder.appendingPathComponent(UUID().uuidString + ".m4a")
        do {
            let r = try AVAudioRecorder(url: url, settings: [AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: 16_000,
                                                             AVNumberOfChannelsKey: 1, AVEncoderBitRateKey: 48_000])
            guard r.record() else { phase = .failed("Couldn't start recording."); return }
            recorder = r; file = url; elapsed = 0; interrupted = false; phase = .recording
            UIApplication.shared.isIdleTimerDisabled = false
            watchInterruptions()
            ticker = Task { [weak self] in
                while !Task.isCancelled {
                    try? await Task.sleep(for: .milliseconds(250))
                    guard let self, let r = self.recorder else { return }
                    self.elapsed = r.currentTime
                }
            }
        } catch { phase = .failed("Couldn't start recording: \(error.localizedDescription)") }
    }

    /// A phone call or alarm pauses the recording; it carries on when that ends.
    private func watchInterruptions() {
        let center = NotificationCenter.default
        let stopped = center.addObserver(forName: AVAudioSession.didBecomeInactiveNotification, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.interrupted = true }
        }
        let resumable = center.addObserver(forName: AVAudioSession.resumptionRecommendationNotification, object: nil, queue: .main) { [weak self] note in
            let advice = (note.userInfo?[AVAudioSession.resumptionContextKey] as? AVAudioSession.ResumptionContext)?.recommendation
            MainActor.assumeIsolated {
                guard let self, advice == .shouldResume else { return }
                self.interrupted = false
                self.recorder?.record()
            }
        }
        observers = [stopped, resumable]
    }

    func stopRecording() async {
        ticker?.cancel(); ticker = nil
        recorder?.stop(); recorder = nil
        for o in observers { NotificationCenter.default.removeObserver(o) }; observers = []
        guard let file else { return }
        pending = file
        await process(file)
    }

    func processPending() async { if let pending { file = pending; await process(pending) } }

    // MARK: who said what

    private struct Word { var start: Double; var end: Double; var text: String }

    func process(_ url: URL) async {
        phase = .working; progress = 0; step = "Getting ready…"
        UIApplication.shared.isIdleTimerDisabled = true
        defer { UIApplication.shared.isIdleTimerDisabled = false }
        do {
            step = "Finding who is speaking… (the first time this downloads a small model)"
            let manager = OfflineDiarizerManager(config: OfflineDiarizerConfig())
            try await manager.prepareModels()
            let spoken = try await manager.process(url)
            let who = spoken.segments.map { (id: "\($0.speakerId)", start: Double($0.startTimeSeconds), end: Double($0.endTimeSeconds)) }
            progress = 0.3

            step = "Writing down the words…"
            let words = try await Self.recognize(url) { fraction in
                Task { @MainActor in self.progress = 0.3 + 0.7 * fraction }
            }
            guard !words.isEmpty else { phase = .failed("No speech was found in the recording."); return }
            build(words: words, who: who)
            phase = .review
        } catch { phase = .failed(error.localizedDescription) }
    }

    /// Gives each word to whoever was speaking at its midpoint, and joins neighbouring words by the same person into turns.
    private func build(words: [Word], who: [(id: String, start: Double, end: Double)]) {
        func speaker(at t: Double) -> String {
            if let hit = who.first(where: { t >= $0.start && t <= $0.end }) { return hit.id }
            return who.min { min(abs($0.start - t), abs($0.end - t)) < min(abs($1.start - t), abs($1.end - t)) }?.id ?? "0"
        }
        var order: [String] = []
        var out: [Turn] = []
        for w in words {
            let id = speaker(at: (w.start + w.end) / 2)
            if !order.contains(id) { order.append(id) }
            if var last = out.last, last.speaker == id {
                last.text += " " + w.text; out[out.count - 1] = last
            } else { out.append(Turn(speaker: id, start: w.start, text: w.text)) }
        }
        speakers = order; turns = out
    }

    /// Words with their times, from Apple's on-device recognizer (the same one Dictate uses).
    private static func recognize(_ url: URL, progress: @escaping @Sendable (Double) -> Void) async throws -> [Word] {
        let allowed = await withCheckedContinuation { (c: CheckedContinuation<Bool, Never>) in
            SFSpeechRecognizer.requestAuthorization { c.resume(returning: $0 == .authorized) }
        }
        guard allowed else { throw MediaTranscriber.Failure(text: "Allow Speech Recognition for LocalBook in Settings to transcribe.") }
        guard let recognizer = SFSpeechRecognizer(locale: Locale.current), recognizer.isAvailable else {
            throw MediaTranscriber.Failure(text: "Transcription isn't available right now.")
        }
        let total = max(1, try await AVURLAsset(url: url).load(.duration).seconds)
        let request = SFSpeechURLRecognitionRequest(url: url)
        request.shouldReportPartialResults = true
        request.requiresOnDeviceRecognition = recognizer.supportsOnDeviceRecognition
        let latest = Latest()
        return try await withCheckedThrowingContinuation { (cont: CheckedContinuation<[Word], Error>) in
            _ = recognizer.recognitionTask(with: request) { result, error in
                if let result {
                    let words = result.bestTranscription.segments.map { Word(start: $0.timestamp, end: $0.timestamp + $0.duration, text: $0.substring) }
                    latest.set(words)
                    progress(min(1, (words.last?.end ?? 0) / total))
                    if result.isFinal, latest.finish() { cont.resume(returning: words) }
                } else if let error, latest.finish() {
                    // A long recording can end with an error after most of it was heard: keep what was heard.
                    let heard = latest.words
                    if heard.count > 20 { cont.resume(returning: heard) } else { cont.resume(throwing: error) }
                }
            }
        }
    }

    private final class Latest: @unchecked Sendable {
        private let lock = NSLock()
        private var stored: [Word] = []
        private var done = false
        func set(_ w: [Word]) { lock.lock(); stored = w; lock.unlock() }
        var words: [Word] { lock.lock(); defer { lock.unlock() }; return stored }
        func finish() -> Bool { lock.lock(); defer { lock.unlock() }; if done { return false }; done = true; return true }
    }

    // MARK: finishing

    /// Call after the note is saved (or the recording is thrown away).
    func discard() {
        if let file { try? FileManager.default.removeItem(at: file) }
        if let pending { try? FileManager.default.removeItem(at: pending) }
        file = nil; pending = Self.latestRecording(); turns = []; speakers = []; names = [:]; phase = .idle
    }

    func reset() { turns = []; speakers = []; names = [:]; phase = .idle; pending = Self.latestRecording() }
}

struct MeetingView: View {
    let library: LibraryModel
    let notebookId: String
    @Environment(\.dismiss) private var dismiss
    @State private var session = MeetingSession()
    @State private var title = ""
    @State private var saving = false
    @State private var saveError: String?

    private var notebookTitle: String { library.notebooks.first { $0.id == notebookId }?.title ?? "Notebook" }
    private func clock(_ t: Double) -> String { String(format: "%d:%02d", Int(t) / 60, Int(t) % 60) }

    var body: some View {
        NavigationStack {
            Group {
                switch session.phase {
                case .idle: idle
                case .recording: recording
                case .working: working
                case .review: review
                case .failed(let why): failed(why)
                }
            }
            .navigationTitle("Meeting").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    if session.phase != .recording && session.phase != .working { Button("Close") { dismiss() } }
                }
            }
        }
        .interactiveDismissDisabled(session.phase == .recording || session.phase == .working || saving)
    }

    private var idle: some View {
        VStack(spacing: 18) {
            Spacer()
            Image(systemName: "person.2.wave.2.fill").font(.system(size: 54)).foregroundStyle(.tint)
            Text("Record a meeting").font(.title3.bold())
            Text("Put the phone where everyone can be heard. When you stop, LocalBook works out who spoke and writes it into “\(notebookTitle)” as a searchable note. It all happens on this phone.")
                .font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center).padding(.horizontal, 32)
            Button { Task { await session.start() } } label: {
                Label("Start recording", systemImage: "record.circle").frame(maxWidth: 260).padding(.vertical, 6)
            }.buttonStyle(.borderedProminent).controlSize(.large).tint(.red)
            if session.pending != nil {
                VStack(spacing: 8) {
                    Text("A recording was never turned into a note.").font(.footnote).foregroundStyle(.secondary)
                    HStack {
                        Button("Write it up") { Task { await session.processPending() } }.buttonStyle(.bordered)
                        Button("Delete it", role: .destructive) { session.discard() }.buttonStyle(.bordered)
                    }
                }.padding(.top, 12)
            }
            Spacer()
        }.frame(maxWidth: .infinity)
    }

    private var recording: some View {
        VStack(spacing: 18) {
            Spacer()
            Image(systemName: session.interrupted ? "pause.circle.fill" : "waveform").font(.system(size: 54)).foregroundStyle(.red)
                .symbolEffect(.variableColor.iterative, isActive: !session.interrupted)
            Text(clock(session.elapsed)).font(.system(size: 44, weight: .semibold, design: .rounded)).monospacedDigit()
            Text(session.interrupted ? "Paused by a call or alert. It carries on by itself." : "Recording. You can lock the screen.")
                .font(.footnote).foregroundStyle(.secondary)
            Button { Task { await session.stopRecording() } } label: {
                Label("Stop and write it up", systemImage: "stop.circle.fill").frame(maxWidth: 260).padding(.vertical, 6)
            }.buttonStyle(.borderedProminent).controlSize(.large)
            Spacer()
        }.frame(maxWidth: .infinity)
    }

    private var working: some View {
        VStack(spacing: 14) {
            Spacer()
            ProgressView(value: min(1, max(0.03, session.progress))).padding(.horizontal, 40)
            Text(session.step).font(.footnote).multilineTextAlignment(.center).padding(.horizontal, 32)
            Label("Keep LocalBook open on screen while this runs. The recording is safe on your phone.", systemImage: "iphone.gen3")
                .font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center).padding(.horizontal, 32)
            Spacer()
        }.frame(maxWidth: .infinity)
    }

    private func failed(_ why: String) -> some View {
        VStack(spacing: 14) {
            Spacer()
            Image(systemName: "exclamationmark.triangle").font(.system(size: 40)).foregroundStyle(.orange)
            Text(why).font(.footnote).multilineTextAlignment(.center).padding(.horizontal, 32)
            if session.pending != nil { Text("The recording is still on your phone.").font(.caption).foregroundStyle(.secondary) }
            HStack {
                if session.pending != nil { Button("Try again") { Task { await session.processPending() } }.buttonStyle(.borderedProminent) }
                Button("Back") { session.reset() }.buttonStyle(.bordered)
            }
            Spacer()
        }.frame(maxWidth: .infinity)
    }

    private var review: some View {
        Form {
            Section { TextField("Title (optional)", text: $title) }
            Section {
                ForEach(session.speakers, id: \.self) { id in
                    TextField(session.displayName(id), text: Binding(get: { session.names[id] ?? "" }, set: { session.names[id] = $0 }))
                }
            } header: { Text("Who spoke") } footer: { Text("Type a name for each voice. The transcript below uses it.") }
            Section("Transcript") {
                ForEach(session.turns) { turn in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(session.displayName(turn.speaker) + " · " + clock(turn.start)).font(.caption.bold()).foregroundStyle(.tint)
                        Text(turn.text).font(.callout)
                    }
                }
            }
            if let saveError { Section { Text(saveError).font(.footnote).foregroundStyle(.orange) } }
            Section {
                Button { Task { await save() } } label: {
                    HStack { if saving { ProgressView() }; Text(saving ? "Saving…" : "Save as a note") }.frame(maxWidth: .infinity)
                }.disabled(saving)
                Button("Throw this recording away", role: .destructive) { session.discard() }.disabled(saving)
            } footer: { Text("Saved in “\(notebookTitle)” and searchable straight away. The recording itself is deleted once the note is saved.") }
        }
        .scrollDismissesKeyboard(.interactively)
    }

    private func save() async {
        saving = true; saveError = nil; defer { saving = false }
        let name = title.trimmingCharacters(in: .whitespaces).isEmpty ? "Meeting " + Date().formatted(date: .abbreviated, time: .shortened) : title
        do {
            try await library.addNote(notebook: notebookId, title: name, text: session.transcript)
            session.discard()
            dismiss()
        } catch { saveError = "Couldn't save: \(error.localizedDescription)" }
    }
}
