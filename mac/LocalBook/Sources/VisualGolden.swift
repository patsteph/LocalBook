import AppKit
import LocalBookCore
import LocalBookEngines
import SwiftUI

/// Settings › Diagnostics › Visual golden set: the 15 asks of `VisualGoldenSet` drawn from one notebook by this
/// app's Studio engine, saved as PNGs (with the SVG and the spec beside each) and an index under
/// User Files/Diagnostics/visual-golden-<stamp>/ that also records the seconds each took and what the lint found, for
/// the side-by-side judgement against the old app's `old-app-visuals.html` (`scripts/visual_golden_export.py`).
/// Nothing stays in the Studio shelf: each item is removed once its picture is written.
@MainActor @Observable
final class VisualGoldenDiagnostics {
    var notebookId: String?
    private(set) var running = false
    private(set) var done = 0
    private(set) var current = ""
    private(set) var folder: URL?
    private(set) var drawn = 0
    var errorMessage: String?
    private var task: Task<Void, Never>?
    private let engine = StudioEngine()

    func run(library: LibraryModel) {
        guard !running, let id = notebookId, let notebook = library.notebooks.first(where: { $0.id == id }) else { return }
        guard library.gemma != nil else { errorMessage = "The answer model is still getting ready. Try again in a moment."; return }
        running = true; done = 0; drawn = 0; folder = nil
        let stamp = Int(Date().timeIntervalSince1970)
        let out = ParityDiagnostics.folder.appending(path: "visual-golden-\(stamp)", directoryHint: .isDirectory)
        try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
        task = Task { [weak self] in
            var made: [VisualGoldenSet.Made] = []
            for prompt in VisualGoldenSet.prompts {
                guard let self, !Task.isCancelled else { break }
                current = "\(prompt.layout.label) · \(prompt.detail.label)"
                let started = Date()
                let item = await engine.make(.visual, in: notebook, topic: prompt.topic, count: 0, notebookOnly: true,
                                             layout: prompt.layout, detail: prompt.detail, library: library)
                let seconds = Date().timeIntervalSince(started)
                if let item, let spec = try? JSONDecoder().decode(VisualSpec.self, from: Data(item.body.utf8)) {
                    StudioStore.delete(item)
                    let name = prompt.id + ".png"
                    let lint = VisualLint.check(VisualLayout.scene(for: spec)).map(\.description)
                    if Self.write(spec, to: out.appending(path: name)) {
                        made.append(.init(prompt: prompt, file: name, seconds: seconds, lint: lint)); drawn += 1
                    } else {
                        made.append(.init(prompt: prompt, problem: "the picture could not be written", seconds: seconds, lint: lint))
                    }
                    try? JSONEncoder().encode(spec).write(to: out.appending(path: prompt.id + ".json"))
                    try? VisualSlide.svg(for: spec).data(using: .utf8)?.write(to: out.appending(path: prompt.id + ".svg"))
                } else {
                    made.append(.init(prompt: prompt, problem: engine.error ?? "no drawing", seconds: seconds))
                }
                done += 1
            }
            guard let self else { return }
            let index = VisualGoldenSet.index(notebook: notebook.title, device: LibraryModel.deviceLine, date: Date(), made: made)
            try? index.data(using: .utf8)?.write(to: out.appending(path: "index.md"))
            let page = VisualGoldenSet.indexHTML(notebook: notebook.title, device: LibraryModel.deviceLine, date: Date(), made: made)
            try? page.data(using: .utf8)?.write(to: out.appending(path: "index.html"))
            folder = out
            running = false
            current = ""
        }
    }

    func stop() { task?.cancel(); engine.stop(); running = false }

    /// The slide at export size as a PNG, in light appearance.
    static func write(_ spec: VisualSpec, to url: URL) -> Bool {
        guard let image = VisualSlide.image(for: spec, fit: VisualSlide.fit(for: spec)),
              let cg = image.cgImage(forProposedRect: nil, context: nil, hints: nil),
              let png = NSBitmapImageRep(cgImage: cg).representation(using: .png, properties: [:]) else { return false }
        return (try? png.write(to: url)) != nil
    }
}

struct VisualGoldenSection: View {
    let golden: VisualGoldenDiagnostics
    let library: LibraryModel

    var body: some View {
        Section {
            Picker("Notebook", selection: Bindable(golden).notebookId) {
                Text("Choose…").tag(String?.none)
                ForEach(library.notebooks) { nb in Text(nb.title).tag(Optional(nb.id)) }
            }
            HStack {
                Button(golden.running ? "Drawing…" : "Draw the 15") { golden.run(library: library) }
                    .disabled(golden.running || golden.notebookId == nil)
                if golden.running { Button("Stop", role: .destructive) { golden.stop() } }
                if let folder = golden.folder {
                    Button("Open index") { NSWorkspace.shared.open(folder.appending(path: "index.html")) }
                    Button("Show in Finder") { NSWorkspace.shared.activateFileViewerSelecting([folder]) }
                }
            }
            if golden.running {
                ProgressView(value: Double(golden.done), total: 15) { Text("\(golden.done) of 15 · \(golden.current)").lineLimit(1) }
            } else if golden.folder != nil {
                LabeledContent("Drawn", value: "\(golden.drawn) of 15")
            }
        } header: { Text("Visual golden set · side by side with the old app") } footer: {
            Text("Draws the same 15 asks (three per layout, at each depth) from the chosen notebook with this app's Studio engine and saves the pictures with an index. On the Mac that runs the old app, scripts/visual_golden_export.py draws them there; judge the pairs. Each drawing takes as long as one Visual in Studio.")
        }
    }
}
