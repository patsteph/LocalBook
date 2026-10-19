import SwiftUI

struct SpeechTestView: View {
    @State private var model = SpeechBenchModel()
    @State private var speakRepeats = 6
    @State private var transcribeRepeats = 20

    var body: some View {
        Form {
            Section {
                switch model.state {
                case .notLoaded:
                    Button("Download and load the speech models") { Task { await model.load() } }
                case .loading(let what):
                    HStack { ProgressView(); Text(what) }
                case .failed(let why):
                    Text(why).foregroundStyle(.red)
                    Button("Try again") { Task { await model.load() } }
                case .running(let what):
                    HStack { ProgressView(); Text(what) }
                    if !model.progressNote.isEmpty { Text(model.progressNote).font(.footnote).foregroundStyle(.secondary) }
                case .ready:
                    Label("Models loaded", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                }
            } header: { Text("Models") } footer: {
                Text("Kokoro (voice) and Parakeet (transcription) via FluidAudio. Keep the phone plugged in; turn the volume up a little.")
            }

            if case .ready = model.state {
                Section {
                    Button("1. Read the passage aloud (about 3 minutes)") { Task { await model.readAloud() } }
                } header: { Text("Locked-screen playback") } footer: {
                    Text("Tap it, wait for the first words, then press the side button to lock the phone. Leave it until the voice stops, then unlock. Playback should not pause or stutter.")
                }
                Section {
                    Stepper("Passes over the text: \(speakRepeats)", value: $speakRepeats, in: 2...20)
                    Button("2. Voice the text in the background (no sound)") { Task { await model.speakInBackground(repeats: speakRepeats) } }
                } header: { Text("Locked-screen speech generation") } footer: {
                    Text("Starts a system background task. Lock the phone right after tapping, wait about a minute, unlock.")
                }
                Section {
                    Button("3. Transcribe the audio (screen on)") { Task { await model.transcribe(background: false, repeats: 1) } }
                        .disabled(!model.hasAudio)
                    Stepper("Passes: \(transcribeRepeats)", value: $transcribeRepeats, in: 5...60)
                    Button("4. Transcribe in the background (lock the phone)") {
                        Task { await model.transcribe(background: true, repeats: transcribeRepeats) }
                    }.disabled(!model.hasAudio)
                } header: { Text("Transcription") } footer: {
                    Text("Needs step 1 first: it listens to what step 1 said, so the right answer is known.")
                }
            }

            if !model.phases.isEmpty {
                Section("Results") {
                    ForEach(model.phases) { p in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(p.name).font(.body.weight(.medium))
                            Text("\(fmt(p.realtimeFactor))× real time"
                                 + (p.wordErrorRate.map { " · \(fmt($0 * 100))% words wrong" } ?? "")
                                 + " · \(p.passesInBackground) of \(p.kind == "speak" ? p.chunks.count : p.passes) in background"
                                 + (p.kind == "speak" && p.name == "Read aloud" ? " · \(p.underruns) gap(s)" : "")
                                 + " · peak \(Int(p.peakFootprintMB)) MB · \(p.thermalEnd)")
                                .font(.footnote).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            if let r = model.report {
                Section {
                    row("Speech ≥ \(fmt(r.bars.speechRealtimeFactorMin))× real time", r.verdict.speechFast)
                    row("No silent gaps in playback", r.verdict.noGaps)
                    row("Background runs worked", r.verdict.backgroundWorked)
                    row("Transcription ≥ \(fmt(r.bars.transcribeRealtimeFactorMin))× real time", r.verdict.transcribeFast)
                    row("Words wrong ≤ \(fmt(r.bars.wordErrorRateMax * 100))%", r.verdict.accurate)
                    if let url = model.reportURL { ShareLink("Share report", item: url) }
                } header: { Text(r.verdict.pass ? "Pass" : "Not yet — run every step") }
            }
            if !model.heard.isEmpty {
                Section("What the transcriber heard") { Text(model.heard).font(.footnote) }
            }
        }
        .navigationTitle("Speech")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func row(_ title: String, _ ok: Bool) -> some View {
        HStack {
            Image(systemName: ok ? "checkmark.circle.fill" : "xmark.circle.fill").foregroundStyle(ok ? .green : .red)
            Text(title)
        }
    }
    private func fmt(_ v: Double) -> String { v.isNaN ? "–" : v.formatted(.number.precision(.fractionLength(1))) }
}
