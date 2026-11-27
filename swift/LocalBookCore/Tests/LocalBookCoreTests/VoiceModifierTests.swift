import Foundation
import Testing
@testable import LocalBookCore

@Suite struct VoiceModifierTests {
    @Test func families() {
        #expect(VoiceModifier.family(of: "mlx-community/gemma-4-e4b-it-4bit") == "gemma")
        #expect(VoiceModifier.family(of: "mlx-community/Phi-4-mini-instruct-4bit") == "phi")
        #expect(VoiceModifier.family(of: "mlx-community/Llama-3.2-3B-Instruct-4bit") == "llama")
        #expect(VoiceModifier.family(of: "mlx-community/Mistral-7B-Instruct-v0.3-4bit") == "mistral")
        #expect(VoiceModifier.family(of: "mlx-community/OLMo-2-1124-7B-Instruct-4bit") == "olmo")
        #expect(VoiceModifier.family(of: "mlx-community/Qwen3-4B-4bit") == "default")
        #expect(VoiceModifier.family(of: nil) == "default")
        #expect(VoiceModifier.text(for: "x/gemma-3").hasPrefix("Write tightly."))
    }

    @Test func appliesOnce() {
        UserDefaults.standard.removeObject(forKey: VoiceModifier.defaultsKey)
        let once = VoiceModifier.apply(to: "Answer from the notebook.", modelId: "x/gemma-4")
        #expect(once.hasPrefix(VoiceModifier.text(for: "x/gemma-4") + "\n\nAnswer"))
        #expect(VoiceModifier.apply(to: once, modelId: "x/gemma-4") == once)
        #expect(VoiceModifier.apply(to: "", modelId: "x/gemma-4") == "")
        UserDefaults.standard.set(false, forKey: VoiceModifier.defaultsKey)
        #expect(VoiceModifier.apply(to: "Plain.", modelId: "x/gemma-4") == "Plain.")
        UserDefaults.standard.removeObject(forKey: VoiceModifier.defaultsKey)
    }
}
