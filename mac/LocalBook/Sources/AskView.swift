import Combine
import SwiftUI

/// The primary screen: a conversation with one notebook, the composer pinned at the bottom.
/// Phase 0 shows the empty state and a composer that cannot send yet; Phase 2 streams answers from the engine.
struct AskView: View {
    let notebook: AppModel.Notebook?
    @Environment(AppModel.self) private var model
    @State private var question = ""
    @FocusState private var composerFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                emptyState
                    .frame(maxWidth: 640)
                    .padding(.top, 80)
                    .padding(.horizontal, 24)
                    .frame(maxWidth: .infinity)
            }
            Divider()
            composer
        }
        .onReceive(NotificationCenter.default.publisher(for: .focusComposer)) { _ in composerFocused = true }
    }

    private var emptyState: some View {
        VStack(alignment: .leading, spacing: 12) {
            if let notebook {
                Text(notebook.name).font(.title2.weight(.semibold))
                Text("Ask anything in this notebook. Answers come from its sources and stay on this Mac.")
                    .foregroundStyle(.secondary)
            } else {
                Text("LocalBook").font(.title2.weight(.semibold))
                Text(model.isPaired
                     ? "Choose a notebook in the sidebar to start asking."
                     : "Pair with your Mac to bring your notebooks here, then ask anything. Everything stays on your devices.")
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var composer: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField(notebook == nil ? "Choose a notebook to ask" : "Ask \(notebook!.name)…", text: $question, axis: .vertical)
                .textFieldStyle(.plain)
                .lineLimit(1...6)
                .focused($composerFocused)
                .onSubmit(send)
                .disabled(notebook == nil)
            Button(action: send) {
                Image(systemName: "arrow.up.circle.fill").font(.title2)
            }
            .buttonStyle(.plain)
            .keyboardShortcut(.return, modifiers: [])
            .disabled(!canSend)
            .help("Send (Return)")
            .accessibilityLabel("Send")
        }
        .padding(12)
        .background(.bar)
    }

    private var canSend: Bool {
        notebook != nil && !question.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && model.isPaired
    }

    private func send() {
        guard canSend else { return }
        // Phase 2: hand the question to the engine and stream the answer.
    }
}

extension Notification.Name {
    /// Posted by the Ask menu item (⌘L): put the cursor in the composer.
    static let focusComposer = Notification.Name("LocalBook.focusComposer")
}
