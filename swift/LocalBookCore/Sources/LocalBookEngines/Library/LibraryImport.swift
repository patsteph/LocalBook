import Foundation
import LocalBookCore

/// Moves the library of the LocalBook already on this Mac (the Python app) into this app, in one go and one way.
/// The old app must be running: it answers over loopback (`/migrate/…`, backend/api/migrate.py) with the same
/// shapes the phone link uses, for every notebook, plus each source's whole text and the Canvas notes. The old app's
/// folder is read once, for the token that lets a process on this Mac talk to it; nothing in it is changed.
public enum LibraryImport {
    public struct OldApp: Sendable {
        public let folder: URL
        public let token: String
        public let port: Int
        public let name: String
        public let notebooks: Int
        public let deviceOnly: Int
    }

    public struct Report: Sendable {
        public var notebooks = 0, sources = 0, chunks = 0, texts = 0, notes = 0, studio = 0
        public var seconds = 0.0
        public var problems: [String] = []
    }

    /// Where the old app keeps its data. The real home folder, not this app's sandbox container.
    public static var defaultFolder: URL {
        let home = getpwuid(getuid()).flatMap { String(cString: $0.pointee.pw_dir) } ?? NSHomeDirectory()
        return URL(fileURLWithPath: home).appending(path: "Library/Application Support/LocalBook", directoryHint: .isDirectory)
    }

    /// Reads the old app's token from its folder and asks it what it has. Call inside the folder's security scope.
    public static func find(folder: URL) async throws -> OldApp {
        let tokenURL = folder.appending(path: ".app_token")
        guard let raw = try? String(contentsOf: tokenURL, encoding: .utf8) else {
            throw LabError.model("That folder has no .app_token, so it is not the LocalBook folder (usually ~/Library/Application Support/LocalBook).")
        }
        let token = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        var port = 8000
        if let env = try? String(contentsOf: folder.appending(path: ".env"), encoding: .utf8) {
            for line in env.split(separator: "\n") where line.hasPrefix("API_PORT=") {
                port = Int(line.dropFirst("API_PORT=".count).trimmingCharacters(in: .whitespaces)) ?? port
            }
        }
        let call = loopback(token: token, port: port)
        let data: Data
        do { data = try await call("/migrate/hello", Data("{}".utf8)) }
        catch {
            throw LabError.model("The LocalBook on this Mac is not answering (port \(port)). Open it, wait for it to finish starting, and try again. (\(error.localizedDescription))")
        }
        guard let r = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw LabError.model("The old app gave an answer this app cannot read.") }
        return OldApp(folder: folder, token: token, port: port, name: r["name"] as? String ?? "LocalBook",
                      notebooks: r["notebooks"] as? Int ?? 0, deviceOnly: r["device_only"] as? Int ?? 0)
    }

    static func loopback(token: String, port: Int) -> LibraryCall {
        return { path, body in
            var req = URLRequest(url: URL(string: "http://127.0.0.1:\(port)\(path)")!)
            req.httpMethod = "POST"
            req.timeoutInterval = 300
            req.httpBody = body
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.setValue(token, forHTTPHeaderField: "x-localbook-token")
            let (data, response) = try await URLSession.shared.data(for: req)
            let status = (response as? HTTPURLResponse)?.statusCode ?? 0
            guard status == 200 else {
                let detail = String(decoding: data.prefix(200), as: UTF8.self)
                throw LabError.model(status == 401 ? "the old app refused the token (it changes on every launch; choose the folder again)" : "\(path): HTTP \(status) \(detail)")
            }
            return data
        }
    }

    /// Everything, in order: notebooks with their chunks and vectors (the sync, from the old app), the whole text of
    /// every source, the Canvas notes, then the Studio items. Safe to run again: each step skips what is here.
    public static func run(_ app: OldApp, store: LibraryStore, onNotebook: @escaping @Sendable () -> Void = {},
                           progress: @escaping @Sendable (String) -> Void) async throws -> Report {
        let started = Date()
        let call = loopback(token: app.token, port: app.port)
        var report = Report()
        let sum = try await LibrarySync.run(store: store, call: call, origin: .oldAppOnThisMac, onNotebook: onNotebook, progress: progress)
        report.notebooks = sum.notebooks; report.sources = sum.sourcesFetched; report.chunks = sum.chunksFetched

        // The whole text of each source, for reading (the chunks are pieces of it).
        for nb in await store.notebooks() {
            let sources = await store.sources(notebook: nb.id).filter { !$0.isLocal }
            var done = 0
            for s in sources {
                if await store.hasSourceText(notebook: nb.id, sourceId: s.id) { continue }
                do {
                    let data = try await call("/migrate/source_text", try JSONSerialization.data(withJSONObject: ["notebook_id": nb.id, "source_id": s.id]))
                    if let r = try JSONSerialization.jsonObject(with: data) as? [String: Any], let text = r["text"] as? String, !text.isEmpty {
                        try await store.setSourceText(notebook: nb.id, sourceId: s.id, text: text, url: r["url"] as? String, format: r["format"] as? String)
                        report.texts += 1
                    }
                } catch { report.problems.append("\(s.name): \(error.localizedDescription)") }
                done += 1
                if done % 10 == 0 { progress("\(nb.title): text of \(done) of \(sources.count) sources") }
            }
            // Canvas notes.
            do {
                let data = try await call("/migrate/notes", try JSONSerialization.data(withJSONObject: ["notebook_id": nb.id]))
                if let r = try JSONSerialization.jsonObject(with: data) as? [String: Any], let notes = r["notes"] as? [[String: Any]] {
                    for n in notes {
                        guard let id = n["id"] as? String else { continue }
                        let text = n["text"] as? String ?? ""
                        let title = (n["title"] as? String ?? "").trimmingCharacters(in: .whitespaces)
                        guard !text.isEmpty || !title.isEmpty else { continue }
                        try await store.addImportedNote(.init(id: id, notebookId: nb.id, title: title.isEmpty ? String(text.split(separator: "\n").first?.prefix(80) ?? "Note") : title,
                                                              text: text, kind: n["note_type"] as? String ?? "note", created: n["created_at"] as? String ?? ""))
                        report.notes += 1
                    }
                }
            } catch { report.problems.append("\(nb.title) notes: \(error.localizedDescription)") }
            progress("\(nb.title): text and notes moved")
        }

        progress("Studio items…")
        let before = StudioStore.all(notebook: nil).count
        if await StudioPull.pull(call: call, root: "/migrate/studio") {
            report.studio = max(0, StudioStore.all(notebook: nil).count - before)
        } else if let why = await StudioPull.lastProblem {
            report.problems.append("Studio items: \(why)")
        }
        try await store.setMeta("imported_from", app.folder.path)
        report.seconds = Date().timeIntervalSince(started)
        SoakTrace.log("import: \(report.notebooks) notebooks, \(report.chunks) chunks, \(report.texts) texts, \(report.notes) notes, \(report.studio) studio in \(Int(report.seconds)) s; \(report.problems.count) problems")
        return report
    }
}

extension LibraryModel {
    /// The old app's Canvas notes for a notebook (or all), moved over by the import.
    public func importedNotes(in notebook: String?) async -> [LibraryStore.ImportedNote] { await store?.importedNotes(notebook: notebook) ?? [] }

    /// Runs the import against the open library, as a sync: progress shows where sync does, the lists reload after.
    public func importOldApp(_ app: LibraryImport.OldApp, progress: @escaping @Sendable (String) -> Void) async throws -> LibraryImport.Report {
        guard let store else { throw LabError.model("The library is not open.") }
        syncState = .syncing("Moving your library…")
        do {
            let report = try await LibraryImport.run(app, store: store, onNotebook: { Task { @MainActor in await self.refresh() } }) { line in
                Task { @MainActor in if case .syncing = self.syncState { self.syncState = .syncing(line) } }
                progress(line)
            }
            await refresh()
            syncState = .idle
            return report
        } catch {
            syncState = .failed(error.localizedDescription)
            throw error
        }
    }
}
