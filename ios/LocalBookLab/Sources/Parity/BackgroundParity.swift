import BackgroundTasks
import Foundation

/// Runs the parity test as a user-started `BGContinuedProcessingTask`, so it keeps going
/// after you leave the app or lock the phone — the iOS 27 path we are betting on for
/// background ingest, transcription and voicing. With the Background Inference entitlement
/// the Neural Engine is allowed to keep working; without it, every item after the screen
/// locks should fail, and the report will say so.
enum BackgroundParity {
    /// Must match `BGTaskSchedulerPermittedIdentifiers` (`com.patsteph.localbook.lab.parity.*`).
    static let prefix = "com.patsteph.localbook.lab.parity"

    static func start(runner: ParityRunner,
                      tokenizerIds: [String: [Int]]?,
                      progress: @escaping @Sendable (Int, Int) -> Void,
                      finished: @escaping @Sendable (ParityReport) async -> Void) async throws {
        let identifier = "\(prefix).\(UUID().uuidString.prefix(8).lowercased())"
        let cancel = CancelFlag()

        let registered = BGTaskScheduler.shared.register(forTaskWithIdentifier: identifier, using: nil) { task in
            guard let continued = task as? BGContinuedProcessingTask else {
                task.setTaskCompleted(success: false)
                return
            }
            let box = UncheckedBox(continued)
            box.value.expirationHandler = { cancel.set() }
            // Report progress immediately: a continued task that reports none is expired.
            // Same total the runner uses: only items within the phone's 512-token cap.
            let cap = EmbeddingGemma2Engine.maxTokens
            let all = runner.fixture.chunks + runner.fixture.queries
            let eligible = all.filter { $0.tokenIds.count <= cap }.count
            let total = eligible * max(1, runner.passes) + (all.count - eligible)
            box.value.progress.totalUnitCount = Int64(total)
            box.value.progress.completedUnitCount = 0

            let job = Task {
                let report = await runner.run(
                    tokenizerIds: tokenizerIds,
                    isCancelled: { cancel.isSet },
                    progress: { done, total in
                        box.value.progress.totalUnitCount = Int64(total)
                        box.value.progress.completedUnitCount = Int64(done)
                        progress(done, total)
                    })
                await finished(report)
                box.value.setTaskCompleted(success: report.errors.isEmpty)
            }
            box.value.expirationHandler = {
                cancel.set()
                job.cancel()
            }
        }
        guard registered else { throw LabError.background("could not register \(identifier)") }

        let request = BGContinuedProcessingTaskRequest(
            identifier: identifier,
            title: "LocalBook parity test",
            subtitle: "Embedding on the Neural Engine")
        request.strategy = .fail   // tell us now if it can't start, rather than queueing
        do {
            try await BGTaskScheduler.shared.submitTaskRequest(request)
        } catch {
            throw LabError.background("submit failed: \(error.localizedDescription)")
        }
    }
}


/// Carries a non-Sendable system object (the background task) into a Task. The task is only
/// touched through its thread-safe `progress` and completion methods.
struct UncheckedBox<T>: @unchecked Sendable {
    let value: T
    init(_ value: T) { self.value = value }
}
