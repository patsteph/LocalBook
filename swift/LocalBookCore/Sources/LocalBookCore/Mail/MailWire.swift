import Foundation

/// The IMAP wire format the client speaks (RFC 3501, with UID MOVE from RFC 6851): building commands, finding the end
/// of a tagged reply, reading literals and the few response shapes we need. Pure, so the tests can feed it bytes.
public enum IMAPWire {
    /// A command with a tag and CRLF.
    public static func command(tag: String, _ text: String) -> Data { Data("\(tag) \(text)\r\n".utf8) }

    /// A quoted IMAP string: backslash and double quote escaped. Passwords with CRLF or 8-bit bytes go as
    /// non-synchronising literals (RFC 7888), sent in one go.
    public static func quoted(_ s: String) -> String {
        if s.utf8.contains(where: { $0 == 13 || $0 == 10 || $0 >= 128 }) { return "{\(s.utf8.count)+}\r\n\(s)" }
        return "\"" + s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") + "\""
    }

    public enum Status: String, Sendable { case ok = "OK", no = "NO", bad = "BAD" }

    /// Everything the server sent for one command: the untagged lines (with literals attached to the line that
    /// announced them) and the tagged final status.
    public struct Reply: Sendable {
        public var untagged: [Line]
        public var status: Status
        public var text: String
        public init(untagged: [Line], status: Status, text: String) { self.untagged = untagged; self.status = status; self.text = text }
    }
    public struct Line: Sendable {
        public var text: String
        /// Literal bytes that followed "{n}" on this line, in order.
        public var literals: [Data]
        public init(text: String, literals: [Data] = []) { self.text = text; self.literals = literals }
    }

    /// Scans a buffer for a complete reply to `tag`. Returns the reply and how many bytes it used, or nil when more
    /// bytes are needed. Literals ("{123}\r\n" then 123 bytes) are honoured, so a CRLF inside a message body never
    /// ends a line early.
    public static func reply(tag: String, in buffer: Data) -> (Reply, consumed: Int)? {
        var lines: [Line] = []
        var pos = buffer.startIndex
        var current = Line(text: "")
        var pendingText = Data()
        while true {
            guard let nl = buffer[pos...].firstIndex(of: UInt8(ascii: "\n")) else { return nil }
            var lineEnd = nl
            if lineEnd > pos, buffer[lineEnd - 1] == UInt8(ascii: "\r") { lineEnd -= 1 }
            pendingText.append(buffer[pos..<lineEnd])
            pos = buffer.index(after: nl)
            // A literal announcement ends the line with {n}.
            let text = String(decoding: pendingText, as: UTF8.self)
            if let open = text.lastIndex(of: "{"), text.hasSuffix("}"), let n = Int(text[text.index(after: open)..<text.index(before: text.endIndex)]) {
                guard buffer.distance(from: pos, to: buffer.endIndex) >= n else { return nil }
                current.literals.append(Data(buffer[pos..<(pos + n)]))
                pos += n
                // The rest of this logical line continues after the literal.
                pendingText.append(contentsOf: Data(" ".utf8))
                continue
            }
            current.text = text
            pendingText = Data()
            let parts = current.text.split(separator: " ", maxSplits: 2, omittingEmptySubsequences: false)
            if parts.first.map(String.init) == tag, parts.count >= 2, let status = Status(rawValue: String(parts[1])) {
                return (Reply(untagged: lines, status: status, text: parts.count > 2 ? String(parts[2]) : ""), buffer.distance(from: buffer.startIndex, to: pos))
            }
            if current.text.hasPrefix("*") || current.text.hasPrefix("+") { lines.append(current) }
            current = Line(text: "")
        }
    }

    /// "* SEARCH 4 7 12" → [4, 7, 12] (ESEARCH "ALL 4,7:9" too).
    public static func searchResults(_ reply: Reply) -> [Int] {
        var out: [Int] = []
        for l in reply.untagged {
            if l.text.hasPrefix("* SEARCH") {
                out += l.text.dropFirst(8).split(separator: " ").compactMap { Int($0) }
            } else if l.text.hasPrefix("* ESEARCH"), let r = l.text.range(of: "ALL ") {
                for piece in l.text[r.upperBound...].split(separator: ",") {
                    let ends = piece.split(separator: ":").compactMap { Int($0.trimmingCharacters(in: .whitespaces)) }
                    if ends.count == 2, ends[0] <= ends[1], ends[1] - ends[0] < 10_000 { out += Array(ends[0]...ends[1]) } else if let a = ends.first { out.append(a) }
                }
            }
        }
        return out.sorted()
    }

    /// The message bytes from a UID FETCH reply (BODY[] or RFC822 literal), with the UID the server echoed.
    public static func fetchedMessages(_ reply: Reply) -> [(uid: Int, data: Data)] {
        var out: [(Int, Data)] = []
        for l in reply.untagged where l.text.hasPrefix("*") && l.text.contains(" FETCH ") {
            guard let body = l.literals.first else { continue }
            var uid = 0
            if let r = l.text.range(of: "UID ") { uid = Int(l.text[r.upperBound...].prefix { $0.isNumber }) ?? 0 }
            out.append((uid, body))
        }
        return out
    }

    /// "* CAPABILITY IMAP4rev1 MOVE UIDPLUS" or "[CAPABILITY ...]" in the greeting → the words.
    public static func capabilities(_ text: String) -> Set<String> {
        guard let r = text.range(of: "CAPABILITY ") else { return [] }
        var rest = String(text[r.upperBound...])
        if let close = rest.firstIndex(of: "]") { rest = String(rest[..<close]) }
        return Set(rest.split(separator: " ").map { $0.uppercased() })
    }

    /// "* LIST (\HasNoChildren) "/" "LocalBook/Personal"" → the mailbox names.
    public static func listedMailboxes(_ reply: Reply) -> [String] {
        reply.untagged.compactMap { l in
            guard l.text.hasPrefix("* LIST") || l.text.hasPrefix("* LSUB") else { return nil }
            if let lit = l.literals.first { return String(decoding: lit, as: UTF8.self) }
            if let m = l.text.firstMatch(of: /"((?:[^"\\]|\\.)*)"\s*$/) {
                return String(m.1).replacingOccurrences(of: "\\\"", with: "\"").replacingOccurrences(of: "\\\\", with: "\\")
            }
            return l.text.split(separator: " ").last.map(String.init)
        }
    }

    /// The tag sequence: A0001, A0002, …
    public static func tag(_ n: Int) -> String { String(format: "A%04d", n) }
}

/// The SMTP side (RFC 5321, AUTH PLAIN from RFC 4616, the message itself per RFC 5322). Pure.
public enum SMTPWire {
    /// A plain-text message, optionally with an HTML alternative, as bytes ready for DATA (dot-stuffed, CRLF).
    public static func message(from: String, to: String, subject: String, text: String, html: String? = nil, inReplyTo: String? = nil, date: Date = Date(), messageId: String = UUID().uuidString.lowercased()) -> Data {
        let domain = from.split(separator: "@").last.map(String.init) ?? "localbook.local"
        var lines: [String] = [
            "From: \(from)", "To: \(to)", "Subject: \(encodeHeader(subject.isEmpty ? "(no subject)" : subject))", "Date: \(MailDates.format(date))",
            "Message-ID: <\(messageId)@\(domain)>", "MIME-Version: 1.0", "X-Mailer: LocalBook Correspondent",
        ]
        if let inReplyTo, !inReplyTo.isEmpty {
            let ref = inReplyTo.hasPrefix("<") ? inReplyTo : "<\(inReplyTo)>"
            lines.append("In-Reply-To: \(ref)"); lines.append("References: \(ref)")
        }
        var body: String
        if let html, !html.isEmpty {
            let boundary = "=_lb_" + UUID().uuidString.replacingOccurrences(of: "-", with: "").prefix(20)
            lines.append("Content-Type: multipart/alternative; boundary=\"\(boundary)\"")
            body = "--\(boundary)\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Transfer-Encoding: 8bit\r\n\r\n\(crlf(text))\r\n"
            body += "--\(boundary)\r\nContent-Type: text/html; charset=utf-8\r\nContent-Transfer-Encoding: 8bit\r\n\r\n\(crlf(html))\r\n--\(boundary)--\r\n"
        } else {
            lines.append("Content-Type: text/plain; charset=utf-8"); lines.append("Content-Transfer-Encoding: 8bit")
            body = crlf(text) + "\r\n"
        }
        return Data((lines.joined(separator: "\r\n") + "\r\n\r\n" + body).utf8)
    }

    /// Lines starting with "." get a second dot; the terminator is "\r\n.\r\n".
    public static func dotStuffed(_ message: Data) -> Data {
        var text = String(decoding: message, as: UTF8.self)
        if !text.hasSuffix("\r\n") { text += "\r\n" }
        text = text.replacingOccurrences(of: "\r\n.", with: "\r\n..")
        if text.hasPrefix(".") { text = "." + text }
        return Data((text + ".\r\n").utf8)
    }

    /// AUTH PLAIN's one line: base64 of "\0user\0password".
    public static func authPlain(user: String, password: String) -> String {
        Data(("\u{0}" + user + "\u{0}" + password).utf8).base64EncodedString()
    }

    /// RFC 2047 for a subject with non-ASCII characters.
    public static func encodeHeader(_ s: String) -> String {
        s.utf8.allSatisfy { $0 < 128 } ? s : "=?utf-8?B?" + Data(s.utf8).base64EncodedString() + "?="
    }

    /// The 3-digit code of a reply and whether more lines follow ("250-" vs "250 ").
    public static func replyCode(_ line: String) -> (code: Int, more: Bool)? {
        guard line.count >= 3, let code = Int(line.prefix(3)) else { return nil }
        return (code, line.count > 3 && line[line.index(line.startIndex, offsetBy: 3)] == "-")
    }

    static func crlf(_ s: String) -> String { s.replacingOccurrences(of: "\r\n", with: "\n").replacingOccurrences(of: "\n", with: "\r\n") }
}
