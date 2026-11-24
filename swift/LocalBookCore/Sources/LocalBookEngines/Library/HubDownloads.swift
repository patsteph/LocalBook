import Foundation
import HuggingFace
import LocalBookCore
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

    /// Hands the loader a model's files. A model already on this device is used as it is, with no network at all;
    /// only a model that is missing or incomplete is fetched from Hugging Face. The hub client would otherwise ask
    /// Hugging Face for the file list on every load (the cache shortcut needs a commit hash, and the loader asks for
    /// `main`), and on a slow or half-connected phone that request can hang the launch for minutes with the model
    /// sitting right there on disk.
    public struct Downloader: MLXLMCommon.Downloader {
        private let hub: HubClient
        public init(_ hub: HubClient = HubClient()) { self.hub = hub }

        public func download(id: String, revision: String?, matching patterns: [String], useLatest: Bool,
                      progressHandler: @Sendable @escaping (Progress) -> Void) async throws -> URL {
            guard let repo = Repo.ID(rawValue: id) else { throw InvalidRepository(id: id) }
            let revision = revision ?? "main"
            let started = Date()
            if !useLatest,
               let local = try? await hub.downloadSnapshot(of: repo, revision: revision, matching: patterns, localFilesOnly: true),
               HubDownloads.isComplete(local, patterns: patterns) {
                SoakTrace.log("model files: \(id) is on this device; no network needed")
                let done = Progress(totalUnitCount: 1); done.completedUnitCount = 1
                progressHandler(done)
                return local
            }
            SoakTrace.log("model files: \(id) is not complete on this device; fetching from Hugging Face")
            let url = try await hub.downloadSnapshot(of: repo, revision: revision, matching: patterns,
                                                     progressHandler: { @MainActor progress in progressHandler(progress) })
            SoakTrace.log("model files: \(id) fetched in \(Int(Date().timeIntervalSince(started))) s")
            return url
        }
    }

    /// Whether a snapshot folder holds everything a load needs: the configuration, at least one weights file when
    /// weights were asked for, and every shard an index lists.
    static func isComplete(_ directory: URL, patterns: [String]) -> Bool {
        let fm = FileManager.default
        guard let files = try? fm.contentsOfDirectory(atPath: directory.path) else { return false }
        let wantsWeights = patterns.contains { $0.hasSuffix("safetensors") }
        if patterns.contains(where: { $0.hasSuffix("json") }), !files.contains("config.json") { return false }
        guard wantsWeights else { return !files.isEmpty }
        guard files.contains(where: { $0.hasSuffix(".safetensors") }) else { return false }
        let indexURL = directory.appending(path: "model.safetensors.index.json")
        if let data = try? Data(contentsOf: indexURL),
           let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let map = json["weight_map"] as? [String: String] {
            return Set(map.values).allSatisfy { files.contains($0) }
        }
        return true
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
