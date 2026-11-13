import Foundation
import LocalBookCore
import MLXLMCommon

/// The recipe's decode settings as MLX generate parameters. Lives with the engines so the pure recipe does not depend on MLX.
extension AnswerRecipe {
    public var generateParameters: GenerateParameters { parameters(maxTokens: nil) }

    public func parameters(maxTokens: Int?) -> GenerateParameters {
        var p = GenerateParameters(maxTokens: maxTokens ?? generation.maxTokens, temperature: generation.temperature)
        p.topP = generation.topP
        p.repetitionPenalty = generation.repetitionPenalty
        p.repetitionContextSize = generation.repetitionContextSize
        return p
    }
}
