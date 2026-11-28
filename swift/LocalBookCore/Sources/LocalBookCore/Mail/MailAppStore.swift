import Foundation
import SQLite3

/// Mail.app's store on this Mac, read in place: the Envelope Index (SQLite) for what arrived and where, and the
/// `.emlx` files for the messages themselves. Nothing is written here; moving, marking and deleting go through Mail
/// itself (the engine's Apple events). The folder is the user's `~/Library/Mail` (or its `V10` child), granted once.
public enum MailAppStore {
    public struct InboxMessage: Sendable, Hashable {
        public var rowId: Int
        public var mailboxURL: String
        public var dateReceived: Date
        public var read: Bool
    }
    public struct Mailbox: Sendable, Hashable, Identifiable {
        public var url: String
        public var id: String { url }
        /// "imap://rosa%40example.com@imap.example.com/INBOX" → "rosa@example.com (imap.example.com)".
        public var label: String {
            guard let u = URL(string: url) else { return url }
            let user = (u.user(percentEncoded: false) ?? "").isEmpty ? "" : (u.user(percentEncoded: false) ?? "")
            let host = u.host() ?? ""
            return user.isEmpty ? url : (host.isEmpty ? user : "\(user) (\(host))")
        }
    }

    /// The index file under a Mail folder: `<folder>/V*/MailData/Envelope Index`, the newest version wins.
    public static func indexURL(in mailFolder: URL) -> URL? {
        let fm = FileManager.default
        let direct = mailFolder.appendingPathComponent("MailData/Envelope Index")
        if fm.fileExists(atPath: direct.path) { return direct }
        let versions = ((try? fm.contentsOfDirectory(at: mailFolder, includingPropertiesForKeys: nil)) ?? [])
            .filter { $0.lastPathComponent.hasPrefix("V") && Int($0.lastPathComponent.dropFirst()) != nil }
            .sorted { (Int($0.lastPathComponent.dropFirst()) ?? 0) > (Int($1.lastPathComponent.dropFirst()) ?? 0) }
        for v in versions {
            let u = v.appendingPathComponent("MailData/Envelope Index")
            if fm.fileExists(atPath: u.path) { return u }
        }
        return nil
    }

    /// The inboxes the index knows, for the account picker.
    public static func inboxes(index: URL) throws -> [Mailbox] {
        let db = try open(index)
        defer { sqlite3_close(db) }
        var out: [Mailbox] = []
        let s = try statement(db, "SELECT url FROM mailboxes WHERE url LIKE '%/INBOX' OR url LIKE '%/Inbox' ORDER BY url")
        defer { sqlite3_finalize(s) }
        while sqlite3_step(s) == SQLITE_ROW { if let c = sqlite3_column_text(s, 0) { out.append(Mailbox(url: String(cString: c))) } }
        return out
    }

    /// New rows in one inbox since `afterRowId`, oldest first, at most `limit`.
    public static func newMessages(index: URL, mailboxURL: String, afterRowId: Int, limit: Int = 50) throws -> [InboxMessage] {
        let db = try open(index)
        defer { sqlite3_close(db) }
        let cols = columns(db, table: "messages")
        let deletedClause = cols.contains("deleted") ? " AND m.deleted = 0" : ""
        let readCol = cols.contains("read") ? "m.read" : "0"
        let sql = "SELECT m.ROWID, m.date_received, \(readCol), mb.url FROM messages m JOIN mailboxes mb ON mb.ROWID = m.mailbox WHERE mb.url = ? AND m.ROWID > ?\(deletedClause) ORDER BY m.ROWID LIMIT ?"
        let s = try statement(db, sql)
        defer { sqlite3_finalize(s) }
        sqlite3_bind_text(s, 1, mailboxURL, -1, transient)
        sqlite3_bind_int64(s, 2, Int64(afterRowId))
        sqlite3_bind_int(s, 3, Int32(limit))
        var out: [InboxMessage] = []
        while sqlite3_step(s) == SQLITE_ROW {
            let rowId = Int(sqlite3_column_int64(s, 0))
            let received = Date(timeIntervalSince1970: TimeInterval(sqlite3_column_int64(s, 1)))
            let read = sqlite3_column_int(s, 2) != 0
            let url = sqlite3_column_text(s, 3).map { String(cString: $0) } ?? mailboxURL
            out.append(InboxMessage(rowId: rowId, mailboxURL: url, dateReceived: received, read: read))
        }
        return out
    }

    /// The highest ROWID in an inbox, so a new account starts from "now" instead of reading years of mail.
    public static func latestRowId(index: URL, mailboxURL: String) throws -> Int {
        let db = try open(index)
        defer { sqlite3_close(db) }
        let s = try statement(db, "SELECT COALESCE(MAX(m.ROWID), 0) FROM messages m JOIN mailboxes mb ON mb.ROWID = m.mailbox WHERE mb.url = ?")
        defer { sqlite3_finalize(s) }
        sqlite3_bind_text(s, 1, mailboxURL, -1, transient)
        return sqlite3_step(s) == SQLITE_ROW ? Int(sqlite3_column_int64(s, 0)) : 0
    }

    /// Finds `<rowId>.emlx` (or `<rowId>.partial.emlx`) under the inbox's `.mbox` folders. Mail splits the Data folder
    /// by the ROWID's digits, so the file is looked up by enumerating `Messages` folders once and caching the map.
    public static func messageFile(rowId: Int, mailFolder: URL, mailboxURL: String, cache: inout [Int: URL]) -> URL? {
        if let u = cache[rowId], FileManager.default.fileExists(atPath: u.path) { return u }
        let fm = FileManager.default
        let wantedBox = (URL(string: mailboxURL)?.lastPathComponent ?? "INBOX") + ".mbox"
        guard let e = fm.enumerator(at: mailFolder, includingPropertiesForKeys: [.isDirectoryKey], options: [.skipsHiddenFiles, .skipsPackageDescendants]) else { return nil }
        let marker = "/\(wantedBox)/"
        for case let url as URL in e {
            let name = url.lastPathComponent
            if name == "Attachments" || name == "MailData" { e.skipDescendants(); continue }
            if name.hasSuffix(".mbox"), name.caseInsensitiveCompare(wantedBox) != .orderedSame { e.skipDescendants(); continue }
            guard name.hasSuffix(".emlx"), url.path.range(of: marker, options: .caseInsensitive) != nil else { continue }
            guard let id = Int(name.prefix { $0.isNumber }) else { continue }
            if cache[id] == nil || !name.hasSuffix(".partial.emlx") { cache[id] = url }
        }
        return cache[rowId]
    }

    /// The raw message of one row, unwrapped from its emlx envelope.
    public static func rawMessage(at url: URL) throws -> Data {
        EMLX.unwrap(try Data(contentsOf: url))
    }

    // MARK: sqlite

    private static let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

    private static func open(_ url: URL) throws -> OpaquePointer {
        var db: OpaquePointer?
        // Read only, and immutable so Mail's own locks and journal never stop a read.
        let uri = "file:\(url.path.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? url.path)?mode=ro&immutable=1"
        guard sqlite3_open_v2(uri, &db, SQLITE_OPEN_READONLY | SQLITE_OPEN_URI | SQLITE_OPEN_FULLMUTEX, nil) == SQLITE_OK, let db else {
            let why = db.map { String(cString: sqlite3_errmsg($0)) } ?? "cannot open"
            if let db { sqlite3_close(db) }
            throw LibraryError.sqlite("Mail's index: \(why)")
        }
        return db
    }
    private static func statement(_ db: OpaquePointer, _ sql: String) throws -> OpaquePointer {
        var s: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &s, nil) == SQLITE_OK, let s else { throw LibraryError.sqlite(String(cString: sqlite3_errmsg(db))) }
        return s
    }
    private static func columns(_ db: OpaquePointer, table: String) -> Set<String> {
        guard let s = try? statement(db, "PRAGMA table_info(\(table))") else { return [] }
        defer { sqlite3_finalize(s) }
        var out = Set<String>()
        while sqlite3_step(s) == SQLITE_ROW { if let c = sqlite3_column_text(s, 1) { out.insert(String(cString: c)) } }
        return out
    }
}
