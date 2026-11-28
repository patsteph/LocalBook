import Foundation

/// One parsed email: the headers the Correspondent reads and the two bodies. Pure Foundation; the old app used
/// mail-parser, and this reader covers what that gave it: RFC 5322 headers (unfolded, RFC 2047 words decoded),
/// MIME multiparts of any depth, quoted-printable and base64 transfer encodings, the common charsets, attachment
/// names and sizes, and the List-Unsubscribe headers.
public struct MailMessage: Sendable, Hashable {
    public struct Address: Sendable, Hashable {
        public var name: String
        public var address: String
        public init(name: String = "", address: String) { self.name = name; self.address = address }
        /// "Rosa Alvarez <rosa@example.com>" or just the address.
        public var display: String { name.isEmpty ? address : "\(name) <\(address)>" }
    }
    public struct Attachment: Sendable, Hashable {
        public var filename: String
        public var size: Int
        public var contentType: String
        public init(filename: String, size: Int, contentType: String) { self.filename = filename; self.size = size; self.contentType = contentType }
    }

    public var messageId: String
    public var subject: String
    public var from: Address?
    public var to: [Address]
    public var date: Date?
    public var textBody: String
    public var htmlBody: String
    public var attachments: [Attachment]
    public var listUnsubscribe: String
    public var listUnsubscribePost: String
    /// Every header, lower-cased name, first value wins.
    public var headers: [String: String]

    public init(messageId: String, subject: String, from: Address?, to: [Address], date: Date?, textBody: String, htmlBody: String,
                attachments: [Attachment] = [], listUnsubscribe: String = "", listUnsubscribePost: String = "", headers: [String: String] = [:]) {
        self.messageId = messageId; self.subject = subject; self.from = from; self.to = to; self.date = date
        self.textBody = textBody; self.htmlBody = htmlBody; self.attachments = attachments
        self.listUnsubscribe = listUnsubscribe; self.listUnsubscribePost = listUnsubscribePost; self.headers = headers
    }

    /// The sender as the old app kept it: the bare address, lower case.
    public var sender: String { (from?.address ?? "").lowercased() }
    /// The text the agent reads: the plain part, or the HTML turned into text.
    public var readableBody: String { textBody.isEmpty ? MailText.htmlToText(htmlBody) : textBody }
}

public enum MailParser {
    /// Parses an RFC 5322 message. Never throws: a message with no parsable headers comes back with a made-up id and
    /// its bytes as the text body, as the old app's stdlib fallback did.
    public static func parse(_ data: Data) -> MailMessage {
        let (headerBlock, body) = splitHeaders(data)
        let headers = parseHeaders(headerBlock)
        let contentType = ContentType(headers["content-type"] ?? "text/plain")
        var text = "", html = ""
        var attachments: [MailMessage.Attachment] = []
        walk(body: body, headers: headers, contentType: contentType, text: &text, html: &html, attachments: &attachments, depth: 0)
        let id = headers["message-id"].map { $0.trimmingCharacters(in: CharacterSet(charactersIn: "<> ")) } ?? ""
        let subject = decodeWords(headers["subject"] ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        return MailMessage(
            messageId: id.isEmpty ? UUID().uuidString.lowercased() : id,
            subject: subject.isEmpty ? "(no subject)" : subject,
            from: addresses(headers["from"] ?? "").first,
            to: addresses(headers["to"] ?? ""),
            date: headers["date"].flatMap(MailDates.parse),
            textBody: text.trimmingCharacters(in: .whitespacesAndNewlines),
            htmlBody: html.trimmingCharacters(in: .whitespacesAndNewlines),
            attachments: attachments,
            listUnsubscribe: headers["list-unsubscribe"] ?? "",
            listUnsubscribePost: headers["list-unsubscribe-post"] ?? "",
            headers: headers)
    }

    // MARK: headers

    /// The header block (up to the first blank line) and the body after it.
    static func splitHeaders(_ data: Data) -> (String, Data) {
        let crlf = Data("\r\n\r\n".utf8), lf = Data("\n\n".utf8)
        var split: Range<Data.Index>?
        if let r = data.range(of: crlf) { split = r }
        if let r = data.range(of: lf), split == nil || r.lowerBound < split!.lowerBound { split = r }
        guard let split else { return (String(decoding: data, as: UTF8.self), Data()) }
        return (String(decoding: data[data.startIndex..<split.lowerBound], as: UTF8.self), Data(data[split.upperBound...]))
    }

    /// Unfolds continuation lines and lowercases names. The first value of a repeated header wins, except
    /// "received", which nobody here reads.
    public static func parseHeaders(_ block: String) -> [String: String] {
        var out: [String: String] = [:]
        var name = "", value = ""
        func flush() {
            guard !name.isEmpty else { return }
            if out[name] == nil { out[name] = value.trimmingCharacters(in: .whitespaces) }
            name = ""; value = ""
        }
        for raw in block.split(omittingEmptySubsequences: false, whereSeparator: { $0 == "\n" }) {
            let line = raw.hasSuffix("\r") ? String(raw.dropLast()) : String(raw)
            if let first = line.first, first == " " || first == "\t" {
                value += " " + line.trimmingCharacters(in: .whitespaces)
            } else if let colon = line.firstIndex(of: ":") {
                flush()
                name = line[..<colon].trimmingCharacters(in: .whitespaces).lowercased()
                value = String(line[line.index(after: colon)...])
            }
        }
        flush()
        return out
    }

    /// RFC 2047 encoded words: =?utf-8?B?...?= and =?iso-8859-1?Q?...?=, adjacent words joined without the space.
    public static func decodeWords(_ s: String) -> String {
        guard s.contains("=?") else { return s }
        let pattern = /=\?([^?]+)\?([bBqQ])\?([^?]*)\?=/
        var out = ""
        var rest = Substring(s)
        var lastWasWord = false
        while let m = rest.firstMatch(of: pattern) {
            let before = rest[rest.startIndex..<m.range.lowerBound]
            if !(lastWasWord && before.allSatisfy(\.isWhitespace)) { out += before }
            let charset = String(m.1).lowercased()
            let text = String(m.3)
            let bytes: Data
            if m.2.lowercased() == "b" { bytes = Data(base64Encoded: text.padding(toLength: ((text.count + 3) / 4) * 4, withPad: "=", startingAt: 0)) ?? Data() }
            else { bytes = Transfer.quotedPrintable(text.replacingOccurrences(of: "_", with: " ")) }
            out += Charsets.decode(bytes, charset: charset)
            rest = rest[m.range.upperBound...]
            lastWasWord = true
        }
        out += rest
        return out
    }

    /// "Rosa Alvarez <rosa@example.com>, bob@example.org" → two addresses. Quoted names keep their commas.
    public static func addresses(_ header: String) -> [MailMessage.Address] {
        var items: [String] = []
        var cur = "", inQuote = false, depth = 0
        for c in decodeWords(header) {
            switch c {
            case "\"": inQuote.toggle(); cur.append(c)
            case "<" where !inQuote: depth += 1; cur.append(c)
            case ">" where !inQuote: depth = max(0, depth - 1); cur.append(c)
            case "," where !inQuote && depth == 0: items.append(cur); cur = ""
            default: cur.append(c)
            }
        }
        items.append(cur)
        return items.compactMap { item in
            let t = item.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !t.isEmpty else { return nil }
            if let lt = t.lastIndex(of: "<"), let gt = t[lt...].firstIndex(of: ">") {
                let addr = t[t.index(after: lt)..<gt].trimmingCharacters(in: .whitespaces)
                let name = t[..<lt].trimmingCharacters(in: CharacterSet(charactersIn: " \"'"))
                return MailMessage.Address(name: name, address: addr.lowercased())
            }
            let bare = t.trimmingCharacters(in: CharacterSet(charactersIn: " \"'<>"))
            return bare.contains("@") ? MailMessage.Address(address: bare.lowercased()) : MailMessage.Address(name: bare, address: "")
        }
    }

    // MARK: MIME

    struct ContentType {
        var mime: String
        var params: [String: String]
        init(_ raw: String) {
            let parts = raw.split(separator: ";", omittingEmptySubsequences: true).map { $0.trimmingCharacters(in: .whitespaces) }
            mime = (parts.first ?? "text/plain").lowercased()
            var p: [String: String] = [:]
            for part in parts.dropFirst() {
                guard let eq = part.firstIndex(of: "=") else { continue }
                let k = part[..<eq].trimmingCharacters(in: .whitespaces).lowercased()
                var v = part[part.index(after: eq)...].trimmingCharacters(in: .whitespaces)
                if v.hasPrefix("\""), v.hasSuffix("\""), v.count >= 2 { v = String(v.dropFirst().dropLast()) }
                p[k] = v
            }
            // RFC 2231 split names: filename*0, filename*1 → filename.
            for key in p.keys where key.hasSuffix("*0") {
                let base = String(key.dropLast(2))
                var joined = "", i = 0
                while let piece = p["\(base)*\(i)"] { joined += piece; i += 1 }
                p[base] = joined
            }
            params = p
        }
        var charset: String { params["charset"]?.lowercased() ?? "utf-8" }
        var boundary: String? { params["boundary"] }
        var name: String? { params["name"] }
    }

    private static func walk(body: Data, headers: [String: String], contentType: ContentType, text: inout String, html: inout String,
                             attachments: inout [MailMessage.Attachment], depth: Int) {
        guard depth < 12 else { return }
        if contentType.mime.hasPrefix("multipart/"), let boundary = contentType.boundary {
            for part in parts(of: body, boundary: boundary) {
                let (h, b) = splitHeaders(part)
                let ph = parseHeaders(h)
                walk(body: b, headers: ph, contentType: ContentType(ph["content-type"] ?? "text/plain"), text: &text, html: &html, attachments: &attachments, depth: depth + 1)
            }
            return
        }
        if contentType.mime == "message/rfc822" {
            let inner = parse(body)
            if text.isEmpty { text = inner.textBody }
            if html.isEmpty { html = inner.htmlBody }
            attachments += inner.attachments
            return
        }
        let disposition = (headers["content-disposition"] ?? "").lowercased()
        let dispositionType = ContentType(headers["content-disposition"] ?? "")
        let filename = dispositionType.params["filename"] ?? contentType.name
        let decoded = Transfer.decode(body, encoding: headers["content-transfer-encoding"] ?? "7bit")
        let isAttachment = disposition.hasPrefix("attachment") || (filename != nil && !contentType.mime.hasPrefix("text/"))
        if isAttachment || !(contentType.mime.hasPrefix("text/")) {
            attachments.append(.init(filename: decodeWords(filename ?? "attachment"), size: decoded.count, contentType: contentType.mime))
            return
        }
        let string = Charsets.decode(decoded, charset: contentType.charset)
        if contentType.mime == "text/html" { if html.isEmpty { html = string } }
        else if text.isEmpty { text = string }
    }

    /// The parts between "--boundary" lines, without the preamble and epilogue.
    static func parts(of body: Data, boundary: String) -> [Data] {
        let marker = Data(("--" + boundary).utf8)
        var out: [Data] = []
        var search = body.startIndex
        var partStart: Data.Index?
        while let r = body.range(of: marker, in: search..<body.endIndex) {
            // The marker must start a line.
            let atLineStart = r.lowerBound == body.startIndex || body[body.index(before: r.lowerBound)] == UInt8(ascii: "\n")
            var after = r.upperBound
            let closing = after + 1 < body.endIndex && body[after] == UInt8(ascii: "-") && body[after + 1] == UInt8(ascii: "-")
            if closing { after += 2 }
            // Skip to the end of the marker line.
            while after < body.endIndex, body[after] != UInt8(ascii: "\n") { after += 1 }
            if after < body.endIndex { after += 1 }
            if atLineStart {
                if let s = partStart {
                    var end = r.lowerBound
                    if end > s, body[end - 1] == UInt8(ascii: "\n") { end -= 1 }
                    if end > s, body[end - 1] == UInt8(ascii: "\r") { end -= 1 }
                    out.append(Data(body[s..<end]))
                }
                partStart = closing ? nil : after
                if closing { break }
            }
            search = after
        }
        return out
    }

    enum Transfer {
        static func decode(_ data: Data, encoding: String) -> Data {
            switch encoding.trimmingCharacters(in: .whitespaces).lowercased() {
            case "base64":
                let cleaned = String(decoding: data, as: UTF8.self).filter { !$0.isWhitespace }
                return Data(base64Encoded: cleaned.padding(toLength: ((cleaned.count + 3) / 4) * 4, withPad: "=", startingAt: 0)) ?? data
            case "quoted-printable": return quotedPrintable(String(decoding: data, as: UTF8.self))
            default: return data
            }
        }

        /// Soft line breaks ("=\r\n") vanish; "=41" becomes the byte 0x41.
        static func quotedPrintable(_ s: String) -> Data {
            var out = Data()
            let bytes = Array(s.utf8)
            var i = 0
            while i < bytes.count {
                let b = bytes[i]
                if b == UInt8(ascii: "=") {
                    if i + 1 < bytes.count, bytes[i + 1] == UInt8(ascii: "\r") { i += (i + 2 < bytes.count && bytes[i + 2] == UInt8(ascii: "\n")) ? 3 : 2; continue }
                    if i + 1 < bytes.count, bytes[i + 1] == UInt8(ascii: "\n") { i += 2; continue }
                    if i + 2 < bytes.count, let v = UInt8(String(decoding: bytes[(i + 1)...(i + 2)], as: UTF8.self), radix: 16) { out.append(v); i += 3; continue }
                }
                out.append(b); i += 1
            }
            return out
        }
    }

    enum Charsets {
        static func decode(_ data: Data, charset: String) -> String {
            let enc: String.Encoding
            switch charset.lowercased().replacingOccurrences(of: "_", with: "-") {
            case "utf-8", "utf8", "us-ascii", "ascii": enc = .utf8
            case "iso-8859-1", "latin1", "latin-1": enc = .isoLatin1
            case "iso-8859-2": enc = .isoLatin2
            case "windows-1252", "cp1252": enc = .windowsCP1252
            case "windows-1251", "cp1251": enc = .windowsCP1251
            case "windows-1250": enc = .windowsCP1250
            case "utf-16", "utf16": enc = .utf16
            case "shift-jis", "shift_jis", "sjis": enc = .shiftJIS
            case "euc-jp": enc = .japaneseEUC
            case "iso-2022-jp": enc = .iso2022JP
            case "macintosh", "mac-roman": enc = .macOSRoman
            default: enc = .utf8
            }
            return String(data: data, encoding: enc) ?? String(data: data, encoding: .isoLatin1) ?? String(decoding: data, as: UTF8.self)
        }
    }
}

public enum MailDates {
    nonisolated(unsafe) private static let formatters: [DateFormatter] = {
        ["EEE, d MMM yyyy HH:mm:ss Z", "d MMM yyyy HH:mm:ss Z", "EEE, d MMM yyyy HH:mm Z", "d MMM yyyy HH:mm Z", "EEE, d MMM yy HH:mm:ss Z", "d MMM yy HH:mm:ss Z"].map {
            let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = $0; return f
        }
    }()

    /// RFC 5322 dates, with the comment "(UTC)" some servers append and the "GMT"/"UT" zone words.
    public static func parse(_ raw: String) -> Date? {
        var s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if let paren = s.firstIndex(of: "(") { s = String(s[..<paren]).trimmingCharacters(in: .whitespaces) }
        for (word, zone) in [(" GMT", " +0000"), (" UT", " +0000"), (" UTC", " +0000"), (" EST", " -0500"), (" EDT", " -0400"), (" CST", " -0600"), (" CDT", " -0500"), (" MST", " -0700"), (" MDT", " -0600"), (" PST", " -0800"), (" PDT", " -0700")] where s.hasSuffix(word) {
            s = String(s.dropLast(word.count)) + zone
        }
        for f in formatters { if let d = f.date(from: s) { return d } }
        let iso = ISO8601DateFormatter()
        return iso.date(from: s)
    }

    /// An RFC 5322 date for outgoing mail.
    public static func format(_ date: Date) -> String {
        let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = "EEE, d MMM yyyy HH:mm:ss Z"
        return f.string(from: date)
    }
}

/// Text cleaning for mail bodies: the old `html_to_clean_text` and `sanitize_for_llm`.
public enum MailText {
    /// Newsletter HTML to text: scripts, styles, images and trackers dropped, anchors kept as "[text](href)",
    /// block tags as line breaks, entities decoded, blank runs collapsed.
    public static func htmlToText(_ html: String) -> String {
        guard !html.isEmpty else { return "" }
        var s = html
        for tag in ["script", "style", "head", "iframe", "object", "noscript"] {
            s = s.replacingOccurrences(of: "(?is)<\(tag)\\b[^>]*>.*?</\(tag)>", with: " ", options: .regularExpression)
        }
        s = s.replacingOccurrences(of: "(?is)<!--.*?-->", with: " ", options: .regularExpression)
        s = s.replacingOccurrences(of: "(?is)<(img|meta|link|base|embed|input)\\b[^>]*>", with: " ", options: .regularExpression)
        // Anchors: [text](href) when both exist.
        s = s.replacingOccurrences(of: "(?is)<a\\b[^>]*?href\\s*=\\s*[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", with: "[$2]($1)", options: .regularExpression)
        s = s.replacingOccurrences(of: "(?i)<br\\s*/?>", with: "\n", options: .regularExpression)
        s = s.replacingOccurrences(of: "(?i)</(p|div|tr|li|h[1-6]|blockquote|section|article|table|ul|ol|pre)>", with: "\n\n", options: .regularExpression)
        s = s.replacingOccurrences(of: "(?i)</(td|th)>", with: " ", options: .regularExpression)
        s = s.replacingOccurrences(of: "<[^>]+>", with: "", options: .regularExpression)
        s = decodeEntities(s)
        // Tags inside a markdown link's label collapse to spaces; tidy them.
        s = s.replacingOccurrences(of: "\\[\\s+", with: "[", options: .regularExpression).replacingOccurrences(of: "\\s+\\]", with: "]", options: .regularExpression)
        s = s.replacingOccurrences(of: "[ \\t\\u00A0]+", with: " ", options: .regularExpression)
        s = s.replacingOccurrences(of: " *\\n *", with: "\n", options: .regularExpression)
        s = s.replacingOccurrences(of: "\\n{3,}", with: "\n\n", options: .regularExpression)
        return s.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    public static func decodeEntities(_ s: String) -> String {
        var t = s
        for (e, c) in [("&nbsp;", " "), ("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", "\""), ("&#39;", "'"), ("&apos;", "'"), ("&rsquo;", "’"), ("&lsquo;", "‘"), ("&rdquo;", "”"), ("&ldquo;", "“"), ("&mdash;", "—"), ("&ndash;", "–"), ("&hellip;", "…"), ("&copy;", "©"), ("&zwnj;", ""), ("&zwj;", "")] {
            t = t.replacingOccurrences(of: e, with: c)
        }
        if t.contains("&#") {
            t = t.replacingOccurrences(of: "&#x([0-9a-fA-F]+);", with: "\u{0}x$1\u{0}", options: .regularExpression)
            t = t.replacingOccurrences(of: "&#([0-9]+);", with: "\u{0}d$1\u{0}", options: .regularExpression)
            var out = ""
            var pieces = t.split(separator: "\u{0}", omittingEmptySubsequences: false)
            if !pieces.isEmpty { out += pieces.removeFirst() }
            var isCode = true
            for piece in pieces {
                if isCode, let first = piece.first {
                    let num = piece.dropFirst()
                    let v = first == "x" ? UInt32(num, radix: 16) : UInt32(num)
                    if let v, let scalar = Unicode.Scalar(v) { out.unicodeScalars.append(scalar) }
                } else { out += piece }
                isCode.toggle()
            }
            t = out
        }
        return t
    }

    /// Prompt-injection markers and long base64 blobs out before a body reaches the model; the old patterns.
    public static func sanitizeForModel(_ text: String) -> String {
        guard !text.isEmpty else { return "" }
        var out = text.replacingOccurrences(of: "[A-Za-z0-9+/]{200,}={0,2}", with: "[base64 blob removed]", options: .regularExpression)
        for p in ["(?im)^\\s*(system|assistant|user)\\s*:\\s*", "(?i)ignore (the )?previous instructions", "(?i)disregard (the )?(previous|above) (instructions|prompt)",
                  "(?i)you are now [a-z ]{3,40} mode", "(?i)reveal (your |the )?system (prompt|message)"] {
            out = out.replacingOccurrences(of: p, with: "[content removed by sanitizer]", options: .regularExpression)
        }
        return out
    }
}

/// Mail.app's on-disk message file: a line with the byte count, the RFC 5322 message, then a property list of flags.
public enum EMLX {
    public static func unwrap(_ data: Data) -> Data {
        guard let nl = data.firstIndex(of: UInt8(ascii: "\n")) else { return data }
        let countText = String(decoding: data[data.startIndex..<nl], as: UTF8.self).trimmingCharacters(in: .whitespacesAndNewlines)
        let start = data.index(after: nl)
        guard let count = Int(countText), count > 0, start + count <= data.endIndex else {
            // Not an emlx after all: hand the bytes on as a plain message.
            return data
        }
        return Data(data[start..<(start + count)])
    }
}
