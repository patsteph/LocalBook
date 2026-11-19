import AppKit
import LocalBookCore
import LocalBookEngines
import Observation
import SwiftUI
import UniformTypeIdentifiers

/// What arrives by drag and drop (or an intent) for a notebook, and what becomes of it on this Mac today:
///
/// - text, and text files (txt, md, csv, rtf…) → a note, searchable here at once and sent to the paired Mac;
/// - a web address → a source: the page's article, a YouTube transcript, or the document it serves (`ingestLink`);
///   when the address cannot be read, a note holding it, so nothing is lost;
/// - a picture → a picture note, its text read with Vision;
/// - audio or video → a source holding the transcript, the recording kept beside it;
/// - PDF, Word, RTF, OpenDocument, HTML → a source made on this Mac (Phase 3): the old app's chunker, a summary and
///   questions from the answer model, embedded here; it stays on this Mac until Phase 4 makes this app the host;
/// - spreadsheets, CSV and PowerPoint → a source the same way, rows as sentences and slides under markers;
/// - anything else → the notebook's Inbox, a folder on this Mac, until its reader lands.
///
/// Notes still go the phone's note route, so they reach the Python Mac and every other device.
@MainActor @Observable
final class IntakeModel {
    struct Report: Equatable { var notes = 0; var sources = 0; var queued = 0; var failed: [String] = [] }

    private(set) var working = false
    var status = ""                                  // set by the folder scan too
    private(set) var lastReport: Report?
    private(set) var reportShownAt = Date.distantPast
    /// How far along a batch is: items finished of items taken, and the notebook they go to (for the activity strip).
    private(set) var done = 0
    private(set) var total = 0
    private(set) var notebookId = ""
    private(set) var reportNotebookId = ""

    private func start(_ count: Int, notebook: String) { working = true; status = "Adding to the notebook…"; done = 0; total = count; notebookId = notebook }

    /// Takes the finished report off the screen before its time is up.
    func dismissReport() { lastReport = nil }

    static func inbox(_ notebookId: String) -> URL {
        let url = AppFolders.userFiles.appending(path: "Inbox", directoryHint: .isDirectory).appending(path: notebookId, directoryHint: .isDirectory)
        try? FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    static func queued(_ notebookId: String) -> [URL] {
        ((try? FileManager.default.contentsOfDirectory(at: inbox(notebookId), includingPropertiesForKeys: [.contentModificationDateKey])) ?? [])
            .filter { !$0.lastPathComponent.hasPrefix(".") }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
    }

    /// Text kinds that become notes (and so reach the Python Mac). CSV is not here: it is a source, read by rows.
    static let textTypes: [UTType] = [.plainText, .utf8PlainText, .rtf, .json, .xml, .html, .sourceCode, .yaml]
    static let acceptedTypes: [UTType] = [.fileURL, .url, .plainText, .utf8PlainText, .text]

    /// Files chosen in an open panel, opened from the Finder or the Dock, or pasted: the same path as a drop.
    func take(files urls: [URL], notebook: String, library: LibraryModel) {
        guard !urls.isEmpty, !working else { return }
        start(urls.count, notebook: notebook)
        Task {
            var report = Report()
            for url in urls {
                do { try await file(url, notebook: notebook, library: library, report: &report) }
                catch { report.failed.append(error.localizedDescription) }
                done += 1
            }
            await finish(report, notebook: notebook, library: library)
        }
    }

    /// Web addresses typed, pasted or opened for one notebook.
    func take(links urls: [URL], notebook: String, library: LibraryModel) {
        guard !urls.isEmpty, !working else { return }
        start(urls.count, notebook: notebook)
        Task {
            var report = Report()
            for url in urls { await link(url, notebook: notebook, library: library, report: &report); done += 1 }
            await finish(report, notebook: notebook, library: library)
        }
    }

    /// The drag pasteboard of a drop, read directly: file URLs, web addresses (Safari and Chrome put the address under
    /// `public.url`, with a promised .webloc beside it that is ignored) and text. Returns false when it holds nothing
    /// this app can add, so the caller can try the providers.
    @discardableResult
    func take(pasteboard: NSPasteboard, notebook: String, library: LibraryModel) -> Bool {
        guard !working, let items = pasteboard.pasteboardItems, !items.isEmpty else { return false }
        SoakTrace.log("drop pasteboard: " + items.map { $0.types.map(\.rawValue).joined(separator: ", ") }.joined(separator: " | "))
        var files: [URL] = [], links: [URL] = [], texts: [String] = []
        let urlType = NSPasteboard.PasteboardType("public.url")
        for item in items {
            if let s = item.string(forType: .fileURL), let u = URL(string: s), u.isFileURL { files.append(u.standardizedFileURL); continue }
            if let s = item.string(forType: .URL) ?? item.string(forType: urlType), let u = URL(string: s.trimmingCharacters(in: .whitespacesAndNewlines)), Self.isWeb(u) { links.append(u); continue }
            if let s = item.string(forType: .string) {
                if let u = Self.soleLink(in: s) { links.append(u) } else if !s.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { texts.append(s) }
            }
        }
        guard !files.isEmpty || !links.isEmpty || !texts.isEmpty else { return false }
        start(files.count + links.count + texts.count, notebook: notebook)
        Task {
            var report = Report()
            for url in files {
                do { try await file(url, notebook: notebook, library: library, report: &report) }
                catch { report.failed.append(error.localizedDescription) }
                done += 1
            }
            for url in links { await link(url, notebook: notebook, library: library, report: &report); done += 1 }
            for text in texts {
                do { try await note(text, title: nil, notebook: notebook, library: library, report: &report) }
                catch { report.failed.append(error.localizedDescription) }
                done += 1
            }
            await finish(report, notebook: notebook, library: library)
        }
        return true
    }

    /// Everything the pasteboard offers for one drop, for one notebook.
    func take(_ providers: [NSItemProvider], notebook: String, library: LibraryModel) {
        guard !providers.isEmpty, !working else { return }
        start(providers.count, notebook: notebook)
        Task {
            var report = Report()
            for provider in providers {
                defer { done += 1 }
                let types = provider.registeredTypeIdentifiers
                SoakTrace.log("drop: \(types.joined(separator: ", "))")
                do {
                    // Safari's address bar drags a promised .webloc beside the address, which can look like a file
                    // without being one; only a real file URL is a file.
                    if Self.offers(provider, .fileURL), let url = try? await Self.loadURL(from: provider, type: .fileURL), url.isFileURL {
                        try await file(url, notebook: notebook, library: library, report: &report)
                    } else if let url = await Self.webAddress(from: provider) {
                        // A link dragged from a browser: Safari and Chrome offer it as public.url, as text, and (Safari) as a
                        // promised .webloc file; whichever of those loads first is the address.
                        await link(url, notebook: notebook, library: library, report: &report)
                    } else if Self.offers(provider, .plainText) || Self.offers(provider, .text) || Self.offers(provider, .html) || Self.offers(provider, .rtf) {
                        let text = try await Self.loadText(from: provider)
                        if let url = Self.soleLink(in: text) { await link(url, notebook: notebook, library: library, report: &report) }
                        else { try await note(text, title: nil, notebook: notebook, library: library, report: &report) }
                    } else {
                        report.failed.append("The drop offered nothing this app can add (\(types.isEmpty ? "no types" : types.prefix(4).joined(separator: ", ")))")
                    }
                } catch { report.failed.append(error.localizedDescription) }
            }
            await finish(report, notebook: notebook, library: library)
        }
    }

    /// Files kept in the Inbox earlier that this Mac can read now become sources too; then the result shows.
    private func finish(_ initial: Report, notebook: String, library: LibraryModel) async {
        var report = initial
        for url in Self.queued(notebook) where TextExtract.canRead(url) {
            do {
                try await library.ingestFile(url, notebook: notebook) { [weak self] s in self?.status = s }
                try? FileManager.default.removeItem(at: url)
                report.sources += 1
            } catch { report.failed.append(error.localizedDescription) }
        }
        working = false; status = ""
        lastReport = report; reportShownAt = Date(); reportNotebookId = notebook
        NotificationCenter.default.post(name: .librarySynced, object: nil)
    }

    /// What the open panel offers: everything a drop takes.
    static let openableTypes: [UTType] = [.pdf, .rtf, .rtfd, .html, .plainText, .utf8PlainText, .commaSeparatedText, .json, .xml, .yaml, .sourceCode,
                                          .image, .audio, .movie, .data]

    // MARK: one item

    private func file(_ url: URL, notebook: String, library: LibraryModel, report: inout Report) async throws {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        let type = UTType(filenameExtension: url.pathExtension) ?? .data
        let name = url.deletingPathExtension().lastPathComponent
        if type.conforms(to: .image) {
            status = "Reading \(url.lastPathComponent)…"
            guard let image = NSImage(contentsOf: url), let small = image.scaledDown(toLongSide: 1600),
                  let jpeg = NSBitmapImageRep(cgImage: small).representation(using: .jpeg, properties: [.compressionFactor: 0.8]) else {
                throw LabError.model("\(url.lastPathComponent) could not be opened as a picture")
            }
            let found = await PhotoText.read(small)
            try await library.addNote(notebook: notebook, title: name, text: found, kind: "image", photo: (small, jpeg))
            report.notes += 1
        } else if type.conforms(to: .audio) || type.conforms(to: .movie) {
            status = "Transcribing \(url.lastPathComponent)…"
            let copy = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + "." + url.pathExtension)
            try FileManager.default.copyItem(at: url, to: copy)
            defer { try? FileManager.default.removeItem(at: copy) }
            let words = try await MediaTranscriber.shared.transcribe(copy)
            guard !words.isEmpty else { throw LabError.model("no speech was found in \(url.lastPathComponent)") }
            // The transcript is a source in its own right (summary, questions, citations), with the recording kept beside it.
            let sourceId = LibraryModel.newSourceId()
            let kept = try? LibraryModel.keepOriginal(url, notebook: notebook, sourceId: sourceId)
            try await library.ingestText(words, title: url.lastPathComponent, format: type.conforms(to: .audio) ? "audio" : "video", notebook: notebook, url: nil,
                                         sourceId: sourceId, file: kept) { [weak self] s in self?.status = s }
            report.sources += 1
        } else if type.conforms(to: .commaSeparatedText) || ["xlsx", "pptx", "csv", "xls", "ppt", "ods", "epub", "ipynb", "svg"].contains(url.pathExtension.lowercased()) {
            try await library.ingestFile(url, notebook: notebook) { [weak self] s in self?.status = s }
            report.sources += 1
        } else if Self.textTypes.contains(where: { type.conforms(to: $0) }) {
            status = "Reading \(url.lastPathComponent)…"
            let text: String
            if type.conforms(to: .rtf) || type.conforms(to: .html) {
                text = (try? NSAttributedString(url: url, options: [:], documentAttributes: nil).string) ?? ""
            } else {
                text = (try? String(contentsOf: url, encoding: .utf8)) ?? (try? String(contentsOf: url, encoding: .isoLatin1)) ?? ""
            }
            guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw LabError.model("\(url.lastPathComponent) has no text") }
            try await note(text, title: name, notebook: notebook, library: library, report: &report)
        } else if TextExtract.canRead(url) {
            try await library.ingestFile(url, notebook: notebook) { [weak self] s in self?.status = s }
            report.sources += 1
        } else {
            status = "Keeping \(url.lastPathComponent) for the notebook…"
            var dest = Self.inbox(notebook).appending(path: url.lastPathComponent)
            var n = 2
            while FileManager.default.fileExists(atPath: dest.path) {
                dest = Self.inbox(notebook).appending(path: "\(name) \(n).\(url.pathExtension)"); n += 1
            }
            try FileManager.default.copyItem(at: url, to: dest)
            report.queued += 1
        }
    }

    /// A web address becomes a source; one this Mac cannot read stays as a note holding the address, and the report says why.
    private func link(_ url: URL, notebook: String, library: LibraryModel, report: inout Report) async {
        let host = (url.host() ?? "the link").replacingOccurrences(of: "www.", with: "")
        status = "Reading \(host)…"
        do {
            try await library.ingestLink(url, notebook: notebook) { [weak self] s in self?.status = s }
            report.sources += 1
        } catch {
            do {
                await library.waitForSearch()
                try await library.addNote(notebook: notebook, title: Self.linkTitle(url), text: url.absoluteString)
                report.notes += 1
                report.failed.append("\(host) could not be read (\(error.localizedDescription)); the address was kept as a note")
            } catch let noteError { report.failed.append("\(host): \(noteError.localizedDescription)") }
        }
    }

    /// Dropped or pasted text that is exactly one web address.
    nonisolated static func soleLink(in text: String) -> URL? {
        let s = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty, !s.contains(where: \.isWhitespace), let url = URL(string: s), let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https", url.host() != nil else { return nil }
        return url
    }

    /// Every web address in `text`, one per line or separated by spaces, for the Add Web Page sheet.
    nonisolated static func links(in text: String) -> [URL] {
        text.split(whereSeparator: { $0.isWhitespace || $0 == "," }).compactMap { soleLink(in: String($0)) }
    }

    private func note(_ text: String, title: String?, notebook: String, library: LibraryModel, report: inout Report) async throws {
        let body = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !body.isEmpty else { return }
        let heading = title ?? String(body.split(separator: "\n").first?.prefix(80) ?? "Dropped text")
        status = "Making “\(heading)” searchable…"
        await library.waitForSearch()
        try await library.addNote(notebook: notebook, title: heading, text: body)
        report.notes += 1
    }

    private static func linkTitle(_ url: URL) -> String {
        let host = (url.host() ?? "").replacingOccurrences(of: "www.", with: "")
        let path = url.path().trimmingCharacters(in: CharacterSet(charactersIn: "/")).replacingOccurrences(of: "-", with: " ").replacingOccurrences(of: "_", with: " ")
        return path.isEmpty ? host : "\(host): \(path.split(separator: "/").last ?? "")"
    }

    // MARK: pasteboard loading

    /// A file URL or a web address; the pasteboard hands either as a URL or as its bytes.
    /// Whether the provider carries `type`, by conformance or by name (some drags register a type SwiftUI does not
    /// resolve to a UTType, so the identifier itself is checked too).
    static func offers(_ provider: NSItemProvider, _ type: UTType) -> Bool {
        provider.hasItemConformingToTypeIdentifier(type.identifier) || provider.registeredTypeIdentifiers.contains(type.identifier)
    }

    /// The web address a dropped link carries, tried every way a browser offers it: `public.url`, then any other
    /// registered type whose name says url (Safari's `public.url-name`, Chrome's dynamic types), then an HTML anchor,
    /// then the text form. Nil when the drop is not a link.
    static func webAddress(from provider: NSItemProvider) async -> URL? {
        let registered = provider.registeredTypeIdentifiers
        var candidates = registered.filter { $0 == UTType.url.identifier }
        if candidates.isEmpty, provider.hasItemConformingToTypeIdentifier(UTType.url.identifier) { candidates.append(UTType.url.identifier) }
        candidates += registered.filter { $0 != UTType.url.identifier && $0 != UTType.fileURL.identifier && $0.lowercased().contains("url") }
        for id in candidates {
            if let url = try? await loadURL(from: provider, typeIdentifier: id), isWeb(url) { return url }
        }
        if offers(provider, .html), let html = try? await loadRaw(from: provider, typeIdentifier: UTType.html.identifier),
           let r = html.range(of: "href=[\"']([^\"']+)[\"']", options: .regularExpression) {
            let href = String(html[r]).replacingOccurrences(of: "^href=[\"']|[\"']$", with: "", options: .regularExpression)
            if let url = URL(string: href), isWeb(url) { return url }
        }
        for type in [UTType.utf8PlainText, .plainText, .text] where offers(provider, type) {
            if let text = try? await loadRaw(from: provider, typeIdentifier: type.identifier), let url = soleLink(in: text) { return url }
        }
        return nil
    }

    nonisolated static func isWeb(_ url: URL) -> Bool {
        let scheme = url.scheme?.lowercased() ?? ""
        return (scheme == "http" || scheme == "https") && url.host() != nil
    }

    private static func loadURL(from provider: NSItemProvider, type: UTType) async throws -> URL {
        try await loadURL(from: provider, typeIdentifier: type.identifier)
    }

    private static func loadURL(from provider: NSItemProvider, typeIdentifier: String) async throws -> URL {
        try await withCheckedThrowingContinuation { cont in
            provider.loadItem(forTypeIdentifier: typeIdentifier) { item, error in
                if let url = item as? URL { cont.resume(returning: url); return }
                if let url = item as? NSURL { cont.resume(returning: url as URL); return }
                if let data = item as? Data {
                    if let url = URL(dataRepresentation: data, relativeTo: nil) { cont.resume(returning: url); return }
                    if let s = String(data: data, encoding: .utf8), let url = soleLink(in: s) { cont.resume(returning: url); return }
                }
                if let s = item as? String, let url = URL(string: s.trimmingCharacters(in: .whitespacesAndNewlines)) { cont.resume(returning: url); return }
                cont.resume(throwing: error ?? LabError.model("the dropped item could not be read"))
            }
        }
    }

    /// One registered type as a string, however the provider hands it over.
    private static func loadRaw(from provider: NSItemProvider, typeIdentifier: String) async throws -> String {
        try await withCheckedThrowingContinuation { cont in
            provider.loadItem(forTypeIdentifier: typeIdentifier) { item, error in
                if let s = item as? String { cont.resume(returning: s); return }
                if let a = item as? NSAttributedString { cont.resume(returning: a.string); return }
                if let url = item as? URL { cont.resume(returning: url.absoluteString); return }
                if let d = item as? Data, let s = String(data: d, encoding: .utf8) ?? String(data: d, encoding: .utf16) { cont.resume(returning: s); return }
                cont.resume(throwing: error ?? LabError.model("the dropped text could not be read"))
            }
        }
    }

    /// Dropped text as plain words: plain text as it is, RTF through its attributed string, HTML with its tags removed.
    private static func loadText(from provider: NSItemProvider) async throws -> String {
        let type = [UTType.utf8PlainText, .plainText, .text, .rtf, .html].first { offers(provider, $0) } ?? .plainText
        let raw = try await loadRaw(from: provider, typeIdentifier: type.identifier)
        if type == .rtf, let a = try? NSAttributedString(data: Data(raw.utf8), options: [.documentType: NSAttributedString.DocumentType.rtf], documentAttributes: nil) {
            return a.string
        }
        if type == .html {
            return raw.replacingOccurrences(of: "<[^>]+>", with: " ", options: .regularExpression)
                .replacingOccurrences(of: "&nbsp;", with: " ").replacingOccurrences(of: "&amp;", with: "&")
                .replacingOccurrences(of: "&lt;", with: "<").replacingOccurrences(of: "&gt;", with: ">")
                .replacingOccurrences(of: "[ \\t]+", with: " ", options: .regularExpression)
        }
        return raw
    }
}

/// Lets a view take drops for a notebook. The drop lands in `model.intake`, which shows progress in the main window.
struct NotebookDrop: ViewModifier {
    @Environment(AppModel.self) private var model
    let notebookId: String?
    @State private var targeted = false

    func body(content: Content) -> some View {
        content
            .onDrop(of: IntakeModel.acceptedTypes, isTargeted: $targeted) { providers in
                guard let notebookId else { return false }
                // The drag pasteboard itself is read first: SwiftUI's providers for a browser's address-bar drag can
                // arrive with no types at all (seen on macOS 26 with Safari), while the pasteboard always has them.
                if !model.intake.take(pasteboard: NSPasteboard(name: .drag), notebook: notebookId, library: model.library) {
                    model.intake.take(providers, notebook: notebookId, library: model.library)
                }
                return true
            }
            .overlay {
                if targeted, notebookId != nil {
                    RoundedRectangle(cornerRadius: 8).strokeBorder(.tint, lineWidth: 2).padding(2).allowsHitTesting(false)
                }
            }
    }
}

extension View {
    func acceptsDrops(for notebookId: String?) -> some View { modifier(NotebookDrop(notebookId: notebookId)) }
}

/// A line at the bottom of the window while a drop is being added, and the result for a few seconds after.
/// What the intake is doing, docked under the content as a slim strip (never over the Ask field): the stage in words,
/// how many items of the batch are done, and a thin bar. When the batch ends, the strip turns into the result for a
/// few seconds (green when everything went in, orange when something could not be read, with the reason) and offers
/// the notebook's sources. Quiet otherwise: the strip is not there at all.
struct IntakeStrip: View {
    @Environment(AppModel.self) private var model
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let intake = model.intake
        Group {
            if intake.working {
                working(intake)
            } else if let r = intake.lastReport, Date().timeIntervalSince(intake.reportShownAt) < (r.failed.isEmpty ? 8 : 20) {
                TimelineView(.periodic(from: intake.reportShownAt, by: r.failed.isEmpty ? 8 : 20)) { _ in
                    result(r, notebook: intake.reportNotebookId)
                }
            }
        }
        .animation(reduceMotion ? nil : .easeInOut(duration: 0.2), value: intake.working)
    }

    private func title(_ id: String) -> String { model.library.notebooks.first { $0.id == id }?.title ?? "the notebook" }

    private func working(_ intake: IntakeModel) -> some View {
        VStack(spacing: 0) {
            Divider()
            HStack(spacing: 10) {
                Image(systemName: "tray.and.arrow.down").foregroundStyle(.tint)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Adding to \(title(intake.notebookId))").font(.callout.weight(.semibold))
                    Text(intake.status).font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                        .contentTransition(.opacity)
                }
                Spacer(minLength: 12)
                if intake.total > 1 {
                    Text("\(min(intake.done + 1, intake.total)) of \(intake.total)").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                }
                ProgressView().controlSize(.small)
            }
            .padding(.horizontal, 16).padding(.vertical, 8)
            ProgressView(value: intake.total > 1 ? Double(intake.done) / Double(intake.total) : nil)
                .progressViewStyle(.linear).controlSize(.mini)
                .padding(.horizontal, 16).padding(.bottom, 6)
        }
        .background(.bar)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Adding to \(title(intake.notebookId)): \(intake.status)")
    }

    private func result(_ r: IntakeModel.Report, notebook: String) -> some View {
        let ok = r.failed.isEmpty
        return VStack(spacing: 0) {
            Divider()
            HStack(spacing: 10) {
                Image(systemName: ok ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                    .foregroundStyle(ok ? Color.green : Color.orange)
                VStack(alignment: .leading, spacing: 2) {
                    Text(Self.headline(r, notebook: title(notebook))).font(.callout.weight(.semibold))
                    if let detail = Self.detail(r) { Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(2) }
                }
                .help(r.failed.joined(separator: "\n"))
                Spacer(minLength: 12)
                if r.sources + r.notes > 0 {
                    Button("Show Sources") { model.selection = .notebook(notebook); model.selection = .sources; model.intake.dismissReport() }
                        .controlSize(.small)
                }
                Button { model.intake.dismissReport() } label: { Image(systemName: "xmark").font(.caption.weight(.semibold)) }
                    .buttonStyle(.borderless).foregroundStyle(.secondary).help("Dismiss")
            }
            .padding(.horizontal, 16).padding(.vertical, 8)
        }
        .background(.bar)
        .overlay(alignment: .leading) { Rectangle().fill(ok ? Color.green : Color.orange).frame(width: 3) }
        .accessibilityElement(children: .combine)
    }

    /// "Added 8 sources and 4 notes to Harbor Lights", or what went wrong.
    static func headline(_ r: IntakeModel.Report, notebook: String) -> String {
        var parts: [String] = []
        if r.sources > 0 { parts.append("\(r.sources) source\(r.sources == 1 ? "" : "s")") }
        if r.notes > 0 { parts.append("\(r.notes) note\(r.notes == 1 ? "" : "s")") }
        if parts.isEmpty {
            if r.queued > 0 { return "\(r.queued) file\(r.queued == 1 ? "" : "s") kept in the Inbox for \(notebook)" }
            if let first = r.failed.first { return r.failed.count == 1 ? "Couldn't add that: \(first)" : "\(r.failed.count) items could not be added" }
            return "Nothing to add"
        }
        let added = "Added " + parts.joined(separator: " and ") + " to \(notebook)"
        return r.failed.isEmpty ? added : added + ", \(r.failed.count) could not be read"
    }

    static func detail(_ r: IntakeModel.Report) -> String? {
        var parts: [String] = []
        if r.queued > 0, r.sources + r.notes > 0 { parts.append("\(r.queued) file\(r.queued == 1 ? "" : "s") kept in the Inbox until this Mac can read \(r.queued == 1 ? "it" : "them")") }
        if let first = r.failed.first, r.sources + r.notes > 0 || r.failed.count > 1 { parts.append(first) }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// One line for logs and tests.
    static func words(_ r: IntakeModel.Report) -> String { headline(r, notebook: "the notebook") }
}
