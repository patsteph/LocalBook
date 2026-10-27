import BackgroundTasks
import SwiftUI
import UIKit

/// A timed soak: answers questions on the phone back to back for N minutes and records, for each answer, how long
/// it took, how fast it decoded, the app's memory, the heat level and the battery. Lock the phone part-way
/// through: an answer that was running is cancelled by the lock guard (the GPU cannot run while locked), the
/// run notes it, and carries on with the next question when the app is active again.
struct SoakSample: Codable, Sendable, Identifiable {
    var id: Int { index }
    let index: Int
    let question: String
    let minutesIn: Double
    let seconds: Double
    let tokensPerSecond: Double?
    let answerChars: Int
    let memoryMB: Double
    let thermal: String
    let batteryPercent: Double
    let lowPowerMode: Bool
    let interruptedByLock: Bool
    var failure: String? = nil
}

struct SoakReport: Codable, Sendable {
    let createdAt: String
    let device: DeviceInfo
    let minutesRun: Double
    let batteryStart: Double
    let batteryEnd: Double
    let peakMemoryMB: Double
    let worstThermal: String
    let answersCompleted: Int
    let answersInterrupted: Int
    let lockEvents: Int
    let meanSeconds: Double
    let samples: [SoakSample]

    func json() -> Data {
        let e = JSONEncoder()
        e.outputFormatting = [.prettyPrinted, .sortedKeys]
        e.keyEncodingStrategy = .convertToSnakeCase
        return (try? e.encode(self)) ?? Data()
    }
}

@MainActor
@Observable
final class SoakModel {
    private(set) var running = false
    private(set) var status = ""
    private(set) var samples: [SoakSample] = []
    private(set) var reportURL: URL?
    private(set) var lockEvents = 0
    private var stop = false
    private let library: LibraryModel

    init(library: LibraryModel) { self.library = library }

    static func thermalName(_ s: ProcessInfo.ThermalState) -> String {
        switch s { case .nominal: "nominal"; case .fair: "fair"; case .serious: "serious"; case .critical: "critical"; @unknown default: "unknown" }
    }
    private static let thermalOrder = ["nominal", "fair", "serious", "critical"]

    func requestStop() { stop = true }

    func start(minutes: Int) async {
        guard !running else { return }
        let questions = AnswerParityModel(library: library).readQuestions().questions
        guard !questions.isEmpty else { status = "Needs the saved question file — open “Mac vs phone” and choose parity_fixture.json first."; return }
        running = true; stop = false; samples = []; lockEvents = 0; reportURL = nil
        defer { running = false; UIApplication.shared.isIdleTimerDisabled = false }
        UIDevice.current.isBatteryMonitoringEnabled = true
        UIApplication.shared.isIdleTimerDisabled = true           // the run is meant to be watched; locking is done by hand
        let started = Date()
        let batteryStart = Double(max(UIDevice.current.batteryLevel, 0)) * 100
        SoakTrace.reset()
        SoakTrace.log("soak start: \(minutes) min, \(questions.count) questions, background GPU supported by this phone: \(BGTaskScheduler.supportedResources.contains(.gpu))")
        let names: [(Notification.Name, String)] = [
            (UIApplication.willResignActiveNotification, "willResignActive"), (UIApplication.didEnterBackgroundNotification, "didEnterBackground"),
            (UIApplication.willEnterForegroundNotification, "willEnterForeground"), (UIApplication.didBecomeActiveNotification, "didBecomeActive"),
            (UIApplication.protectedDataWillBecomeUnavailableNotification, "protectedDataWillBecomeUnavailable"),
            (UIApplication.didReceiveMemoryWarningNotification, "MEMORY WARNING")]
        let tokens = names.map { (name, label) in
            NotificationCenter.default.addObserver(forName: name, object: nil, queue: .main) { _ in
                SoakTrace.log("EVENT \(label)")
                if name == UIApplication.willResignActiveNotification { MainActor.assumeIsolated { self.lockEvents += 1 } }
            }
        }
        defer { tokens.forEach { NotificationCenter.default.removeObserver($0) }; SoakTrace.log("soak end") }

        var i = 0
        while !stop, Date().timeIntervalSince(started) < Double(minutes) * 60 {
            while !GPUGuard.canStart && !stop { try? await Task.sleep(for: .seconds(1)) }       // never start GPU work while not in front
            let q = questions[i % questions.count]
            status = "Answer \(i + 1) · \(Int(Date().timeIntervalSince(started) / 60)) of \(minutes) min · lock the phone whenever you like"
            samples.append(await one(index: i, question: q, started: started))
            writeReport(started: started, batteryStart: batteryStart)
            i += 1
        }
        status = stop ? "Stopped after \(samples.count) answers." : "Done: \(samples.count) answers in \(minutes) min."
    }

    private func one(index: Int, question: String, started: Date) async -> SoakSample {
        let t0 = Date()
        var interrupted = false
        var result: LibraryModel.LocalAnswer?
        var failure: String?
        do {
            guard let nb = try await library.bestNotebook(for: question) else { throw LibraryError.badResponse("no matching notebook") }
            SoakTrace.log("answer \(index + 1) begins")
            let task = Task { try await library.localAnswer(question: question, notebook: nb) }
            let flag = LockFlag()
            let observer = GPUGuard.protect(task, label: "soak answer \(index + 1)") { flag.set() }      // GPU work must stop when the screen locks
            defer { NotificationCenter.default.removeObserver(observer) }
            do { result = try await task.value } catch is CancellationError { interrupted = true } catch { failure = error.localizedDescription }
            if flag.isSet { interrupted = true }
        } catch { failure = error.localizedDescription }
        SoakTrace.log("answer \(index + 1) returned (interrupted=\(interrupted), failure=\(failure ?? "none"))")
        if interrupted {                                          // wait for the app to be active again before the next question
            while UIApplication.shared.applicationState != .active && !stop { try? await Task.sleep(for: .seconds(1)) }
        }
        return SoakSample(
            index: index, question: question, minutesIn: Date().timeIntervalSince(started) / 60,
            seconds: Date().timeIntervalSince(t0), tokensPerSecond: result?.tokensPerSecond, answerChars: result?.answer.count ?? 0,
            memoryMB: MemoryProbe.footprintMB(), thermal: Self.thermalName(ProcessInfo.processInfo.thermalState),
            batteryPercent: Double(max(UIDevice.current.batteryLevel, 0)) * 100, lowPowerMode: ProcessInfo.processInfo.isLowPowerModeEnabled,
            interruptedByLock: interrupted, failure: failure)
    }

    private func writeReport(started: Date, batteryStart: Double) {
        let done = samples.filter { !$0.interruptedByLock && $0.failure == nil }
        let report = SoakReport(
            createdAt: ISO8601DateFormatter().string(from: Date()), device: DeviceInfo.current(),
            minutesRun: Date().timeIntervalSince(started) / 60, batteryStart: batteryStart, batteryEnd: samples.last?.batteryPercent ?? batteryStart,
            peakMemoryMB: samples.map(\.memoryMB).max() ?? 0,
            worstThermal: samples.map(\.thermal).max { (Self.thermalOrder.firstIndex(of: $0) ?? 0) < (Self.thermalOrder.firstIndex(of: $1) ?? 0) } ?? "nominal",
            answersCompleted: done.count, answersInterrupted: samples.filter(\.interruptedByLock).count, lockEvents: lockEvents,
            meanSeconds: done.isEmpty ? 0 : done.map(\.seconds).reduce(0, +) / Double(done.count), samples: samples)
        let url = reportURL ?? FileManager.default.temporaryDirectory.appending(path: "soak-\(Int(started.timeIntervalSince1970)).json")
        try? report.json().write(to: url)
        try? report.json().write(to: URL.documentsDirectory.appending(path: url.lastPathComponent))
        reportURL = url
    }
}

struct SoakView: View {
    @State private var model: SoakModel
    @State private var minutes = 10
    init(library: LibraryModel) { _model = State(initialValue: SoakModel(library: library)) }

    var body: some View {
        Form {
            Section {
                Stepper("Run for \(minutes) min", value: $minutes, in: 2...60, step: 2)
                if model.running {
                    HStack { ProgressView(); Text(model.status).font(.footnote) }
                    Button("Stop", role: .destructive) { model.requestStop() }
                } else {
                    Button("Start soak test") { Task { await model.start(minutes: minutes) } }
                    if !model.status.isEmpty { Text(model.status).font(.footnote).foregroundStyle(.secondary) }
                }
            } footer: {
                Text("Answers questions back to back and records time, speed, memory, heat and battery. Lock the phone for ~20 s part-way through, then wake it: the answer that was running is cancelled and the run carries on.")
            }
            Section { LabeledContent("Background GPU on this phone", value: BGTaskScheduler.supportedResources.contains(.gpu) ? "supported" : "not supported") }
            if let last = model.samples.last {
                Section("Latest") {
                    LabeledContent("Answers", value: "\(model.samples.count) · locks \(model.lockEvents)")
                    LabeledContent("Memory", value: String(format: "%.0f MB", last.memoryMB))
                    LabeledContent("Heat", value: last.thermal)
                    LabeledContent("Battery", value: String(format: "%.0f %%", last.batteryPercent))
                    LabeledContent("Last answer", value: String(format: "%.1f s", last.seconds) + (last.interruptedByLock ? " (interrupted by lock)" : ""))
                }
            }
            if let url = model.reportURL { Section { ShareLink("Share report", item: url) } }
            Section { Text("If the app hangs while locked, force-quit it, reopen, and share soak-trace.txt from the app's Files folder.").font(.footnote) }
        }
        .navigationTitle("Soak test")
    }
}
