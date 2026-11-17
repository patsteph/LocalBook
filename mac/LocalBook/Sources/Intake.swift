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
    private(set) var status = ""
    private(set) var lastReport: Report?
    private(set) var reportShownAt = Date.distantPast

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
        working = true; status = "Adding to the notebook…"
        Task {
            var report = Report()
            for url in urls {
                do { try await file(url, notebook: notebook, library: library, report: &report) }
                catch { report.failed.append(error.localizedDescription) }
            }
            await finish(report, notebook: notebook, library: library)
        }
    }

    /// Web addresses typed, pasted or opened for one notebook.
    func take(links urls: [URL], notebook: String, library: LibraryModel) {
        guard !urls.isEmpty, !working else { return }
        working = true; status = "Adding to the notebook…"
        Task {
            var report = Report()
            for url in urls { await link(url, notebook: notebook, library: library, report: &report) }
            await finish(report, notebook: notebook, library: library)
        }
    }

    /// Everything the pasteboard offers for one drop, for one notebook.
    func take(_ providers: [NSItemProvider], notebook: String, library: LibraryModel) {
        guard !providers.isEmpty, !working else { return }
        working = true; status = "Adding to the notebook…"
        Task {
            var report = Report()
            for provider in providers {
                do {
                    if provider.hasItemConformingToTypeIdentifier(UTType.fileURL.identifier) {
                        let url = try await Self.loadURL(from: provider, type: .fileURL)
                        try await file(url, notebook: notebook, library: library, report: &report)
                    } else if provider.hasItemConformingToTypeIdentifier(UTType.url.identifier) {
                        let url = try await Self.loadURL(from: provider, type: .url)
                        await link(url, notebook: notebook, library: library, report: &report)
                    } else if provider.hasItemConformingToTypeIdentifier(UTType.plainText.identifier) || provider.hasItemConformingToTypeIdentifier(UTType.text.identifier) {
                        let text = try await Self.loadText(from: provider)
                        if let url = Self.soleLink(in: text) { await link(url, notebook: notebook, library: library, report: &report) }
                        else { try await note(text, title: nil, notebook: notebook, library: library, report: &report) }
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
        lastReport = report; reportShownAt = Date()
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
    static func soleLink(in text: String) -> URL? {
        let s = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty, !s.contains(where: \.isWhitespace), let url = URL(string: s), let scheme = url.scheme?.lowercased(),
              scheme == "http" || scheme == "https", url.host() != nil else { return nil }
        return url
    }

    /// Every web address in `text`, one per line or separated by spaces, for the Add Web Page sheet.
    static func links(in text: String) -> [URL] {
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
    private static func loadURL(from provider: NSItemProvider, type: UTType) async throws -> URL {
        try await withCheckedThrowingContinuation { cont in
            provider.loadItem(forTypeIdentifier: type.identifier) { item, error in
                if let url = item as? URL { cont.resume(returning: url); return }
                if let url = item as? NSURL { cont.resume(returning: url as URL); return }
                if let data = item as? Data, let url = URL(dataRepresentation: data, relativeTo: nil) { cont.resume(returning: url); return }
                if let s = item as? String, let url = URL(string: s) { cont.resume(returning: url); return }
                cont.resume(throwing: error ?? LabError.model("the dropped item could not be read"))
            }
        }
    }

    private static func loadText(from provider: NSItemProvider) async throws -> String {
        try await withCheckedThrowingContinuation { cont in
            provider.loadItem(forTypeIdentifier: UTType.plainText.identifier) { item, error in
                if let s = item as? String { cont.resume(returning: s); return }
                if let d = item as? Data, let s = String(data: d, encoding: .utf8) { cont.resume(returning: s); return }
                if let a = item as? NSAttributedString { cont.resume(returning: a.string); return }
                cont.resume(throwing: error ?? LabError.model("the dropped text could not be read"))
            }
        }
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
                model.intake.take(providers, notebook: notebookId, library: model.library)
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
struct IntakeBanner: View {
    @Environment(AppModel.self) private var model

    var body: some View {
        let intake = model.intake
        if intake.working {
            HStack(spacing: 8) { ProgressView().controlSize(.small); Text(intake.status).font(.callout) }
                .padding(.horizontal, 14).padding(.vertical, 8).background(.regularMaterial, in: Capsule())
        } else if let r = intake.lastReport, Date().timeIntervalSince(intake.reportShownAt) < 6 {
            TimelineView(.periodic(from: intake.reportShownAt, by: 6)) { _ in
                Text(Self.words(r)).font(.callout).padding(.horizontal, 14).padding(.vertical, 8).background(.regularMaterial, in: Capsule())
            }
        }
    }

    static func words(_ r: IntakeModel.Report) -> String {
        var parts: [String] = []
        if r.notes > 0 { parts.append("\(r.notes) note\(r.notes == 1 ? "" : "s") added") }
        if r.sources > 0 { parts.append("\(r.sources) source\(r.sources == 1 ? "" : "s") added") }
        if r.queued > 0 { parts.append("\(r.queued) file\(r.queued == 1 ? "" : "s") kept in the Inbox until this Mac can read them") }
        if !r.failed.isEmpty { parts.append("\(r.failed.count) could not be read") }
        return parts.isEmpty ? "Nothing to add" : parts.joined(separator: " · ")
    }
}
