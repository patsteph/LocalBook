import SwiftUI
import AVKit
import WebKit

/// Shows a Mac-made audio or video item: the file downloads once (with progress), then `content` plays it.
struct MediaGate<Content: View>: View {
    let item: StudioItem
    @ViewBuilder var content: () -> Content
    @State private var have = false
    @State private var progress = 0.0
    @State private var loading = false
    @State private var failed = false

    private var url: URL? { item.mediaFile.map(StudioStore.audioURL) }

    var body: some View {
        Group {
            if have || item.mediaFile == nil { content() }
            else {
                VStack(spacing: 14) {
                    Image(systemName: item.kind.symbol).font(.system(size: 44)).foregroundStyle(item.kind.tint)
                    Text("Made on your Mac").font(.headline)
                    if let b = item.mediaBytes { Text(ByteCountFormatter.string(fromByteCount: Int64(b), countStyle: .file)).font(.footnote).foregroundStyle(.secondary) }
                    if loading { ProgressView(value: progress).padding(.horizontal, 40) }
                    else {
                        Button("Download to this phone") { start() }.buttonStyle(.borderedProminent)
                        if failed { Text("Couldn't reach your Mac. Try again when it is on.").font(.footnote).foregroundStyle(.secondary) }
                    }
                }
                .padding()
            }
        }
        .onAppear { if let url { have = FileManager.default.fileExists(atPath: url.path) } }
    }

    private func start() {
        loading = true; failed = false
        Task {
            do {
                try await StudioPull.download(item) { progress = $0 }
                have = true
            } catch { failed = true }
            loading = false
        }
    }
}

struct VideoItemView: View {
    let info: VideoInfo
    @State private var player: AVPlayer?

    var body: some View {
        Group {
            if let player { VideoPlayer(player: player) } else { ProgressView() }
        }
        .onAppear { player = AVPlayer(url: StudioStore.audioURL(info.file)) }
        .onDisappear { player?.pause(); player = nil }
    }
}

/// Draws an SVG made on the Mac. JavaScript is off and nothing can navigate away, so the markup can only be drawn.
struct SVGItemView: UIViewRepresentable {
    let svg: String

    func makeCoordinator() -> Coordinator { Coordinator() }

    final class Coordinator: NSObject, WKNavigationDelegate {
        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, preferences: WKWebpagePreferences) async -> (WKNavigationActionPolicy, WKWebpagePreferences) {
            preferences.allowsContentJavaScript = false
            return (action.navigationType == .other ? .allow : .cancel, preferences)
        }
    }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.defaultWebpagePreferences.allowsContentJavaScript = false
        let view = WKWebView(frame: .zero, configuration: config)
        view.navigationDelegate = context.coordinator
        view.isOpaque = false; view.backgroundColor = .clear
        view.scrollView.minimumZoomScale = 1; view.scrollView.maximumZoomScale = 6
        let page = "<html><head><meta name=viewport content='width=device-width,initial-scale=1,user-scalable=yes'><style>body{margin:0;display:flex;justify-content:center}svg{max-width:100%;height:auto}</style></head><body>\(svg)</body></html>"
        view.loadHTMLString(page, baseURL: nil)
        return view
    }

    func updateUIView(_ view: WKWebView, context: Context) {}
}
