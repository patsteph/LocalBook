import Foundation

/// The voice that reads answers aloud, chosen once in Settings and shared by the Mac and the phone. Every entry is a
/// Kokoro-82M English voice pack FluidAudio knows; only Heart ships with the model, the others download the first time
/// they are used (a few hundred kilobytes each) and are kept.
public struct ReadAloudVoice: Identifiable, Hashable, Sendable {
    public enum Accent: String, CaseIterable, Sendable { case american, british }

    /// The Kokoro voice pack name, such as `af_heart`.
    public let id: String
    public let name: String
    public let accent: Accent
    public let feminine: Bool

    public var label: String { "\(name) · \(accent == .american ? "American" : "British") \(feminine ? "woman" : "man")" }

    public static let all: [ReadAloudVoice] = [
        .init(id: "af_heart", name: "Heart", accent: .american, feminine: true),
        .init(id: "af_bella", name: "Bella", accent: .american, feminine: true),
        .init(id: "af_nova", name: "Nova", accent: .american, feminine: true),
        .init(id: "af_sarah", name: "Sarah", accent: .american, feminine: true),
        .init(id: "af_sky", name: "Sky", accent: .american, feminine: true),
        .init(id: "af_nicole", name: "Nicole", accent: .american, feminine: true),
        .init(id: "am_michael", name: "Michael", accent: .american, feminine: false),
        .init(id: "am_adam", name: "Adam", accent: .american, feminine: false),
        .init(id: "am_eric", name: "Eric", accent: .american, feminine: false),
        .init(id: "am_liam", name: "Liam", accent: .american, feminine: false),
        .init(id: "am_onyx", name: "Onyx", accent: .american, feminine: false),
        .init(id: "am_puck", name: "Puck", accent: .american, feminine: false),
        .init(id: "bf_emma", name: "Emma", accent: .british, feminine: true),
        .init(id: "bf_alice", name: "Alice", accent: .british, feminine: true),
        .init(id: "bf_isabella", name: "Isabella", accent: .british, feminine: true),
        .init(id: "bf_lily", name: "Lily", accent: .british, feminine: true),
        .init(id: "bm_george", name: "George", accent: .british, feminine: false),
        .init(id: "bm_daniel", name: "Daniel", accent: .british, feminine: false),
        .init(id: "bm_fable", name: "Fable", accent: .british, feminine: false),
        .init(id: "bm_lewis", name: "Lewis", accent: .british, feminine: false),
    ]

    public static let standard = all[0]

    public static func named(_ id: String) -> ReadAloudVoice? { all.first { $0.id == id } }

    /// The chosen voice (`readAloudVoice`), Heart until one is chosen.
    public static var current: ReadAloudVoice {
        named(UserDefaults.standard.string(forKey: "readAloudVoice") ?? "") ?? standard
    }

    /// Reading speed, 0.7 to 1.3 (`readAloudSpeed`), 1.0 until changed.
    public static var speed: Float {
        let v = UserDefaults.standard.object(forKey: "readAloudSpeed") as? Double ?? 1.0
        return Float(min(1.3, max(0.7, v)))
    }

    /// A line to try a voice with.
    public static let sampleLine = "Here is how I sound reading one of your answers aloud."
}
