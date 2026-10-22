import SwiftUI
import AVFoundation
import Speech
import Vision
import UIKit

/// Dictation: speech to text on the phone (on-device recognition where the language supports it).
@MainActor @Observable
final class Dictation {
    private(set) var listening = false
    private(set) var failure: String?
    private let recognizer = SFSpeechRecognizer(locale: Locale.current)
    private let engine = AVAudioEngine()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var onText: (@MainActor (String) -> Void)?

    func toggle(onText: @escaping @MainActor (String) -> Void) {
        if listening { stop(); return }
        failure = nil
        SFSpeechRecognizer.requestAuthorization { status in
            Task { @MainActor in
                guard status == .authorized else { self.failure = "Allow Speech Recognition for LocalBook in Settings to dictate."; return }
                AVAudioApplication.requestRecordPermission { ok in
                    Task { @MainActor in
                        guard ok else { self.failure = "Allow the microphone for LocalBook in Settings to dictate."; return }
                        self.start(onText)
                    }
                }
            }
        }
    }

    private func start(_ onText: @escaping @MainActor (String) -> Void) {
        guard let recognizer, recognizer.isAvailable else { failure = "Dictation isn't available right now."; return }
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.record, mode: .measurement, options: .duckOthers)
            try session.setActive(true, options: .notifyOthersOnDeactivation)
            let req = SFSpeechAudioBufferRecognitionRequest()
            req.shouldReportPartialResults = true
            req.requiresOnDeviceRecognition = recognizer.supportsOnDeviceRecognition      // stays on the phone when it can
            request = req
            self.onText = onText
            let input = engine.inputNode
            input.removeTap(onBus: 0)
            input.installTap(onBus: 0, bufferSize: 1024, format: input.outputFormat(forBus: 0)) { buf, _ in req.append(buf) }
            engine.prepare()
            try engine.start()
            listening = true
            task = recognizer.recognitionTask(with: req) { [weak self] result, error in
                Task { @MainActor in
                    guard let self else { return }
                    if let result { self.onText?(result.bestTranscription.formattedString) }
                    if error != nil || result?.isFinal == true { self.stop() }
                }
            }
        } catch { failure = "Couldn't start dictation: \(error.localizedDescription)"; stop() }
    }

    func stop() {
        guard listening || task != nil else { return }
        engine.stop()
        engine.inputNode.removeTap(onBus: 0)
        request?.endAudio()
        task?.cancel()
        request = nil; task = nil; onText = nil; listening = false
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }
}

/// Reads the text in a photo on the phone (Vision), so a photographed page or whiteboard becomes searchable.
enum PhotoText {
    static func read(_ image: UIImage) async -> String {
        guard let cg = image.cgImage else { return "" }
        return await withCheckedContinuation { cont in
            let req = VNRecognizeTextRequest { r, _ in
                let lines = (r.results as? [VNRecognizedTextObservation])?.compactMap { $0.topCandidates(1).first?.string } ?? []
                cont.resume(returning: lines.joined(separator: "\n"))
            }
            req.recognitionLevel = .accurate
            req.usesLanguageCorrection = true
            DispatchQueue.global(qos: .userInitiated).async {
                do { try VNImageRequestHandler(cgImage: cg).perform([req]) } catch { cont.resume(returning: "") }
            }
        }
    }
}

struct CameraPicker: UIViewControllerRepresentable {
    let onImage: (UIImage) -> Void
    @Environment(\.dismiss) private var dismiss
    func makeUIViewController(context: Context) -> UIImagePickerController {
        let p = UIImagePickerController()
        p.sourceType = UIImagePickerController.isSourceTypeAvailable(.camera) ? .camera : .photoLibrary
        p.delegate = context.coordinator
        return p
    }
    func updateUIViewController(_ vc: UIImagePickerController, context: Context) {}
    func makeCoordinator() -> Coordinator { Coordinator(self) }
    final class Coordinator: NSObject, UIImagePickerControllerDelegate, UINavigationControllerDelegate {
        let parent: CameraPicker
        init(_ p: CameraPicker) { parent = p }
        func imagePickerController(_ picker: UIImagePickerController, didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
            if let img = info[.originalImage] as? UIImage { parent.onImage(img) }
            parent.dismiss()
        }
        func imagePickerControllerDidCancel(_ picker: UIImagePickerController) { parent.dismiss() }
    }
}

/// Write, dictate, or photograph a note into a notebook.
struct NoteEditor: View {
    let library: LibraryModel
    let notebookId: String
    @Environment(\.dismiss) private var dismiss
    @State private var title = ""
    @State private var text = ""
    @State private var dictation = Dictation()
    @State private var dictationBase = ""
    @State private var camera = false
    @State private var reading = false
    @State private var saving = false
    @State private var fromPhoto = false
    @State private var photo: (image: CGImage, jpeg: Data, thumb: UIImage)?
    @State private var error: String?

    private var notebookTitle: String { library.notebooks.first { $0.id == notebookId }?.title ?? "Notebook" }

    var body: some View {
        NavigationStack {
            Form {
                Section { TextField("Title (optional)", text: $title) }
                Section {
                    TextEditor(text: $text).frame(minHeight: 220)
                        .overlay(alignment: .topLeading) {
                            if text.isEmpty { Text("Write, dictate or photograph a note…").foregroundStyle(.tertiary).padding(.top, 8).padding(.leading, 5).allowsHitTesting(false) }
                        }
                } footer: {
                    Text("Saved in “\(notebookTitle)” and searchable straight away. It is sent to your Mac when it is reachable and comes back through sync.")
                }
                if let photo {
                    Section {
                        HStack(spacing: 12) {
                            Image(uiImage: photo.thumb).resizable().scaledToFill().frame(width: 72, height: 72).clipShape(RoundedRectangle(cornerRadius: 10))
                            VStack(alignment: .leading, spacing: 2) {
                                Text("Photo attached").font(.subheadline)
                                Text(library.vision != nil ? "Searchable by what it shows." : "Turn on picture search in Settings to find it by what it shows.")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Button("Remove", role: .destructive) { self.photo = nil }.font(.footnote)
                        }
                    }
                }
                if reading { Section { HStack { ProgressView(); Text("Reading the photo…").font(.footnote) } } }
                if let msg = error ?? dictation.failure { Section { Text(msg).font(.footnote).foregroundStyle(.orange) } }
            }
            .navigationTitle("New note").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("Cancel") { dictation.stop(); dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { Task { await save() } }
                        .disabled(saving || (text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && photo == nil))
                }
                ToolbarItemGroup(placement: .bottomBar) {
                    Button {
                        dictationBase = text.isEmpty ? "" : text + "\n"
                        dictation.toggle { spoken in text = dictationBase + spoken }
                    } label: { Label(dictation.listening ? "Stop dictating" : "Dictate", systemImage: dictation.listening ? "stop.circle.fill" : "mic.fill") }
                    Spacer()
                    Button { dictation.stop(); camera = true } label: { Label("Photo", systemImage: "camera.fill") }
                }
            }
            .fullScreenCover(isPresented: $camera) {
                CameraPicker { image in
                    reading = true
                    Task {
                        let small = image.scaledDown(toLongSide: 1600)
                        if let cg = small.cgImage, let jpeg = small.jpegData(compressionQuality: 0.8) {
                            photo = (cg, jpeg, small.scaledDown(toLongSide: 160))
                            fromPhoto = true
                        }
                        let found = await PhotoText.read(image)
                        reading = false
                        if !found.isEmpty { text = text.isEmpty ? found : text + "\n\n" + found }
                    }
                }.ignoresSafeArea()
            }
        }
    }

    private func save() async {
        dictation.stop(); saving = true; defer { saving = false }
        do {
            try await library.addNote(notebook: notebookId, title: title, text: text, kind: fromPhoto ? "image" : "note",
                                      photo: photo.map { ($0.image, $0.jpeg) })
            dismiss()
        } catch { self.error = "Couldn't save: \(error.localizedDescription)" }
    }
}

/// Notes made on this phone, with delete.
struct MyNotesView: View {
    let library: LibraryModel
    @State private var notes: [LibraryStore.LocalNote] = []
    @State private var sent: Set<String> = []

    private func load() async {
        notes = await library.localNotes()
        var s: Set<String> = []
        for n in notes where await library.noteSentSourceId(n.id) != nil { s.insert(n.id) }
        sent = s
    }

    var body: some View {
        List {
            if let why = library.noteSendProblem { Label(why, systemImage: "wifi.slash").font(.footnote).foregroundStyle(.orange) }
            if notes.isEmpty { Text("No notes yet. Tap New note on the Ask or Notebooks screen.").foregroundStyle(.secondary) }
            ForEach(notes) { n in
                HStack(alignment: .top, spacing: 10) {
                    if n.hasPicture, let ui = UIImage(contentsOfFile: LibraryModel.photoURL(n.id).path) {
                        Image(uiImage: ui.scaledDown(toLongSide: 120)).resizable().scaledToFill().frame(width: 48, height: 48).clipShape(RoundedRectangle(cornerRadius: 8))
                    }
                VStack(alignment: .leading, spacing: 2) {
                    Text(n.title).font(.body)
                    Text((library.notebooks.first { $0.id == n.notebookId }?.title ?? "") + (sent.contains(n.id) ? " · sent to your Mac" : " · waiting to send"))
                        .font(.caption).foregroundStyle(.secondary)
                    Text(n.text).font(.footnote).foregroundStyle(.secondary).lineLimit(2)
                }
                }
                .swipeActions { Button("Delete", role: .destructive) { Task { await library.removeNote(n); notes = await library.localNotes() } } }
            }
        }
        .navigationTitle("My notes")
        .task {
            await load()                                   // show the list at once; sending can take minutes
            await library.flushNotes()
            await load()
        }
    }
}

extension UIImage {
    /// Scaled so the longer side is at most `side` points (pixels at scale 1); the same image if already smaller.
    func scaledDown(toLongSide side: CGFloat) -> UIImage {
        let longest = max(size.width, size.height) * scale
        guard longest > side else { return self }
        let f = side / longest
        let target = CGSize(width: size.width * scale * f, height: size.height * scale * f)
        let format = UIGraphicsImageRendererFormat(); format.scale = 1
        return UIGraphicsImageRenderer(size: target, format: format).image { _ in draw(in: CGRect(origin: .zero, size: target)) }
    }
}

/// Settings: the optional image encoder that lets a typed question find a photo by what it shows.
struct PictureSearchSection: View {
    let library: LibraryModel
    var body: some View {
        Section {
            switch library.visionState {
            case .none:
                Button("Turn on picture search (\(EmbeddingModelStore.visionDownloadSize))") { Task { await library.installVision() } }
                    .disabled(library.searchState != .ready)
            case .downloading(let p):
                HStack { ProgressView(value: p); Text("\(Int(p * 100))%").font(.footnote) }
            case .ready:
                Label("Picture search is on", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                Button("Turn off and remove", role: .destructive) { library.removeVision() }
            case .failed(let why):
                Text(why).font(.footnote).foregroundStyle(.orange)
                Button("Try again") { Task { await library.installVision() } }
            }
        } header: { Text("Picture search") } footer: {
            Text("Photos you add to a notebook become findable by what they show, not only by text in them. It downloads once, then works on this phone; nothing about your photos leaves it.")
        }
    }
}
