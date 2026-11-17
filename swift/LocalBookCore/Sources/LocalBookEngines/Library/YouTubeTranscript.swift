import Foundation
import LocalBookCore

/// A YouTube video's transcript and title, fetched as the old app's `youtube-transcript-api` and oEmbed call did:
/// the watch page gives the Innertube key, the player endpoint (Android client) lists the caption tracks, and the
/// chosen track's timed text is joined with spaces. English first, by hand before generated, then any language.
public enum YouTubeTranscript {
    public struct Video: Sendable {
        public let id: String
        public let title: String
        public let transcript: String
        public let language: String
    }

    /// `_extract_youtube_id`.
    public static func videoId(_ url: URL) -> String? {
        let host = (url.host() ?? "").lowercased()
        if host.hasSuffix("youtu.be") {
            let id = url.lastPathComponent
            return id.isEmpty || id == "/" ? nil : id
        }
        guard host.hasSuffix("youtube.com") else { return nil }
        let path = url.path()
        if path == "/watch" {
            return URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems?.first(where: { $0.name == "v" })?.value
        }
        for prefix in ["/embed/", "/v/", "/shorts/", "/live/"] where path.hasPrefix(prefix) {
            let rest = path.dropFirst(prefix.count)
            return rest.split(separator: "/").first.map(String.init)
        }
        return nil
    }

    public static func fetch(_ url: URL) async throws -> Video {
        guard let id = videoId(url) else { throw LabError.model("Could not extract video ID from URL") }
        let html = try await watchPage(id)
        guard let key = firstGroup("\"INNERTUBE_API_KEY\":\\s*\"([a-zA-Z0-9_-]+)\"", in: html) else {
            if html.contains("class=\"g-recaptcha\"") { throw LabError.model("YouTube asked for a captcha; try again later") }
            throw LabError.model("YouTube's page could not be read")
        }
        let player = try await playerResponse(id, key: key)
        try checkPlayability(player["playabilityStatus"] as? [String: Any])
        guard let captions = (player["captions"] as? [String: Any])?["playerCaptionsTracklistRenderer"] as? [String: Any],
              let tracks = captions["captionTracks"] as? [[String: Any]], !tracks.isEmpty else {
            throw LabError.model("Subtitles are disabled for this video")
        }
        guard let track = choose(tracks), let base = track["baseUrl"] as? String,
              let trackURL = URL(string: base.replacingOccurrences(of: "&fmt=srv3", with: "")) else {
            throw LabError.model("No transcript available for this video")
        }
        let xml = try await get(trackURL)
        let snippets = TimedTextParser.parse(xml)
        let text = snippets.joined(separator: " ")
        guard !text.trimmingCharacters(in: .whitespaces).isEmpty else { throw LabError.model("No transcript available for this video") }
        let title = await title(id)
        return Video(id: id, title: title, transcript: text, language: track["languageCode"] as? String ?? "")
    }

    /// `find_transcript(["en"])` first; then any track, by hand before generated, so a video in another language still comes in.
    static func choose(_ tracks: [[String: Any]]) -> [String: Any]? {
        let manual = tracks.filter { ($0["kind"] as? String ?? "") != "asr" }
        let generated = tracks.filter { ($0["kind"] as? String ?? "") == "asr" }
        func english(_ list: [[String: Any]]) -> [String: Any]? { list.first { ($0["languageCode"] as? String ?? "").hasPrefix("en") } }
        return english(manual) ?? english(generated) ?? manual.first ?? generated.first
    }

    /// `_assert_playability`, with the old app's messages.
    static func checkPlayability(_ status: [String: Any]?) throws {
        guard let status, let state = status["status"] as? String, state != "OK" else { return }
        let reason = status["reason"] as? String ?? ""
        if state == "LOGIN_REQUIRED" {
            if reason.contains("bot") { throw LabError.model("YouTube is blocking requests from this network; try again later") }
            if reason.contains("age") { throw LabError.model("This video is age restricted") }
        }
        if state == "ERROR" && reason.contains("unavailable") { throw LabError.model("Video is unavailable or private") }
        throw LabError.model(reason.isEmpty ? "Video is unplayable" : reason)
    }

    /// `_get_youtube_title` over oEmbed, with its fallback name.
    static func title(_ id: String) async -> String {
        let fallback = "YouTube Video \(id)"
        guard let url = URL(string: "https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v=\(id)&format=json") else { return fallback }
        for timeout in [5.0, 10.0] {
            var request = URLRequest(url: url, timeoutInterval: timeout)
            request.setValue(WebExtract.userAgent, forHTTPHeaderField: "User-Agent")
            if let got = try? await URLSession.shared.data(for: request),
               (got.1 as? HTTPURLResponse)?.statusCode == 200,
               let json = try? JSONSerialization.jsonObject(with: got.0) as? [String: Any],
               let title = json["title"] as? String, !title.isEmpty { return title }
        }
        return fallback
    }

    // MARK: fetching

    /// `_fetch_video_html`, with the consent cookie when Europe's consent page answers instead.
    static func watchPage(_ id: String) async throws -> String {
        let url = URL(string: "https://www.youtube.com/watch?v=\(id)")!
        var html = try await get(url)
        if html.contains("action=\"https://consent.youtube.com/s\"") {
            guard let v = firstGroup("name=\"v\" value=\"(.*?)\"", in: html) else { throw LabError.model("YouTube's consent page could not be passed") }
            html = try await get(url, cookie: "CONSENT=YES+\(v)")
            if html.contains("action=\"https://consent.youtube.com/s\"") { throw LabError.model("YouTube's consent page could not be passed") }
        }
        return html
    }

    static func playerResponse(_ id: String, key: String) async throws -> [String: Any] {
        var request = URLRequest(url: URL(string: "https://www.youtube.com/youtubei/v1/player?key=\(key)")!, timeoutInterval: 20)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(WebExtract.userAgent, forHTTPHeaderField: "User-Agent")
        request.httpBody = try JSONSerialization.data(withJSONObject: [
            "context": ["client": ["clientName": "ANDROID", "clientVersion": "20.10.38"]],
            "videoId": id,
        ])
        let (data, response) = try await URLSession.shared.data(for: request)
        try check(response, what: "YouTube's player")
        guard let json = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { throw LabError.model("YouTube's player answer could not be read") }
        return json
    }

    static func get(_ url: URL, cookie: String? = nil) async throws -> String {
        var request = URLRequest(url: url, timeoutInterval: 20)
        request.setValue(WebExtract.userAgent, forHTTPHeaderField: "User-Agent")
        request.setValue("en-US,en;q=0.9", forHTTPHeaderField: "Accept-Language")
        if let cookie { request.setValue(cookie, forHTTPHeaderField: "Cookie") }
        let (data, response) = try await URLSession.shared.data(for: request)
        try check(response, what: url.host() ?? "YouTube")
        return String(decoding: data, as: UTF8.self)
    }

    static func check(_ response: URLResponse, what: String) throws {
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            throw LabError.model(http.statusCode == 429 ? "YouTube is rate limiting this network; try again later" : "\(what) answered \(http.statusCode)")
        }
    }

    static func firstGroup(_ pattern: String, in s: String) -> String? {
        guard let re = try? NSRegularExpression(pattern: pattern), let m = re.firstMatch(in: s, range: NSRange(s.startIndex..., in: s)),
              m.numberOfRanges > 1, let r = Range(m.range(at: 1), in: s) else { return nil }
        return String(s[r])
    }
}

/// The timed-text XML: `<text start="…" dur="…">…</text>`, each entry's text with HTML entities decoded once more
/// (YouTube double-encodes them) and tags removed, as `_TranscriptParser.parse` did.
final class TimedTextParser: NSObject, XMLParserDelegate {
    private var pieces: [String] = []
    private var current: String?

    static func parse(_ xml: String) -> [String] {
        let p = TimedTextParser()
        let parser = XMLParser(data: Data(xml.utf8))
        parser.delegate = p
        parser.parse()
        return p.pieces
    }

    func parser(_ parser: XMLParser, didStartElement name: String, namespaceURI: String?, qualifiedName: String?, attributes: [String: String]) {
        if name == "text" || name == "p" { current = "" }
    }
    func parser(_ parser: XMLParser, foundCharacters string: String) { current?.append(string) }
    func parser(_ parser: XMLParser, didEndElement name: String, namespaceURI: String?, qualifiedName: String?) {
        guard name == "text" || name == "p", let raw = current else { return }
        current = nil
        let unescaped = HTMLEntities.decode(raw)
        let stripped = unescaped.replacingOccurrences(of: "<[^>]*>", with: "", options: .regularExpression)
        if !stripped.isEmpty { pieces.append(stripped) }
    }
}

/// Python's `html.unescape` for the entities captions use.
enum HTMLEntities {
    static let named: [String: String] = ["amp": "&", "lt": "<", "gt": ">", "quot": "\"", "apos": "'", "nbsp": "\u{A0}", "#39": "'"]

    static func decode(_ s: String) -> String {
        guard s.contains("&") else { return s }
        var out = ""
        var i = s.startIndex
        while i < s.endIndex {
            if s[i] == "&", let semi = s[i...].firstIndex(of: ";"), s.distance(from: i, to: semi) <= 8 {
                let body = String(s[s.index(after: i)..<semi])
                var replacement: String?
                if body.hasPrefix("#x") || body.hasPrefix("#X") { if let v = UInt32(body.dropFirst(2), radix: 16), let u = Unicode.Scalar(v) { replacement = String(u) } }
                else if body.hasPrefix("#") { if let v = UInt32(body.dropFirst()), let u = Unicode.Scalar(v) { replacement = String(u) } }
                else { replacement = named[body] }
                if let replacement { out += replacement; i = s.index(after: semi); continue }
            }
            out.append(s[i]); i = s.index(after: i)
        }
        return out
    }
}
