import SwiftUI

/// The one screen shown while the app gets ready: your notebooks, search, and answers, each with a check, and one bar.
struct SetupView: View {
    let library: LibraryModel
    let onSkip: () -> Void

    private var steps: [(title: String, state: StepState)] {
        [("Your notebooks", library.notebooks.isEmpty ? .working : .done),
         ("Search", stepState(library.searchState == .ready, failed: failure(library.searchState))),
         ("Answers", stepState(library.answerModelState == .ready, failed: answerFailure))]
    }
    enum StepState { case working, done, failed(String) }

    var body: some View {
        VStack(spacing: 28) {
            Spacer()
            Image(systemName: "books.vertical.fill").font(.system(size: 54)).foregroundStyle(.tint)
            Text("Getting LocalBook ready").font(.title2.bold())
            VStack(alignment: .leading, spacing: 14) {
                ForEach(Array(steps.enumerated()), id: \.offset) { _, step in
                    HStack(alignment: .top, spacing: 12) {
                        switch step.state {
                        case .done: Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                        case .working: ProgressView().controlSize(.small)
                        case .failed: Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                        }
                        VStack(alignment: .leading, spacing: 2) {
                            Text(step.title).font(.body)
                            if case .failed(let why) = step.state { Text(why).font(.caption).foregroundStyle(.secondary) }
                        }
                    }
                }
            }
            .frame(maxWidth: 320, alignment: .leading)
            ProgressView(value: Double(done), total: Double(steps.count)).frame(maxWidth: 320)
            Text(hint).font(.footnote).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 320)
            Spacer()
            Button("Read my notebooks while I wait", action: onSkip).font(.footnote)
        }
        .padding(24).frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var done: Int { steps.filter { if case .done = $0.state { return true } else { return false } }.count }

    private var hint: String {
        library.answerModelState == .loading ? "Loading the answer model — about a minute. It stays loaded while the app is open." : "This happens each time the app opens."
    }

    private func stepState(_ ok: Bool, failed: String?) -> StepState { if let failed { return .failed(failed) }; return ok ? .done : .working }
    private func failure(_ s: LibraryModel.SearchState) -> String? { if case .failed(let why) = s { return why } else { return nil } }
    private var answerFailure: String? { if case .failed(let why) = library.answerModelState { return why } else { return nil } }
}
