import AVFoundation
import FluidAudio
import LocalBookCore
import LocalBookEngines
import Observation
import SwiftUI

/// Records a meeting, then (afterwards, not live) works out who spoke and what they said, on this Mac.
/// The audio is kept until the note is saved, so a meeting is never lost to a failed transcription.
/// The phone's `MeetingSession` without the audio session, interruptions and model pausing: the Mac has the
/// memory to keep the answer model loaded while a meeting is written up.
@MainActor @Observable
final class MeetingSession {
    enum Phase: Equatable { case idle, recording, working, review, failed(String) }
    struct Turn: Identifiable, Codable {
        var id = UUID(); var speaker: String; var start: Double; var text: String
        private enum CodingKeys: String, CodingKey { case speaker, start, text }
    }
    private struct Saved: Codable { var turns: [Turn]; var speakers: [String]; var names: [String: String]; var title: String }

    private(set) var phase: Phase = .idle
    private(set) var elapsed = 0.0
    private(set) var step = ""
    private(set) var progress = 0.0
    private(set) var stage = 0
    private(set) var voicesFound = 0
    private(set) var wordsHeard = 0
    private(set) var meetingLength = 0.0
    private(set) var workStarted = Date()
    private(set) var turns: [Turn] = []
    private(set) var speakers: [String] = []
    var names: [String: String] = [:]
    var title = ""
    private(set) var pending: URL?
    private(set) var micSilent = false

    private var recorder: AVAudioRecorder?
    private var ticker: Task<Void, Never>?
    private var micCheck: Task<Void, Never>?
    private var file: URL?
    private var stopper: LongTranscriber.Stopper?
    private var awake: KeepAwake?

    static var folder: URL {
        let url = AppFolders.userFiles.appending(path: "Meetings", directoryHint: .isDirectory)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    init() {
        pending = Self.latestRecording()
        if let pending, let saved = Self.restore(for: pending) {
            file = pending
            turns = saved.turns; speakers = saved.speakers; names = saved.names; title = saved.title
            phase = .review
        }
    }

    struct SavedMeta: Codable { var title: String; var notebookId: String; var notebookTitle: String; var savedAt: Date }

    static func savedMetaURL(for recording: URL) -> URL { recording.deletingPathExtension().appendingPathExtension("saved.json") }
    static func savedMeta(for recording: URL) -> SavedMeta? {
        guard let data = try? Data(contentsOf: savedMetaURL(for: recording)) else { return nil }
        let d = JSONDecoder(); d.dateDecodingStrategy = .iso8601
        return try? d.decode(SavedMeta.self, from: data)
    }
    static func hasReview(_ recording: URL) -> Bool { restore(for: recording) != nil }
    static func removeFiles(for recording: URL) {
        for u in [recording, sidecar(for: recording), savedMetaURL(for: recording)] { try? FileManager.default.removeItem(at: u) }
    }

    func markSaved(title: String, notebookId: String, notebookTitle: String) {
        if let url = file ?? pending {
            let e = JSONEncoder(); e.dateEncodingStrategy = .iso8601
            if let data = try? e.encode(SavedMeta(title: title, notebookId: notebookId, notebookTitle: notebookTitle, savedAt: Date())) {
                try? data.write(to: Self.savedMetaURL(for: url), options: .atomic)
            }
            try? FileManager.default.removeItem(at: Self.sidecar(for: url))
        }
        file = nil; turns = []; speakers = []; names = [:]; self.title = ""; phase = .idle
        pending = Self.latestRecording()
    }

    /// Open a particular recording from the list (to write it up).
    func select(_ url: URL) {
        guard phase == .idle || phase == .review else { return }
        if phase == .review { reset() }
        pending = url
    }

    private static func sidecar(for recording: URL) -> URL { recording.deletingPathExtension().appendingPathExtension("review.json") }

    private static func restore(for recording: URL) -> Saved? {
        guard let data = try? Data(contentsOf: sidecar(for: recording)),
              let saved = try? JSONDecoder().decode(Saved.self, from: data), !saved.turns.isEmpty else { return nil }
        return saved
    }

    func persist() {
        guard phase == .review, let recording = file ?? pending else { return }
        let saved = Saved(turns: turns, speakers: speakers, names: names, title: title)
        if let data = try? JSONEncoder().encode(saved) { try? data.write(to: Self.sidecar(for: recording), options: .atomic) }
    }

    static func recordings() -> [URL] {
        let files = (try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: [.contentModificationDateKey])) ?? []
        return files.filter { $0.pathExtension == "m4a" }.sorted { Self.date($0) > Self.date($1) }
    }

    static func date(_ url: URL) -> Date {
        (try? url.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
    }

    private static func latestRecording() -> URL? {
        recordings().first { savedMeta(for: $0) == nil }
    }

    func displayName(_ id: String) -> String {
        let typed = names[id]?.trimmingCharacters(in: .whitespaces) ?? ""
        return typed.isEmpty ? "Speaker \((speakers.firstIndex(of: id) ?? 0) + 1)" : typed
    }

    func glance(_ id: String) -> String {
        let mine = turns.filter { $0.speaker == id }
        guard let first = mine.first else { return "" }
        let opening = first.text.split(separator: " ").prefix(8).joined(separator: " ")
        return "Spoke \(mine.count) time\(mine.count == 1 ? "" : "s"), first at \(String(format: "%d:%02d", Int(first.start) / 60, Int(first.start) % 60)): “\(opening)…”"
    }

    var transcript: String {
        let who = speakers.map { displayName($0) }.joined(separator: ", ")
        let top = "\(MeetingNotes.header), \(Date().formatted(date: .complete, time: .shortened)). Speakers: \(who)."
        return ([top] + turns.map { "\(displayName($0.speaker)): \($0.text)" }).joined(separator: "\n\n")
    }

    // MARK: remembered names

    private static let namesKey = "meetingNames"
    static var knownNames: [String] { UserDefaults.standard.stringArray(forKey: namesKey) ?? [] }

    func suggestions(for id: String) -> [String] {
        let typed = (names[id] ?? "").trimmingCharacters(in: .whitespaces)
        guard typed.count >= 2 else { return [] }
        let taken = Set(names.filter { $0.key != id }.map { $0.value.lowercased() })
        return Self.knownNames.filter { name in
            let n = name.lowercased(), t = typed.lowercased()
            return n != t && !taken.contains(n) && (n.hasPrefix(t) || n.split(separator: " ").contains { $0.hasPrefix(t) })
        }.prefix(4).map { $0 }
    }

    func rememberNames() {
        var known = Self.knownNames
        for id in speakers {
            let n = (names[id] ?? "").trimmingCharacters(in: .whitespaces)
            guard !n.isEmpty else { continue }
            known.removeAll { $0.lowercased() == n.lowercased() }
            known.insert(n, at: 0)
        }
        UserDefaults.standard.set(Array(known.prefix(300)), forKey: Self.namesKey)
    }

    // MARK: recording

    func start() async {
        guard await AVAudioApplication.requestRecordPermission() else {
            phase = .failed("Allow the microphone for LocalBook in System Settings › Privacy & Security to record."); return
        }
        let url = Self.folder.appendingPathComponent(UUID().uuidString + ".m4a")
        do {
            let r = try AVAudioRecorder(url: url, settings: [AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: 16_000,
                                                             AVNumberOfChannelsKey: 1, AVEncoderBitRateKey: 48_000])
            r.isMeteringEnabled = true
            guard r.record() else { phase = .failed("Couldn't start recording."); return }
            recorder = r; file = url; elapsed = 0; micSilent = false; phase = .recording
            awake = KeepAwake.begin(reason: "Recording a meeting")
            checkMic()
            ticker = Task { [weak self] in
                while !Task.isCancelled {
                    try? await Task.sleep(for: .milliseconds(250))
                    guard let self, let r = self.recorder else { return }
                    self.elapsed = r.currentTime
                }
            }
        } catch { phase = .failed("Couldn't start recording: \(error.localizedDescription)") }
    }

    /// A real microphone never gives perfect digital silence; if nothing at all arrives in 2 s, say so.
    private func checkMic() {
        micCheck?.cancel()
        micCheck = Task { [weak self] in
            var loudest: Float = -160
            for _ in 0..<8 {
                try? await Task.sleep(for: .milliseconds(250))
                guard !Task.isCancelled, let self, let r = self.recorder, self.phase == .recording else { return }
                r.updateMeters()
                loudest = max(loudest, r.peakPower(forChannel: 0))
                if loudest > -120 { return }
            }
            guard let self, self.phase == .recording else { return }
            SoakTrace.log("meeting: microphone silent after 2 s (peak \(loudest) dB)")
            self.micSilent = true
        }
    }

    func stopRecording() async {
        micCheck?.cancel(); micCheck = nil
        ticker?.cancel(); ticker = nil
        recorder?.stop()
        recorder = nil
        awake?.end(); awake = nil
        guard let file else { return }
        pending = file
        await process(file)
    }

    func stopWorking() { stopper?.stop() }

    func processPending() async {
        guard let pending else { return }
        file = pending
        if let saved = Self.restore(for: pending) {
            turns = saved.turns; speakers = saved.speakers; names = saved.names; title = saved.title
            phase = .review
        } else { await process(pending) }
    }

    // MARK: who said what

    private typealias Word = LongTranscriber.Word

    func process(_ url: URL) async {
        phase = .working; progress = 0; step = "Getting ready…"
        stage = 0; voicesFound = 0; wordsHeard = 0; workStarted = Date()
        meetingLength = (try? await AVURLAsset(url: url).load(.duration).seconds) ?? 0
        let hold = KeepAwake.begin(reason: "Writing up a meeting")
        let stop = LongTranscriber.Stopper(); stopper = stop
        defer { hold.end(); stopper = nil }
        do {
            let began = Date()
            func lap(_ what: String) { SoakTrace.log("meeting: \(what) after \(String(format: "%.1f", Date().timeIntervalSince(began))) s") }
            step = "Finding who is speaking… (the first time this downloads a small model)"
            let manager = OfflineDiarizerManager(config: OfflineDiarizerConfig(zeroVoteReembed: .init(enabled: true)))
            try await manager.prepareModels()
            lap("speaker models ready")
            let spoken = try await manager.process(url)
            let who = spoken.segments.map { (id: "\($0.speakerId)", start: Double($0.startTimeSeconds), end: Double($0.endTimeSeconds)) }
            lap("speakers found: \(Set(who.map(\.id)).count) voices, \(who.count) segments")
            progress = 0.3; stage = 1; voicesFound = Set(who.map(\.id)).count
            if stop.isStopped { throw LongTranscriber.Failure(text: "Stopped. The recording is still on this Mac.") }

            step = "Writing down the words…"
            let total = max(1, try await AVURLAsset(url: url).load(.duration).seconds)
            let words = try await LongTranscriber.transcribe(url, total: total, stopper: stop,
                step: { text in Task { @MainActor in self.step = text } },
                progress: { fraction in Task { @MainActor in self.progress = 0.3 + 0.7 * fraction } })
            lap("words written: \(words.count)")
            guard !words.isEmpty else { phase = .failed("No speech was found in the recording."); return }
            stage = 2; wordsHeard = words.count; step = "Putting it in order…"
            build(words: words, who: who)
            phase = .review
            persist()
        } catch {
            SoakTrace.log("meeting: failed: \(error)")
            phase = .failed(error.localizedDescription)
        }
    }

    private func build(words: [Word], who: [(id: String, start: Double, end: Double)]) {
        func speaker(at t: Double) -> String {
            if let hit = who.first(where: { t >= $0.start && t <= $0.end }) { return hit.id }
            return who.min { min(abs($0.start - t), abs($0.end - t)) < min(abs($1.start - t), abs($1.end - t)) }?.id ?? "0"
        }
        let ids = Self.tidy(words, ids: words.map { speaker(at: ($0.start + $0.end) / 2) })
        var order: [String] = []
        var out: [Turn] = []
        for (w, id) in zip(words, ids) {
            if !order.contains(id) { order.append(id) }
            if var last = out.last, last.speaker == id {
                last.text = LongTranscriber.join(last.text, w.text); out[out.count - 1] = last
            } else { out.append(Turn(speaker: id, start: w.start, text: w.text)) }
        }
        for i in out.indices { out[i].text = out[i].text.trimmingCharacters(in: .whitespacesAndNewlines) }
        speakers = order; turns = out
    }

    /// The phone's two repairs around speaker changes: a lone wedged word belongs to the run around it, and a change
    /// in mid-sentence moves to the nearest pause or sentence end within a few words.
    private nonisolated static func tidy(_ words: [Word], ids input: [String]) -> [String] {
        var ids = input
        let n = words.count
        guard n > 2 else { return ids }
        func gap(_ j: Int) -> Double { max(0, words[j].start - words[j - 1].end) }
        for i in 1..<(n - 1) where ids[i - 1] == ids[i + 1] && ids[i] != ids[i - 1] && gap(i) < 0.15 && gap(i + 1) < 0.15 {
            ids[i] = ids[i - 1]
        }
        func score(_ j: Int) -> Double {
            let end = words[j - 1].text.trimmingCharacters(in: .whitespaces).last
            return gap(j) + (end.map { ".?!".contains($0) } == true ? 0.5 : 0)
        }
        var i = 1
        while i < n {
            guard ids[i] != ids[i - 1] else { i += 1; continue }
            var lo = i - 1; while lo > 0 && ids[lo - 1] == ids[i - 1] { lo -= 1 }
            var hi = i; while hi + 1 < n && ids[hi + 1] == ids[i] { hi += 1 }
            var best = i, top = score(i)
            for j in max(lo + 1, i - 4)...min(hi, i + 4) where score(j) > top + 0.15 { best = j; top = score(j) }
            if best != i {
                let left = ids[i - 1], right = ids[i]
                for k in min(i, best)..<max(i, best) { ids[k] = best > i ? left : right }
            }
            i = max(i, best) + 1
        }
        return ids
    }

    // MARK: finishing

    func discard() {
        for url in [file, pending].compactMap({ $0 }) { Self.removeFiles(for: url) }
        file = nil; turns = []; speakers = []; names = [:]; title = ""; phase = .idle
        pending = Self.latestRecording()
    }

    func reset() { file = nil; turns = []; speakers = []; names = [:]; phase = .idle; pending = Self.latestRecording() }
}

/// Meetings: the recordings on this Mac on the left, the recorder (idle, recording, working, review) on the right.
/// Record › Start Meeting Recording (⇧⌘R) opens this pane and starts at once.
struct MeetingsPane: View {
    @Environment(AppModel.self) private var model
    @State private var session = MeetingSession()
    @State private var recordings: [URL] = []
    @State private var title = ""
    @State private var destination: String?
    @State private var saving = false
    @State private var saveProgress = 0.0
    @State private var saveError: String?
    @State private var confirmDelete: URL?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var library: LibraryModel { model.library }
    private var target: String? { destination ?? library.scopeToNotebook }
    private var notebookTitle: String { library.notebooks.first { $0.id == target }?.title ?? "Notebook" }
    private func clock(_ t: Double) -> String { String(format: "%d:%02d", Int(t) / 60, Int(t) % 60) }

    var body: some View {
        HSplitView {
            list.frame(minWidth: 240, idealWidth: 280, maxWidth: 380)
            Group {
                switch session.phase {
                case .idle: idle
                case .recording: recording
                case .working: working
                case .review: review
                case .failed(let why): failed(why)
                }
            }
            .frame(minWidth: 400, maxWidth: .infinity, maxHeight: .infinity)
            .overlay { if saving { savingCover } }
        }
        .task { reload(); title = session.title }
        .onChange(of: title) { _, v in session.title = v; session.persist() }
        .onChange(of: session.names) { _, _ in session.persist() }
        .onChange(of: session.phase) { _, p in if p == .review { title = session.title }; reload() }
        .onReceive(NotificationCenter.default.publisher(for: .startMeetingRecording)) { _ in
            if session.phase == .idle { Task { await session.start() } }
        }
        .confirmationDialog("Delete this recording?", isPresented: Binding(get: { confirmDelete != nil }, set: { if !$0 { confirmDelete = nil } }), titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                if let url = confirmDelete { MeetingSession.removeFiles(for: url); if session.pending == url { session.reset() }; reload() }
                confirmDelete = nil
            }
        } message: { Text("The audio is removed from this Mac. A note already saved from it stays in its notebook.") }
    }

    // MARK: list

    private var list: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Recordings").font(.headline)
                Spacer()
                Button { Task { await session.start() } } label: { Label("Record", systemImage: "record.circle") }
                    .tint(.red).disabled(session.phase != .idle || library.notebooks.isEmpty)
                    .help(Shortcuts.startRecording.help("Start recording a meeting"))
            }
            .padding(12)
            Divider()
            List {
                if recordings.isEmpty { Text("No recordings yet.").font(.callout).foregroundStyle(.secondary) }
                ForEach(recordings, id: \.self) { url in
                    let saved = MeetingSession.savedMeta(for: url)
                    HStack(spacing: 10) {
                        Image(systemName: saved == nil ? "waveform" : "checkmark.circle").foregroundStyle(saved == nil ? .orange : .green).frame(width: 22)
                        VStack(alignment: .leading, spacing: 1) {
                            Text(saved?.title ?? MeetingSession.date(url).formatted(date: .abbreviated, time: .shortened)).lineLimit(1)
                            Text(saved.map { "Saved in \($0.notebookTitle)" } ?? (MeetingSession.hasReview(url) ? "Written up, not saved" : "Not written up yet"))
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    .contextMenu {
                        if saved == nil, session.phase == .idle || session.phase == .review {
                            Button("Write It Up") { session.select(url); Task { await session.processPending() } }
                        }
                        Button("Delete…", role: .destructive) { confirmDelete = url }
                    }
                }
            }
            .listStyle(.inset)
        }
    }

    private func reload() { recordings = MeetingSession.recordings() }

    // MARK: phases

    private var idle: some View {
        VStack(spacing: 18) {
            Spacer()
            Image(systemName: "person.2.wave.2.fill").font(.system(size: 54)).foregroundStyle(.tint)
            Text("Record a meeting").font(.title3.bold())
            Text("When you stop, LocalBook works out who spoke and writes it into “\(notebookTitle)” as a searchable note. It all happens on this Mac.")
                .font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 420)
            Button { Task { await session.start() } } label: {
                Label("Start recording", systemImage: "record.circle").frame(maxWidth: 240).padding(.vertical, 4)
            }.buttonStyle(.borderedProminent).controlSize(.large).tint(.red).disabled(library.notebooks.isEmpty)
                .help(Shortcuts.startRecording.help("Start recording a meeting"))
            if session.pending != nil {
                VStack(spacing: 8) {
                    Text("A recording was never turned into a note.").font(.footnote).foregroundStyle(.secondary)
                    HStack {
                        Button("Write it up") { Task { await session.processPending() } }.buttonStyle(.bordered)
                        Button("Delete it", role: .destructive) { session.discard(); reload() }.buttonStyle(.bordered)
                    }
                }.padding(.top, 12)
            }
            Spacer()
        }.frame(maxWidth: .infinity)
    }

    private var recording: some View {
        VStack(spacing: 18) {
            Spacer()
            Image(systemName: "waveform").font(.system(size: 54)).foregroundStyle(.red)
                .symbolEffect(.variableColor.iterative, isActive: !reduceMotion)
            Text(clock(session.elapsed)).font(.system(size: 44, weight: .semibold, design: .rounded)).monospacedDigit()
                .accessibilityLabel("Recording time \(Int(session.elapsed) / 60) minutes \(Int(session.elapsed) % 60) seconds")
            Text("Recording. You can work in other apps meanwhile.").font(.callout).foregroundStyle(.secondary)
            if session.micSilent {
                Text("The microphone isn't picking anything up. Check the input in System Settings › Sound, then stop and start again.")
                    .font(.footnote).foregroundStyle(.orange).multilineTextAlignment(.center).frame(maxWidth: 420)
            }
            Button { Task { await session.stopRecording() } } label: {
                Label("Stop and write it up", systemImage: "stop.circle.fill").frame(maxWidth: 240).padding(.vertical, 4)
            }.buttonStyle(.borderedProminent).controlSize(.large)
            Spacer()
        }.frame(maxWidth: .infinity)
    }

    private var working: some View {
        VStack(spacing: 18) {
            Spacer()
            Image(systemName: "text.badge.checkmark").font(.system(size: 44)).foregroundStyle(.tint)
                .symbolEffect(.pulse, isActive: !reduceMotion)
            Text("Writing up your meeting").font(.title3.bold())
            Text("\(clock(session.meetingLength)) recorded · safe on this Mac").font(.footnote).foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 12) {
                stageRow(0, "Finding who is speaking", done: session.stage > 0 ? "\(session.voicesFound) voice\(session.voicesFound == 1 ? "" : "s") found" : nil)
                stageRow(1, "Writing down the words", done: session.stage > 1 ? "\(session.wordsHeard.formatted()) words" : nil)
                stageRow(2, "Putting it in order", done: nil)
            }.frame(maxWidth: 360, alignment: .leading)
            ProgressView(value: min(1, max(0.03, session.progress))).frame(maxWidth: 360)
            TimelineView(.periodic(from: session.workStarted, by: 1)) { ctx in
                Text("Working for \(clock(ctx.date.timeIntervalSince(session.workStarted)))").font(.caption).monospacedDigit().foregroundStyle(.secondary)
            }
            Text(session.step).font(.caption).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 420)
            Button("Stop", role: .cancel) { session.stopWorking() }.buttonStyle(.bordered).padding(.top, 4)
            Spacer()
        }.frame(maxWidth: .infinity)
    }

    private func stageRow(_ n: Int, _ title: String, done: String?) -> some View {
        let state = session.stage > n ? 2 : (session.stage == n ? 1 : 0)
        return HStack(spacing: 10) {
            Group {
                if state == 2 { Image(systemName: "checkmark.circle.fill").foregroundStyle(.green) }
                else if state == 1 { ProgressView().controlSize(.small) }
                else { Image(systemName: "circle").foregroundStyle(.tertiary) }
            }.frame(width: 24)
            VStack(alignment: .leading, spacing: 0) {
                Text(title).font(.subheadline).foregroundStyle(state == 0 ? .secondary : .primary)
                if let done { Text(done).font(.caption).foregroundStyle(.secondary) }
            }
        }.accessibilityElement(children: .combine)
    }

    private var savingCover: some View {
        ZStack {
            Color.black.opacity(0.25)
            VStack(spacing: 14) {
                ProgressView().controlSize(.large)
                Text("Saving to “\(notebookTitle)”").font(.headline)
                Text(saveProgress > 0 ? "Making it searchable… \(Int(saveProgress * 100))%" : "Getting ready…").font(.footnote).foregroundStyle(.secondary).monospacedDigit()
                ProgressView(value: max(0.03, saveProgress)).frame(width: 200)
            }
            .padding(28).background(.regularMaterial, in: RoundedRectangle(cornerRadius: 16))
        }
        .accessibilityElement(children: .combine)
    }

    private func failed(_ why: String) -> some View {
        VStack(spacing: 14) {
            Spacer()
            Image(systemName: "exclamationmark.triangle").font(.system(size: 40)).foregroundStyle(.orange)
            Text(why).font(.callout).multilineTextAlignment(.center).frame(maxWidth: 420)
            if session.pending != nil { Text("The recording is still on this Mac.").font(.caption).foregroundStyle(.secondary) }
            HStack {
                if session.pending != nil { Button("Try again") { Task { await session.processPending() } }.buttonStyle(.borderedProminent) }
                Button("Back") { session.reset() }.buttonStyle(.bordered)
            }
            Spacer()
        }.frame(maxWidth: .infinity)
    }

    private var review: some View {
        VStack(spacing: 0) {
            Form {
                Section {
                    TextField("Title (optional)", text: $title)
                    Picker("Save in", selection: Binding(get: { target ?? "" }, set: { destination = $0 })) {
                        if target == nil { Text("Choose a notebook").tag("") }
                        ForEach(library.notebooks) { n in Text(n.title).tag(n.id) }
                    }
                }
                Section("Who spoke") {
                    ForEach(session.speakers, id: \.self) { id in
                        VStack(alignment: .leading, spacing: 4) {
                            TextField(session.displayName(id), text: Binding(get: { session.names[id] ?? "" }, set: { session.names[id] = $0 }))
                            let picks = session.suggestions(for: id)
                            if !picks.isEmpty {
                                HStack(spacing: 6) {
                                    ForEach(picks, id: \.self) { name in
                                        Button(name) { session.names[id] = name }.font(.footnote.weight(.medium)).controlSize(.small)
                                    }
                                }
                            }
                            Text(session.glance(id)).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                        }
                    }
                    Text("Names are optional. Any voice you leave blank is saved as Speaker 1, 2… and the note can be edited later.")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Section("Transcript") {
                    ForEach(session.turns) { turn in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(session.displayName(turn.speaker) + " · " + clock(turn.start)).font(.caption.bold()).foregroundStyle(.tint)
                            Text(turn.text).font(.callout).textSelection(.enabled)
                        }
                    }
                }
                if let saveError { Text(saveError).font(.footnote).foregroundStyle(.orange) }
            }
            .formStyle(.grouped)
            Divider()
            HStack {
                Button("Throw this recording away", role: .destructive) { session.discard(); reload() }.disabled(saving)
                Spacer()
                Text("Saved in “\(notebookTitle)” and searchable straight away.").font(.footnote).foregroundStyle(.secondary)
                Button { Task { await save() } } label: { Text(saving ? "Saving…" : "Save as a Note") }
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction).disabled(saving || target == nil)
            }
            .padding(12)
        }
    }

    private func save() async {
        guard let target else { return }
        saving = true; saveError = nil; saveProgress = 0; defer { saving = false }
        let name = title.trimmingCharacters(in: .whitespaces).isEmpty ? "Meeting " + Date().formatted(date: .abbreviated, time: .shortened) : title
        do {
            await library.waitForSearch()
            try await library.addNote(notebook: target, title: name, text: session.transcript, progress: { saveProgress = $0 })
            session.rememberNames()
            session.markSaved(title: name, notebookId: target, notebookTitle: notebookTitle)
            reload()
            NotificationCenter.default.post(name: .librarySynced, object: nil)
        } catch { saveError = "Couldn't save: \(error.localizedDescription)" }
    }
}
