import Foundation
import HuggingFace
import MLXLMCommon
import Tokenizers

/// The two small bridges the model loaders need, written out instead of taken from mlx-swift-lm's MLXHuggingFace
/// macros. MLXHuggingFace pulls in the package's FoundationModels adapter (a default trait), and that adapter does
/// not compile against the iOS 27 SDK; the macros expanded to exactly these two types, so nothing is lost by
/// writing them here and leaving that library out of both apps.
public enum HubDownloads {
    struct InvalidRepository: Error, LocalizedError {
        let id: String
        var errorDescription: String? { "“\(id)” is not a model repository id" }
    }

    /// Downloads a model snapshot (or returns the cached one) through swift-huggingface's hub client.
    public struct Downloader: MLXLMCommon.Downloader {
        private let hub: HubClient
        public init(_ hub: HubClient = HubClient()) { self.hub = hub }

        public func download(id: String, revision: String?, matching patterns: [String], useLatest: Bool,
                      progressHandler: @Sendable @escaping (Progress) -> Void) async throws -> URL {
            guard let repo = Repo.ID(rawValue: id) else { throw InvalidRepository(id: id) }
            return try await hub.downloadSnapshot(of: repo, revision: revision ?? "main", matching: patterns,
                                                  progressHandler: { @MainActor progress in progressHandler(progress) })
        }
    }

    /// Loads a model folder's tokenizer with swift-transformers and presents it as the runtime's tokenizer.
    public struct TokenizerLoader: MLXLMCommon.TokenizerLoader {
        public init() {}
        public func load(from directory: URL) async throws -> any MLXLMCommon.Tokenizer {
            TokenizerBridge(try await AutoTokenizer.from(modelFolder: directory))
        }
    }

    struct TokenizerBridge: MLXLMCommon.Tokenizer {
        private let upstream: any Tokenizers.Tokenizer
        init(_ upstream: any Tokenizers.Tokenizer) { self.upstream = upstream }

        func encode(text: String, addSpecialTokens: Bool) -> [Int] { upstream.encode(text: text, addSpecialTokens: addSpecialTokens) }
        func decode(tokenIds: [Int], skipSpecialTokens: Bool) -> String { upstream.decode(tokens: tokenIds, skipSpecialTokens: skipSpecialTokens) }
        func convertTokenToId(_ token: String) -> Int? { upstream.convertTokenToId(token) }
        func convertIdToToken(_ id: Int) -> String? { upstream.convertIdToToken(id) }
        var bosToken: String? { upstream.bosToken }
        var eosToken: String? { upstream.eosToken }
        var unknownToken: String? { upstream.unknownToken }

        func applyChatTemplate(messages: [[String: any Sendable]], tools: [[String: any Sendable]]?,
                               additionalContext: [String: any Sendable]?) throws -> [Int] {
            do {
                return try upstream.applyChatTemplate(messages: messages, tools: tools, additionalContext: additionalContext)
            } catch Tokenizers.TokenizerError.missingChatTemplate {
                throw MLXLMCommon.TokenizerError.missingChatTemplate
            }
        }
    }
}
