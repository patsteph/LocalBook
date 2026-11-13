import Foundation

/// One thing made in Studio on this phone: a written piece, a quiz or a set of flashcards. Kept as a small JSON file
/// per item, so it is there offline and nothing about it leaves the phone.
public struct StudioItem: Codable, Identifiable, Sendable, Hashable {
    public enum Kind: String, Codable, CaseIterable, Sendable {
        case doc, quiz, cards, audio, visual, video
        public var label: String { switch self { case .doc: "Briefing"; case .quiz: "Quiz"; case .cards: "Flashcards"; case .audio: "Audio overview"; case .visual: "Visual"; case .video: "Video" } }
        public var short: String { self == .audio ? "Audio" : label }
        public var symbol: String { switch self { case .doc: "doc.text"; case .quiz: "checklist"; case .cards: "rectangle.stack"; case .audio: "waveform"; case .visual: "chart.bar.doc.horizontal"; case .video: "film" } }
    }
    public var id: String
    public var notebookId: String
    public var kind: Kind
    public var title: String
    public var created: Date
    /// visual: the JSON of a VisualSpec. audio: the JSON of an AudioScript. doc: the text (Markdown-light). quiz / cards: the JSON of [StudioQuestion] / [StudioCard].
    public var body: String
    /// Items brought over from a Mac: "mac". Phone-made items leave this empty.
    public var origin: String? = nil
    /// The Mac's revision stamp for a Mac item, so only changed ones are fetched again.
    public var rev: String? = nil
    /// Audio or video file kept next to the item (Mac items download on request), and its size on the Mac.
    public var mediaFile: String? = nil
    public var mediaBytes: Int? = nil
}

/// A video: the file, how long it is, and the narration lines it speaks.
public struct VideoInfo: Codable, Sendable, Hashable {
    public var file: String
    public var duration: Double
    public var lines: [String] = []
}

public struct StudioQuestion: Codable, Sendable, Identifiable, Hashable {
    public var id: String { question }
    public let question: String
    public let choices: [String]
    public let answer: Int
    public let why: String?
}

public struct StudioCard: Codable, Sendable, Identifiable, Hashable {
    public var id: String { front }
    public let front: String
    public let back: String
}

public enum StudioStore {
    public static var folder: URL {
        let dir = AppFolders.base.appendingPathComponent("Studio", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    private static func url(_ id: String) -> URL { folder.appendingPathComponent(id + ".json") }

    public static func all(notebook: String?) -> [StudioItem] {
        let files = (try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)) ?? []
        let decoder = JSONDecoder()
        let items = files.filter { $0.pathExtension == "json" }.compactMap { try? decoder.decode(StudioItem.self, from: Data(contentsOf: $0)) }
        return items.filter { notebook == nil || $0.notebookId == notebook }.sorted { $0.created > $1.created }
    }

    public static func save(_ item: StudioItem) {
        if let data = try? JSONEncoder().encode(item) { try? data.write(to: url(item.id), options: .atomic) }
    }

    public static func delete(_ item: StudioItem) {
        if let media = item.mediaFile { try? FileManager.default.removeItem(at: audioURL(media)) }
        if item.kind == .video, let info = try? JSONDecoder().decode(VideoInfo.self, from: Data(item.body.utf8)) {
            try? FileManager.default.removeItem(at: audioURL(info.file))
        }
        if item.kind == .audio, let script = try? JSONDecoder().decode(AudioScript.self, from: Data(item.body.utf8)) {
            try? FileManager.default.removeItem(at: audioURL(script.file))
        }
        try? FileManager.default.removeItem(at: url(item.id))
    }

    public static func audioURL(_ file: String) -> URL { folder.appendingPathComponent(file) }
}

/// A phone-made audio overview: the spoken lines (host 0 = Alex, 1 = Sam) with where each starts in the audio file.
public struct AudioScript: Codable, Sendable, Hashable {
    public struct Line: Codable, Sendable, Hashable { public var host: Int; public var text: String; public var start: Double }
    public var file: String
    public var duration: Double
    public var lines: [Line]
}
