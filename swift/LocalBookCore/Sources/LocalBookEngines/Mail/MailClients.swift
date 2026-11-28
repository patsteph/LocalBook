import Foundation
import LocalBookCore
import Network
import Security

/// A line-oriented TLS (or plain) connection on Network.framework: the transport under the IMAP and SMTP clients.
/// Plain is for the local test server only; real accounts use TLS (implicit on 993/465, STARTTLS on 587).
actor MailConnection {
    enum Problem: LocalizedError {
        case closed, timeout, refused(String), protocolError(String)
        var errorDescription: String? {
            switch self {
            case .closed: "The mail server closed the connection"
            case .timeout: "The mail server did not answer in time"
            case .refused(let s): s
            case .protocolError(let s): s
            }
        }
    }

    private var connection: NWConnection
    private var buffer = Data()
    private let host: String
    private let port: Int
    private let timeout: TimeInterval

    init(host: String, port: Int, tls: Bool, timeout: TimeInterval = 30) {
        self.host = host; self.port = port; self.timeout = timeout
        let params: NWParameters = tls ? .tls : .tcp
        connection = NWConnection(host: NWEndpoint.Host(host), port: NWEndpoint.Port(rawValue: UInt16(clamping: port)) ?? 993, using: params)
    }

    func open() async throws {
        let c = connection
        try await withCheckedThrowingContinuation { (k: CheckedContinuation<Void, any Error>) in
            let once = Once()
            c.stateUpdateHandler = { state in
                switch state {
                case .ready: once.run { k.resume() }
                case .failed(let e): once.run { k.resume(throwing: Problem.refused("Could not reach \(self.host):\(self.port): \(e.localizedDescription)")) }
                case .waiting(let e): once.run { c.cancel(); k.resume(throwing: Problem.refused("Could not reach \(self.host):\(self.port): \(e.localizedDescription)")) }
                case .cancelled: once.run { k.resume(throwing: Problem.closed) }
                default: break
                }
            }
            c.start(queue: DispatchQueue(label: "LocalBook.mail"))
        }
        connection.stateUpdateHandler = nil
    }

    func send(_ data: Data) async throws {
        let c = connection
        try await withCheckedThrowingContinuation { (k: CheckedContinuation<Void, any Error>) in
            c.send(content: data, completion: .contentProcessed { e in if let e { k.resume(throwing: Problem.refused(e.localizedDescription)) } else { k.resume() } })
        }
    }

    /// Reads until `done` finds a complete unit in the buffer; returns it and drops those bytes.
    func read<T>(_ done: @Sendable (Data) -> (T, Int)?) async throws -> T {
        let deadline = Date().addingTimeInterval(timeout)
        while true {
            if let (value, used) = done(buffer) { buffer.removeFirst(used); return value }
            guard Date() < deadline else { throw Problem.timeout }
            let chunk = try await receive()
            guard !chunk.isEmpty else { throw Problem.closed }
            buffer.append(chunk)
        }
    }

    /// One CRLF-terminated line, without the CRLF.
    func readLine() async throws -> String {
        try await read { buf in
            guard let nl = buf.firstIndex(of: UInt8(ascii: "\n")) else { return nil }
            var end = nl
            if end > buf.startIndex, buf[end - 1] == UInt8(ascii: "\r") { end -= 1 }
            return (String(decoding: buf[buf.startIndex..<end], as: UTF8.self), buf.distance(from: buf.startIndex, to: nl) + 1)
        }
    }

    private func receive() async throws -> Data {
        let c = connection
        return try await withCheckedThrowingContinuation { (k: CheckedContinuation<Data, any Error>) in
            c.receive(minimumIncompleteLength: 1, maximumLength: 1 << 16) { data, _, complete, error in
                if let error { k.resume(throwing: Problem.refused(error.localizedDescription)); return }
                k.resume(returning: (data?.isEmpty == false) ? data! : Data())
            }
        }
    }

    func close() { connection.cancel() }

    private final class Once: @unchecked Sendable {
        private let lock = NSLock(); private var done = false
        func run(_ f: () -> Void) { lock.lock(); let d = done; done = true; lock.unlock(); if !d { f() } }
    }
}

/// The IMAP the Correspondent needs: log in, pick INBOX, find and fetch new messages by UID, mark, move, delete.
/// RFC 3501 with UID MOVE (RFC 6851) when the server has it, COPY + flag + EXPUNGE otherwise.
actor IMAPClient {
    private let conn: MailConnection
    private var n = 0
    private(set) var capabilities: Set<String> = []
    let host: String

    init(host: String, port: Int, tls: Bool) {
        self.host = host
        conn = MailConnection(host: host, port: port, tls: tls)
    }

    func connect(user: String, password: String) async throws {
        try await conn.open()
        let greeting = try await conn.readLine()
        guard greeting.hasPrefix("* OK") || greeting.hasPrefix("* PREAUTH") else { throw MailConnection.Problem.protocolError("Unexpected greeting: \(greeting)") }
        capabilities = IMAPWire.capabilities(greeting)
        if !greeting.hasPrefix("* PREAUTH") {
            let r = try await run("LOGIN \(IMAPWire.quoted(user)) \(IMAPWire.quoted(password))")
            guard r.status == .ok else { throw MailConnection.Problem.refused("The mail server refused the sign-in: \(r.text)") }
            capabilities.formUnion(IMAPWire.capabilities(r.text))
        }
        if capabilities.isEmpty || !capabilities.contains("IMAP4REV1") {
            let r = try await run("CAPABILITY")
            for l in r.untagged { capabilities.formUnion(IMAPWire.capabilities(l.text)) }
        }
    }

    func select(_ mailbox: String = "INBOX") async throws {
        let r = try await run("SELECT \(IMAPWire.quoted(mailbox))")
        guard r.status == .ok else { throw MailConnection.Problem.refused("Cannot open \(mailbox): \(r.text)") }
    }

    /// Unseen UIDs above `lastUID`, ascending.
    func unseenUIDs(after lastUID: Int) async throws -> [Int] {
        let r = try await run("UID SEARCH UNSEEN UID \(lastUID + 1):*")
        guard r.status == .ok else { throw MailConnection.Problem.refused("Search failed: \(r.text)") }
        return IMAPWire.searchResults(r).filter { $0 > lastUID }
    }

    /// The whole message, without marking it read.
    func fetch(uid: Int) async throws -> Data? {
        let r = try await run("UID FETCH \(uid) (UID BODY.PEEK[])")
        guard r.status == .ok else { throw MailConnection.Problem.refused("Fetch failed: \(r.text)") }
        return IMAPWire.fetchedMessages(r).first?.data
    }

    func markSeen(uid: Int) async throws { _ = try await run("UID STORE \(uid) +FLAGS (\\Seen)") }

    func ensureMailbox(_ name: String) async throws {
        let r = try await run("LIST \"\" \(IMAPWire.quoted(name))")
        if IMAPWire.listedMailboxes(r).contains(name) { return }
        _ = try await run("CREATE \(IMAPWire.quoted(name))")
    }

    func move(uid: Int, to mailbox: String) async throws {
        if capabilities.contains("MOVE") {
            let r = try await run("UID MOVE \(uid) \(IMAPWire.quoted(mailbox))")
            if r.status == .ok { return }
        }
        let c = try await run("UID COPY \(uid) \(IMAPWire.quoted(mailbox))")
        guard c.status == .ok else { throw MailConnection.Problem.refused("Move failed: \(c.text)") }
        _ = try await run("UID STORE \(uid) +FLAGS (\\Deleted)")
        _ = try await run("EXPUNGE")
    }

    /// Gmail and iCloud keep "deleted" messages unless they go to the trash folder, as the old app learned.
    func delete(uid: Int) async throws {
        let h = host.lowercased()
        let trash: String? = (h.contains("gmail") || h.contains("googlemail")) ? "[Gmail]/Trash" : ((h.contains("icloud") || h.contains("me.com") || h.contains("mac.com")) ? "Deleted Messages" : nil)
        if let trash, (try? await move(uid: uid, to: trash)) != nil { return }
        _ = try await run("UID STORE \(uid) +FLAGS (\\Deleted)")
        _ = try await run("EXPUNGE")
    }

    func logout() async {
        _ = try? await run("LOGOUT")
        await conn.close()
    }

    private func run(_ command: String) async throws -> IMAPWire.Reply {
        n += 1
        let tag = IMAPWire.tag(n)
        try await conn.send(IMAPWire.command(tag: tag, command))
        return try await conn.read { buf in IMAPWire.reply(tag: tag, in: buf).map { ($0.0, $0.consumed) } }
    }
}

/// Sending one message over SMTP with AUTH PLAIN (or LOGIN), implicit TLS on 465; plain only for the loopback test
/// server. Three tries with a second's pause, as the old `correspondent_smtp`.
enum SMTPClient {
    static func send(host: String, port: Int, tls: Bool, user: String, password: String, from: String, to: String, message: Data) async throws {
        var last: (any Error)?
        for attempt in 0..<3 {
            do { try await sendOnce(host: host, port: port, tls: tls, user: user, password: password, from: from, to: to, message: message); return }
            catch { last = error; if attempt < 2 { try? await Task.sleep(for: .seconds(1)) } }
        }
        throw last ?? MailConnection.Problem.closed
    }

    private static func sendOnce(host: String, port: Int, tls: Bool, user: String, password: String, from: String, to: String, message: Data) async throws {
        let isLoopback = ["127.0.0.1", "localhost", "::1"].contains(host.lowercased())
        guard tls || isLoopback else { throw MailConnection.Problem.protocolError("Sending needs TLS (port 465); plain SMTP is only for a server on this Mac") }
        let conn = MailConnection(host: host, port: port, tls: tls)
        try await conn.open()
        defer { Task { await conn.close() } }
        try await expect(conn, 220)
        try await conn.send(Data("EHLO localbook.local\r\n".utf8))
        let ehlo = try await expect(conn, 250)
        if !user.isEmpty {
            if ehlo.contains(where: { $0.uppercased().contains("PLAIN") }) || !ehlo.contains(where: { $0.uppercased().contains("AUTH") }) {
                try await conn.send(Data("AUTH PLAIN \(SMTPWire.authPlain(user: user, password: password))\r\n".utf8))
                try await expect(conn, 235)
            } else {
                try await conn.send(Data("AUTH LOGIN\r\n".utf8)); try await expect(conn, 334)
                try await conn.send(Data(Data(user.utf8).base64EncodedString().utf8) + Data("\r\n".utf8)); try await expect(conn, 334)
                try await conn.send(Data(Data(password.utf8).base64EncodedString().utf8) + Data("\r\n".utf8)); try await expect(conn, 235)
            }
        }
        try await conn.send(Data("MAIL FROM:<\(from)>\r\n".utf8)); try await expect(conn, 250)
        try await conn.send(Data("RCPT TO:<\(to)>\r\n".utf8)); try await expect(conn, 250, 251)
        try await conn.send(Data("DATA\r\n".utf8)); try await expect(conn, 354)
        try await conn.send(SMTPWire.dotStuffed(message)); try await expect(conn, 250)
        try await conn.send(Data("QUIT\r\n".utf8))
        _ = try? await conn.readLine()
    }

    /// Reads one (possibly multi-line) reply and checks its code.
    @discardableResult private static func expect(_ conn: MailConnection, _ codes: Int...) async throws -> [String] {
        var lines: [String] = []
        while true {
            let line = try await conn.readLine()
            lines.append(line)
            guard let (code, more) = SMTPWire.replyCode(line) else { throw MailConnection.Problem.protocolError("Odd SMTP reply: \(line)") }
            if more { continue }
            guard codes.contains(code) else { throw MailConnection.Problem.refused("The mail server said: \(line)") }
            return lines
        }
    }
}

/// IMAP passwords live in the Keychain, one generic-password item per account id; the state file never holds them.
enum MailSecrets {
    static let service = "com.patsteph.localbook.mail"

    private static func base(_ accountId: String) -> [String: Any] {
        var q: [String: Any] = [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: accountId]
        #if os(macOS)
        q[kSecUseDataProtectionKeychain as String] = true
        #endif
        return q
    }

    static func password(for accountId: String) -> String? {
        var q = base(accountId)
        q[kSecReturnData as String] = true
        var out: CFTypeRef?
        guard SecItemCopyMatching(q as CFDictionary, &out) == errSecSuccess, let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    static func set(_ password: String, for accountId: String) {
        let data = Data(password.utf8)
        if SecItemUpdate(base(accountId) as CFDictionary, [kSecValueData as String: data] as CFDictionary) == errSecSuccess { return }
        var add = base(accountId)
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        add[kSecAttrLabel as String] = "LocalBook mail password"
        SecItemAdd(add as CFDictionary, nil)
    }

    static func remove(for accountId: String) { SecItemDelete(base(accountId) as CFDictionary) }
}

#if os(macOS)
/// Mail.app does the moving, marking, deleting and sending for the Mail path, through Apple events (the app has the
/// automation entitlement; macOS asks the user once). Messages are found by their Message-ID.
enum MailAppBridge {
    enum Problem: LocalizedError {
        case script(String)
        var errorDescription: String? { if case .script(let s) = self { return "Mail: \(s)" } else { return nil } }
    }

    static func run(_ source: String) throws {
        var error: NSDictionary?
        guard let script = NSAppleScript(source: source) else { throw Problem.script("bad script") }
        script.executeAndReturnError(&error)
        if let error { throw Problem.script((error[NSAppleScript.errorMessage] as? String) ?? "Apple event failed (\(error[NSAppleScript.errorNumber] ?? ""))") }
    }

    static func esc(_ s: String) -> String { s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") }

    private static func find(_ messageId: String) -> String {
        "set theMessages to (every message of inbox whose message id is \"\(esc(messageId))\")\n"
    }

    static func markRead(messageId: String) throws {
        try run("tell application \"Mail\"\n\(find(messageId))repeat with m in theMessages\nset read status of m to true\nend repeat\nend tell")
    }

    static func move(messageId: String, toMailbox name: String, account: String?) throws {
        let path = name.split(separator: "/").map { "\"\(esc(String($0)))\"" }
        // Nested mailbox: mailbox "Personal" of mailbox "LocalBook" [of account "X"].
        var target = "mailbox \(path.last ?? "\"LocalBook\"")"
        for p in path.dropLast().reversed() { target += " of mailbox \(p)" }
        if let account, !account.isEmpty { target += " of account \"\(esc(account))\"" }
        try run("tell application \"Mail\"\ntry\nmake new mailbox with properties {name:\"\(esc(name))\"}\nend try\n\(find(messageId))repeat with m in theMessages\nmove m to \(target)\nend repeat\nend tell")
    }

    static func delete(messageId: String) throws {
        try run("tell application \"Mail\"\n\(find(messageId))repeat with m in theMessages\ndelete m\nend repeat\nend tell")
    }

    static func send(from: String, to: String, subject: String, body: String) throws {
        try run("""
        tell application "Mail"
        set msg to make new outgoing message with properties {subject:"\(esc(subject))", content:"\(esc(body))", visible:false}
        tell msg
        make new to recipient at end of to recipients with properties {address:"\(esc(to))"}
        \(from.isEmpty ? "" : "set sender to \"\(esc(from))\"")
        end tell
        send msg
        end tell
        """)
    }
}
#endif
