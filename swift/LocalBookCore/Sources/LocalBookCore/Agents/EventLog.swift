import Foundation

/// What happened in LocalBook, one line per event (`Events/events.jsonl`): sources added, questions asked, Studio
/// items and insights made, what the Collector found, briefs written. The Curator's brief reads it; agents read it
/// through `events_since` with a cursor.
public struct LBEvent: Codable, Sendable, Hashable, Identifiable {
    public var id: String
    public var kind: String
    public var notebookId: String?
    public var title: String
    public var detail: String
    public var at: Date
    enum CodingKeys: String, CodingKey { case id, kind, notebookId = "notebook_id", title, detail, at }
    public init(kind: String, notebookId: String?, title: String, detail: String = "", at: Date = Date()) {
        // Ids sort by time: the cursor is the last id a client saw.
        self.id = String(format: "%013.0f-%@", at.timeIntervalSince1970 * 1000, String(UUID().uuidString.prefix(8)))
        self.kind = kind; self.notebookId = notebookId; self.title = title; self.detail = detail; self.at = at
    }
}

public enum EventLog {
    public static let kinds: [(kind: String, meaning: String)] = [
        ("source_added", "A source joined a notebook (by the user, the Collector or research)"),
        ("question_asked", "A question was asked and answered in a notebook"),
        ("studio_made", "A Studio item was made"), ("insight_made", "An insight was made"),
        ("collector_found", "The Collector queued an item for approval"), ("collector_stored", "The Collector added a source on its own"),
        ("brief_written", "The Curator wrote a brief"), ("connection_found", "The Curator found a cross-notebook connection"),
        ("research_done", "A research job finished"), ("mail_ingested", "The Correspondent added a newsletter or forward to a notebook"),
        ("mail_queued", "The Correspondent is waiting for a notebook to be picked"), ("journal_sent", "The weekly journal was mailed")]

    public static var folder: URL {
        let dir = AppFolders.base.appendingPathComponent("Events", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }
    static var url: URL { folder.appendingPathComponent("events.jsonl") }
    private static let lock = NSLock()

    public static func record(_ kind: String, notebook: String?, title: String, detail: String = "") {
        append(LBEvent(kind: kind, notebookId: notebook, title: title, detail: detail))
    }
    public static func append(_ e: LBEvent) {
        let enc = JSONEncoder(); enc.dateEncodingStrategy = .iso8601
        guard var line = try? enc.encode(e) else { return }
        line.append(0x0A)
        lock.lock(); defer { lock.unlock() }
        if let h = try? FileHandle(forWritingTo: url) { defer { try? h.close() }; _ = try? h.seekToEnd(); try? h.write(contentsOf: line) }
        else { try? line.write(to: url) }
    }

    public static func all() -> [LBEvent] {
        lock.lock(); defer { lock.unlock() }
        guard let data = try? Data(contentsOf: url), let text = String(data: data, encoding: .utf8) else { return [] }
        let d = JSONDecoder(); d.dateDecodingStrategy = .iso8601
        return text.split(separator: "\n").compactMap { try? d.decode(LBEvent.self, from: Data($0.utf8)) }
    }
    public static func since(_ date: Date) -> [LBEvent] { all().filter { $0.at >= date } }
    /// Events after the cursor (an event id), oldest first, with the cursor to pass next time.
    public static func since(cursor: String?, limit: Int = 100) -> (events: [LBEvent], cursor: String) {
        let events = all().sorted { $0.id < $1.id }
        let after = cursor.map { c in events.filter { $0.id > c } } ?? events
        let page = Array(after.prefix(limit))
        return (page, page.last?.id ?? cursor ?? events.last?.id ?? "")
    }
    public static func clear() { lock.lock(); try? FileManager.default.removeItem(at: url); lock.unlock() }
}
