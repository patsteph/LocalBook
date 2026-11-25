import Foundation
import LocalBookCore

/// Settings › Diagnostics › Ingestion diff: the Phase 3 gate's 50-document check. The fixture written by the old
/// app (`scripts/ingestion_diff_export.py`) sits in the folder of documents; this Mac reads the same files with
/// `TextExtract`, cuts them with `Chunker` and the token budget as `ingestText` would, and `IngestionDiff` compares.
/// Nothing is added to the library: this is a read-only pass over the folder.
extension LibraryModel {
    public struct IngestionDiffFixture: Sendable {
        public let fixture: IngestionDiff.Fixture
        public let folder: URL
        public let bookmark: Data?
    }

    public static let fixtureName = "ingestion-fixture.json"

    /// Opens the fixture and remembers its folder (with a security-scoped bookmark on the Mac, so the files beside it
    /// can be read after the open panel closes). Pass the FOLDER the fixture sits in: the sandbox grants what the user
    /// picked and nothing beside it, so a fixture picked as a file leaves every document next to it unreadable.
    /// A fixture file is still accepted for a web fixture, which reads nothing from the folder.
    nonisolated public static func openIngestionFixture(_ url: URL) throws -> IngestionDiffFixture {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        let isFolder = (try? url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) ?? url.hasDirectoryPath
        let fixtureURL = isFolder ? url.appending(path: fixtureName) : url
        let fixture = try IngestionDiff.Fixture.load(try Data(contentsOf: fixtureURL))
        let folder = isFolder ? url : url.deletingLastPathComponent()
        #if os(macOS)
        let bookmark = try? folder.bookmarkData(options: .withSecurityScope, includingResourceValuesForKeys: nil, relativeTo: nil)
        #else
        let bookmark = try? folder.bookmarkData(options: [], includingResourceValuesForKeys: nil, relativeTo: nil)
        #endif
        return IngestionDiffFixture(fixture: fixture, folder: folder, bookmark: bookmark)
    }

    /// Reads every file the fixture names from the folder beside it and compares. `progress` hears one line per file.
    public func runIngestionDiff(_ opened: IngestionDiffFixture, progress: (@MainActor (Int, Int, String) -> Void)? = nil) async -> IngestionDiff.Report {
        await waitForSearch()
        let count: (String) -> Int
        let counterName: String
        if let tokenizer {
            count = { tokenizer.encode(text: $0, addSpecialTokens: false).count }
            counterName = "tokenizer"
        } else {
            count = TokenBudget.fallbackCount
            counterName = "estimate"
        }
        let folder = Self.resolveFolder(opened.bookmark) ?? opened.folder
        let scoped = folder.startAccessingSecurityScopedResource()
        defer { if scoped { folder.stopAccessingSecurityScopedResource() } }

        var results: [IngestionDiff.FileResult] = []
        let total = opened.fixture.files.count
        for (i, file) in opened.fixture.files.enumerated() {
            progress?(i, total, file.name)
            let url = folder.appending(path: file.name)
            let here: IngestionDiff.Here
            if let address = file.url, let link = URL(string: address) {
                // The web diff: the address is read here as `ingestLink` would, and chunked as the old app chunked
                // web sources (type "web" or "youtube", the title as the filename).
                do {
                    let (title, text) = try await Self.webText(link)
                    let raw = Chunker.chunks(text: text, sourceType: file.format, filename: title)
                    let chunks = TokenBudget.enforce(raw, budget: opened.fixture.tokenBudget, count: count)
                    here = .read(text: text, chunks: chunks)
                } catch is CancellationError {
                    break
                } catch {
                    here = .failed(error.localizedDescription)
                }
            } else if !FileManager.default.fileExists(atPath: url.path) {
                here = .missing
            } else if !TextExtract.canRead(url) {
                here = .unreadable
            } else {
                do {
                    let text = try await TextExtract.text(from: url)
                    let format = TextExtract.format(for: url) ?? file.format
                    let raw = Chunker.chunks(text: text, sourceType: format, filename: url.lastPathComponent)
                    let chunks = TokenBudget.enforce(raw, budget: opened.fixture.tokenBudget, count: count)
                    here = .read(text: text, chunks: chunks)
                } catch is CancellationError {
                    break
                } catch {
                    here = .failed(error.localizedDescription)
                }
            }
            results.append(IngestionDiff.compare(file, here: here))
            if Task.isCancelled { break }
        }
        progress?(total, total, "")
        return IngestionDiff.Report(fixture: opened.fixture, files: results, swiftTokenCounter: counterName, device: Self.deviceLine)
    }

    /// A web address as text: the page or transcript, or the downloaded document read by `TextExtract`.
    nonisolated static func webText(_ link: URL) async throws -> (title: String, text: String) {
        switch try await WebExtract.fetch(link) {
        case .text(let title, let text, _, _, _):
            return (title, text)
        case .file(let file, let name):
            defer { try? FileManager.default.removeItem(at: file.deletingLastPathComponent()) }
            return (name, try await TextExtract.text(from: file))
        }
    }

    nonisolated public static var deviceLine: String { "\(DeviceDescription.userName) (\(DeviceDescription.model), \(DeviceDescription.system))" }

    nonisolated static func resolveFolder(_ bookmark: Data?) -> URL? {
        guard let bookmark else { return nil }
        var stale = false
        #if os(macOS)
        return try? URL(resolvingBookmarkData: bookmark, options: .withSecurityScope, relativeTo: nil, bookmarkDataIsStale: &stale)
        #else
        return try? URL(resolvingBookmarkData: bookmark, options: [], relativeTo: nil, bookmarkDataIsStale: &stale)
        #endif
    }
}
