import Foundation

/// The old app's `voice_modifier.py`: two or three sentences about tone, by model family, put in front of the answer
/// instructions so answers read the same across models (Gemma hedges, Phi over-formats). Tone only, never content or
/// shape, and never for Studio's structured output. Off with the `voiceModifier` default set to false.
public enum VoiceModifier {
    public static let defaultsKey = "voiceModifier"

    public static var enabled: Bool {
        UserDefaults.standard.object(forKey: defaultsKey) as? Bool ?? true
    }

    public static let families: [String: String] = [
        "olmo": "Write in concise, journalistic prose. Lead with the conclusion. Skip preamble and meta-commentary.",
        "gemma": "Write tightly. Skip preamble — start with the substance. Avoid hedging phrases like 'it is worth noting', 'in the context of', or 'fundamentally'. Use bold and italics sparingly.",
        "phi": "Be direct. Avoid filler words and over-formatted markdown. Match the response length to the question's depth.",
        "llama": "Write clearly and directly. Avoid unnecessary preamble or sign-off.",
        "mistral": "Write directly. Skip apologies and meta-commentary.",
        "granite": "Write clearly and concisely. Skip preamble; lead with substance.",
        "default": "Write directly and concisely. Skip preamble.",
    ]

    /// `mlx-community/gemma-4-e4b-it-4bit` → `gemma`; a name with no known family → `default`.
    public static func family(of modelId: String?) -> String {
        guard let id = modelId?.lowercased(), !id.isEmpty else { return "default" }
        let name = id.split(separator: "/").last.map(String.init) ?? id
        for fam in ["olmo", "gemma", "phi", "llama", "mistral", "granite"] where name.hasPrefix(fam) || name.contains("-" + fam) || name.contains("_" + fam) { return fam }
        return "default"
    }

    public static func text(for modelId: String?) -> String { families[family(of: modelId)] ?? families["default"]! }

    /// `instructions` with the modifier in front, unless it is off or already there.
    public static func apply(to instructions: String, modelId: String?) -> String {
        guard enabled, !instructions.isEmpty else { return instructions }
        let voice = text(for: modelId)
        if instructions.drop(while: \.isWhitespace).hasPrefix(voice) { return instructions }
        return voice + "\n\n" + instructions
    }
}
