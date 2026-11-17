import Foundation
import LocalBookCore
import UniformTypeIdentifiers
#if canImport(WebKit)
import WebKit
#endif

/// A web address read for ingestion: the old app's `web_scraper._scrape_single` routing in Swift.
/// - YouTube → the transcript and the video's title (`YouTubeTranscript`).
/// - arXiv abstract pages, Google Docs/Slides/Sheets and addresses that end in a document extension → the file,
///   downloaded for `TextExtract`.
/// - anything else → the page loaded in WebKit (scripts run, as they did in Playwright) and read by Readability
///   plus `PageText.js`, which writes the text in trafilatura's shape with the old image-reference section.
public enum WebFetched: Sendable {
    /// A page or transcript, ready for the chunker. `format` is the old app's source type: "web" or "youtube".
    case text(title: String, text: String, format: String, author: String?, date: String?)
    /// A document downloaded to a temporary file (the caller removes it); `name` is its filename with extension.
    case file(URL, name: String)
}

public enum WebExtract {
    public static func isYouTube(_ url: URL) -> Bool {
        let host = (url.host() ?? "").lowercased()
        return host.hasSuffix("youtube.com") || host.hasSuffix("youtu.be")
    }

    /// `_is_document_url`: an address whose path ends in a document extension.
    static func documentExtension(_ url: URL) -> String? {
        let ext = url.pathExtension.lowercased()
        return ["pdf", "pptx", "docx", "xlsx", "doc", "ppt", "xls"].contains(ext) ? ext : nil
    }

    /// `_get_google_export_url`: Google Docs → text, Slides → .pptx, Sheets → .csv.
    static func googleExport(_ url: URL) -> (URL, ext: String)? {
        let s = url.absoluteString
        func id(_ kind: String) -> String? {
            guard let r = s.range(of: "docs\\.google\\.com/\(kind)/d/([a-zA-Z0-9_-]+)", options: .regularExpression) else { return nil }
            return String(s[r]).components(separatedBy: "/d/").last
        }
        if let d = id("document") { return (URL(string: "https://docs.google.com/document/d/\(d)/export?format=txt")!, "txt") }
        if let d = id("presentation") { return (URL(string: "https://docs.google.com/presentation/d/\(d)/export/pptx")!, "pptx") }
        if let d = id("spreadsheets") { return (URL(string: "https://docs.google.com/spreadsheets/d/\(d)/export?format=csv")!, "csv") }
        return nil
    }

    /// `_arxiv_to_pdf_url`: an abstract page becomes its PDF.
    static func arxivPDF(_ url: URL) -> URL? {
        guard (url.host() ?? "").hasSuffix("arxiv.org") else { return nil }
        let path = url.path()
        if path.hasPrefix("/abs/") { return URL(string: "https://arxiv.org/pdf/" + String(path.dropFirst(5)) + ".pdf") }
        if path.hasPrefix("/pdf/") { return url }
        return nil
    }

    /// Read `url`. `progress` hears each stage in words.
    public static func fetch(_ url: URL, progress: (@Sendable (String) -> Void)? = nil) async throws -> WebFetched {
        guard let scheme = url.scheme?.lowercased(), scheme == "http" || scheme == "https" else {
            throw LabError.model("\(url.absoluteString) is not a web address")
        }
        if isYouTube(url) {
            progress?("Getting the transcript…")
            let video = try await YouTubeTranscript.fetch(url)
            return .text(title: video.title, text: video.transcript, format: "youtube", author: nil, date: nil)
        }
        if let pdf = arxivPDF(url) {
            return try await download(pdf, ext: "pdf", progress: progress)
        }
        if let ext = documentExtension(url) {
            return try await download(url, ext: ext, progress: progress)
        }
        if let g = googleExport(url) {
            return try await download(g.0, ext: g.ext, progress: progress)
        }
        #if canImport(WebKit)
        progress?("Loading \(url.host() ?? "the page")…")
        let loader = await PageLoader()
        do {
            let page = try await loader.load(url)
            guard PyLen(page.text) >= 40 else { throw LabError.model("No readable text was found on \(url.host() ?? "the page")") }
            return .text(title: page.title.isEmpty ? url.absoluteString : page.title, text: page.text, format: "web",
                         author: page.author.isEmpty ? nil : page.author, date: page.date.isEmpty ? nil : page.date)
        } catch let served as PageLoader.NotHTML {
            // The address served a document, not a page: take the file instead.
            let ext = UTType(mimeType: served.mime)?.preferredFilenameExtension ?? "bin"
            return try await download(url, ext: ext, progress: progress)
        }
        #else
        throw LabError.model("This device cannot read web pages into sources yet")
        #endif
    }

    /// `_scrape_remote_document`: fetch the file to a temporary path with the right extension.
    static func download(_ url: URL, ext: String, progress: (@Sendable (String) -> Void)?) async throws -> WebFetched {
        progress?("Downloading \(url.lastPathComponent.isEmpty ? (url.host() ?? "the file") : url.lastPathComponent)…")
        var request = URLRequest(url: url, timeoutInterval: 60)
        request.setValue(userAgent, forHTTPHeaderField: "User-Agent")
        let (tmp, response) = try await URLSession.shared.download(for: request)
        if let http = response as? HTTPURLResponse, !(200..<300).contains(http.statusCode) {
            try? FileManager.default.removeItem(at: tmp)
            throw LabError.model("\(url.host() ?? "The server") answered \(http.statusCode) for \(url.lastPathComponent)")
        }
        var name = response.suggestedFilename ?? url.lastPathComponent
        if name.isEmpty || name == "/" { name = url.host() ?? "download" }
        if (name as NSString).pathExtension.isEmpty { name += "." + ext }
        let dest = FileManager.default.temporaryDirectory.appending(path: "localbook-" + UUID().uuidString, directoryHint: .isDirectory).appending(path: name)
        try FileManager.default.createDirectory(at: dest.deletingLastPathComponent(), withIntermediateDirectories: true)
        try FileManager.default.moveItem(at: tmp, to: dest)
        return .file(dest, name: name)
    }

    static let userAgent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/26.0 Safari/605.1.15"
}

#if canImport(WebKit)
/// The read page, as `PageText.js` returns it.
public struct WebPage: Sendable {
    public let title: String
    public let author: String
    public let date: String
    public let text: String
    /// True when Readability found an article; false when the whole page was kept.
    public let readable: Bool
}

/// Loads one page in an off-screen `WKWebView` (scripts run, cookies kept to the load) and reads it with
/// Readability + `PageText.js`. The old app's Playwright fetch and trafilatura extraction, in one object.
@MainActor
public final class PageLoader: NSObject, WKNavigationDelegate {
    /// The address served something other than a page; `mime` says what.
    public struct NotHTML: Error { public let mime: String }

    private var webView: WKWebView?
    private var navigation: CheckedContinuation<Void, Error>?
    private var nonHTML: String?

    public override init() { super.init() }

    public func load(_ url: URL, timeout: Duration = .seconds(30), settle: Duration = .seconds(1.5)) async throws -> WebPage {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.defaultWebpagePreferences.allowsContentJavaScript = true
        let view = WKWebView(frame: CGRect(x: 0, y: 0, width: 1280, height: 900), configuration: config)
        view.customUserAgent = WebExtract.userAgent
        view.navigationDelegate = self
        webView = view
        defer { view.navigationDelegate = nil; webView = nil }

        var request = URLRequest(url: url)
        request.timeoutInterval = 25
        let timer = Task { @MainActor [weak self] in
            try await Task.sleep(for: timeout)
            self?.webView?.stopLoading()
            self?.finish(LabError.model("\(url.host() ?? "The page") took too long to load"))
        }
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            self.navigation = cont
            view.load(request)
        }
        timer.cancel()
        if let mime = nonHTML { throw NotHTML(mime: mime) }
        try? await Task.sleep(for: settle)          // scripts that fill the page after load

        guard let page = try await evaluate(Self.script, in: view) else {
            throw LabError.model("\(url.host() ?? "The page") could not be read")
        }
        return page
    }

    /// Runs the reader and turns its object into a `WebPage` inside the completion, so only a Sendable value crosses.
    private func evaluate(_ script: String, in view: WKWebView) async throws -> WebPage? {
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<WebPage?, Error>) in
            view.evaluateJavaScript(script) { value, error in
                if let error { cont.resume(throwing: error); return }
                guard let dict = value as? [String: Any], let text = dict["text"] as? String else { cont.resume(returning: nil); return }
                cont.resume(returning: WebPage(title: dict["title"] as? String ?? "", author: dict["author"] as? String ?? "",
                                               date: dict["date"] as? String ?? "", text: text, readable: dict["readable"] as? Bool ?? false))
            }
        }
    }

    private func finish(_ error: Error?) {
        guard let cont = navigation else { return }
        navigation = nil
        if let error { cont.resume(throwing: error) } else { cont.resume() }
    }

    // MARK: WKNavigationDelegate

    public func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { finish(nil) }
    public func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { failed(error) }
    public func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { failed(error) }
    /// A cancelled navigation is our own doing (a document instead of a page); the result is read from `nonHTML`.
    private func failed(_ error: Error) { if nonHTML != nil { finish(nil) } else { finish(error) } }
    public func webView(_ webView: WKWebView, decidePolicyFor navigationResponse: WKNavigationResponse) async -> WKNavigationResponsePolicy {
        guard navigationResponse.isForMainFrame else { return .allow }
        let mime = (navigationResponse.response.mimeType ?? "text/html").lowercased()
        if mime.contains("html") || mime.contains("xml") || mime.hasPrefix("text/") { return .allow }
        nonHTML = mime
        return .cancel
    }

    /// Readability.js followed by the reader; both ship as package resources.
    nonisolated static let script: String = {
        func read(_ name: String) -> String {
            guard let url = Bundle.module.url(forResource: name, withExtension: "js", subdirectory: "Resources") ?? Bundle.module.url(forResource: name, withExtension: "js"),
                  let s = try? String(contentsOf: url, encoding: .utf8) else { return "" }
            return s
        }
        return read("Readability") + "\n" + read("PageText")
    }()
}
#endif

private func PyLen(_ s: String) -> Int { s.unicodeScalars.count }
