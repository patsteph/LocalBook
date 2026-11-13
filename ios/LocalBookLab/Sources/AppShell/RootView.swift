import LocalBookCore
import LocalBookEngines
import SwiftUI

/// Pairing comes first; after that the app is four tabs, usable at once. Models load in the background, except when
/// the app was opened from a meeting-recorder launcher (widget, control, Siri): then the recorder comes up at once and
/// the answer model waits until the recorder is closed.
struct RootView: View {
    let library: LibraryModel
    @State private var tab = 0
    @State private var paired = MacLink.paired?.verified == true
    /// The recorder opened from a launcher, and the notebook it will save into (changeable before saving).
    @State private var quickRecord: QuickRecord?
    @State private var modelsStarted = false
    /// The launch-time wait is over; after this a launcher's request is handled as it arrives.
    @State private var settled = false
    @State private var openingRecorder = false
    private let launch = RecorderLaunch.shared

    struct QuickRecord: Identifiable { let id = UUID(); let notebookId: String }

    var body: some View {
        if paired {
            TabView(selection: $tab) {
                AskView(library: library)
                    .tabItem { Label("Ask", systemImage: "text.bubble") }.tag(0)
                NotebooksView(library: library, onAsk: { tab = 0 })
                    .tabItem { Label("Notebooks", systemImage: "books.vertical") }.tag(1)
                StudioView(library: library)
                    .tabItem { Label("Studio", systemImage: "sparkles.rectangle.stack") }.tag(2)
                SettingsView(library: library, onForget: { paired = false })
                    .tabItem { Label("Settings", systemImage: "gearshape") }.tag(3)
            }
            .task {
                // A launch from a widget or control arrives a moment after the first frame. Wait briefly for it before
                // loading 4+ GB of models the recorder doesn't need. (If it comes later still, the recorder puts the
                // models away and any load still in flight is dropped when it finishes.)
                await RecorderLaunch.settle(launch)
                settled = true
                if launch.requested, await openRecorderFromLauncher() { return }
                await startModels()
            }
            .onChange(of: launch.requested) { _, now in
                guard now, settled else { return }                 // during the launch wait, the task above handles it
                Task { await openRecorderFromLauncher() }
            }
            .sheet(item: $quickRecord, onDismiss: recorderClosed) { q in
                MeetingView(library: library, notebookId: q.notebookId, startRecording: true)
            }
        } else {
            OnboardingView(library: library, onDone: { paired = true })
                .onChange(of: launch.requested) { _, now in if now { launch.requested = false } }     // nothing to record into yet
        }
    }

    /// The normal start: search runs on the Neural Engine and the answer model on the GPU, so both start at once.
    private func startModels() async {
        guard !modelsStarted else { return }
        modelsStarted = true
        let t0 = Date()
        async let search: Void = {
            await library.prepareSearch()
            await library.loadVisionIfInstalled()
            SoakTrace.log("startup: search ready after \(String(format: "%.1f", Date().timeIntervalSince(t0))) s")
        }()
        async let answers: Void = {
            await library.prepareAnswerModel()
            SoakTrace.log("startup: answer model ready after \(String(format: "%.1f", Date().timeIntervalSince(t0))) s")
        }()
        _ = await (search, answers)
        await MeetingModels.prepare()          // after the search and answer models, so launch is not slowed
    }

    /// Straight to the recorder. Nothing heavy is loaded first: the recorder only needs the library (to know the
    /// notebooks) and the microphone.
    /// Returns false when the recorder could not be opened (it is already on screen, or there is no notebook yet).
    @discardableResult private func openRecorderFromLauncher() async -> Bool {
        launch.requested = false
        guard quickRecord == nil, !openingRecorder, MeetingView.onScreen == 0 else { return false }   // already recording or writing up
        openingRecorder = true
        defer { openingRecorder = false }
        await library.open()                                                            // the notebooks, no models
        let last = UserDefaults.standard.string(forKey: MeetingSession.openKey)        // a write-up left open: same notebook
            ?? library.scopeToNotebook
        guard MeetingView.onScreen == 0,
              let id = library.notebooks.first(where: { $0.id == last })?.id ?? library.notebooks.first?.id else { return false }
        SoakTrace.log("launch: meeting recorder from a launcher; answer model held")
        library.holdAnswerModel = true
        library.pauseModels()                    // frees anything already loaded; marks the rest as not wanted yet
        tab = 0
        quickRecord = QuickRecord(notebookId: id)
        return true
    }

    /// The recorder is closed: now the app opens as usual, with the answer model loaded.
    private func recorderClosed() {
        library.holdAnswerModel = false
        Task {
            await library.resumeModels()           // no-op if the write-up already brought search back
            if modelsStarted { await library.prepareAnswerModel() } else { await startModels() }
        }
    }
}

extension RecorderLaunch {
    /// Returns as soon as a launcher's request is seen, or after a short wait (well under a second) if none comes.
    static func settle(_ launch: RecorderLaunch) async {
        for _ in 0..<6 {
            if launch.requested { return }
            try? await Task.sleep(for: .milliseconds(100))
        }
    }
}
