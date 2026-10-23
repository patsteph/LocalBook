import SwiftUI

/// What an item turns into when shared: a file for audio and video, readable text for the rest. Visuals share from their own screen.
enum StudioShare {
    static func fileURL(_ item: StudioItem) -> URL? {
        let name: String?
        switch item.kind {
        case .audio: name = (try? JSONDecoder().decode(AudioScript.self, from: Data(item.body.utf8)))?.file
        case .video: name = (try? JSONDecoder().decode(VideoInfo.self, from: Data(item.body.utf8)))?.file
        default: name = nil
        }
        guard let name else { return nil }
        let url = StudioStore.audioURL(name)
        return FileManager.default.fileExists(atPath: url.path) ? url : nil
    }

    static func text(_ item: StudioItem) -> String? {
        switch item.kind {
        case .doc: return item.body
        case .quiz:
            let qs = (try? JSONDecoder().decode([StudioQuestion].self, from: Data(item.body.utf8))) ?? []
            guard !qs.isEmpty else { return nil }
            let body = qs.enumerated().map { i, q in
                let choices = q.choices.enumerated().map { "   \(["A", "B", "C", "D", "E", "F"][min($0.offset, 5)]). \($0.element)" }.joined(separator: "\n")
                let answer = q.choices.indices.contains(q.answer) ? "Answer: \(q.choices[q.answer])" : ""
                return "\(i + 1). \(q.question)\n\(choices)\n   \(answer)" + (q.why.map { "\n   \($0)" } ?? "")
            }.joined(separator: "\n\n")
            return item.title + "\n\n" + body
        case .cards:
            let cs = (try? JSONDecoder().decode([StudioCard].self, from: Data(item.body.utf8))) ?? []
            guard !cs.isEmpty else { return nil }
            return item.title + "\n\n" + cs.map { "\($0.front)\n   \($0.back)" }.joined(separator: "\n\n")
        case .audio:
            guard let s = try? JSONDecoder().decode(AudioScript.self, from: Data(item.body.utf8)) else { return nil }
            return item.title + "\n\n" + s.lines.map { "\(StudioEngine.hostNames[$0.host]): \($0.text)" }.joined(separator: "\n\n")
        case .visual, .video: return nil
        }
    }
}

struct StudioShareButton: View {
    let item: StudioItem
    var body: some View {
        if let url = StudioShare.fileURL(item) {
            ShareLink(item: url) { Label("Share", systemImage: "square.and.arrow.up") }
        } else if let text = StudioShare.text(item) {
            ShareLink(item: text, subject: Text(item.title)) { Label("Share", systemImage: "square.and.arrow.up") }
        }
    }
}
