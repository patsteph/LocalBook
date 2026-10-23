import Foundation

/// One thing made in Studio on this phone: a written piece, a quiz or a set of flashcards. Kept as a small JSON file
/// per item, so it is there offline and nothing about it leaves the phone.
struct StudioItem: Codable, Identifiable, Sendable, Hashable {
    enum Kind: String, Codable, CaseIterable, Sendable {
        case doc, quiz, cards
        var label: String { switch self { case .doc: "Briefing"; case .quiz: "Quiz"; case .cards: "Flashcards" } }
        var symbol: String { switch self { case .doc: "doc.text"; case .quiz: "checklist"; case .cards: "rectangle.stack" } }
    }
    var id: String
    var notebookId: String
    var kind: Kind
    var title: String
    var created: Date
    /// doc: the text (Markdown-light). quiz / cards: the JSON of [StudioQuestion] / [StudioCard].
    var body: String
}

struct StudioQuestion: Codable, Sendable, Identifiable, Hashable {
    var id: String { question }
    let question: String
    let choices: [String]
    let answer: Int
    let why: String?
}

struct StudioCard: Codable, Sendable, Identifiable, Hashable {
    var id: String { front }
    let front: String
    let back: String
}

enum StudioStore {
    static var folder: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        let dir = base.appendingPathComponent("Studio", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }

    private static func url(_ id: String) -> URL { folder.appendingPathComponent(id + ".json") }

    static func all(notebook: String?) -> [StudioItem] {
        let files = (try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)) ?? []
        let decoder = JSONDecoder()
        let items = files.filter { $0.pathExtension == "json" }.compactMap { try? decoder.decode(StudioItem.self, from: Data(contentsOf: $0)) }
        return items.filter { notebook == nil || $0.notebookId == notebook }.sorted { $0.created > $1.created }
    }

    static func save(_ item: StudioItem) {
        if let data = try? JSONEncoder().encode(item) { try? data.write(to: url(item.id), options: .atomic) }
    }

    static func delete(_ item: StudioItem) { try? FileManager.default.removeItem(at: url(item.id)) }
}
