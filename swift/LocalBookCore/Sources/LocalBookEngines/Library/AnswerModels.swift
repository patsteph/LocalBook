import Foundation
import HuggingFace
import LocalBookCore
import MLXHuggingFace
import MLXLLM
import MLXLMCommon
import MLXVLM
import Tokenizers
#if canImport(Metal)
import Metal
#endif

/// The answer models on this device: which one answers, which are downloaded, what is on Hugging Face, and whether a
/// model fits in memory. The Mac's LLM Studio (Settings › Models) is built on this; the iPhone keeps the default.
///
/// Models live in the Hub cache the MLX loader uses (`Library/Caches/huggingface/hub` inside the sandbox), so a model
/// downloaded here is the model the loader finds, and the loader's own download lands in the same place.
public enum AnswerModels {
    public static let defaultId = "mlx-community/gemma-4-e4b-it-4bit"
    private static let key = "answerModelId"

    /// The model that answers questions. Changing it takes effect on the next load (`LibraryModel.reloadAnswerModel`).
    public static var chosenId: String {
        UserDefaults.standard.string(forKey: key) ?? defaultId
    }

    public static func choose(_ id: String) {
        if id == defaultId { UserDefaults.standard.removeObject(forKey: key) } else { UserDefaults.standard.set(id, forKey: key) }
    }

    /// What the loader is given: the registry's entry when it has one (it knows the model's extra end-of-turn tokens),
    /// otherwise a configuration built from the id with the Gemma family's end-of-turn token where the name says Gemma.
    public static var configuration: ModelConfiguration { configuration(for: chosenId) }

    public static func configuration(for id: String) -> ModelConfiguration {
        if LLMModelFactory.shared.contains(id: id) { return LLMModelFactory.shared.configuration(id: id) }
        let lower = id.lowercased()
        let eos: Set<String> = lower.contains("gemma-4") ? ["<turn|>"] : lower.contains("gemma") ? ["<end_of_turn>"] : []
        return ModelConfiguration(id: id, extraEOSTokens: eos)
    }

    /// Whether the model last loaded can look at a picture (it was loaded with its vision tower).
    nonisolated(unsafe) public private(set) static var loadedWithVision = false
    /// Why the last load went without the vision tower, for the settings line; nil when it loaded with vision.
    nonisolated(unsafe) public private(set) static var visionProblem: String?

    /// Loads the chosen answer model. On the Mac the checkpoint is loaded with its vision tower when it has one, so the
    /// visuals critic can look at a picture with the same model and nothing more is downloaded; a text-only
    /// checkpoint falls back to the text loader. The phone loads the text model only, as it always has: the vision
    /// tower would sit in the memory the answer model and search already fill.
    public static func loadContainer() async throws -> ModelContainer {
        #if os(macOS)
        do {
            let c = try await VLMModelFactory.shared.loadContainer(from: #hubDownloader(), using: #huggingFaceTokenizerLoader(), configuration: configuration)
            loadedWithVision = true
            visionProblem = nil
            return c
        } catch {
            visionProblem = error.localizedDescription
            SoakTrace.log("answer model: no vision tower (\(error.localizedDescription)); loading the text model")
        }
        #endif
        let c = try await LLMModelFactory.shared.loadContainer(from: #hubDownloader(), using: #huggingFaceTokenizerLoader(), configuration: configuration)
        loadedWithVision = false
        return c
    }

    /// "mlx-community/gemma-4-e4b-it-4bit" → "gemma-4-e4b-it-4bit".
    public static func shortName(_ id: String) -> String { id.split(separator: "/").last.map(String.init) ?? id }

    // MARK: on this device

    public struct Downloaded: Identifiable, Sendable, Hashable {
        public let id: String
        public let bytes: Int64
        public let modified: Date
        public let directory: URL
    }

    /// Where the loader keeps models.
    public static var cacheDirectory: URL { HubCache.default.cacheDirectory }

    /// Every model repository in the cache that has weights.
    public static func downloaded() -> [Downloaded] {
        let fm = FileManager.default
        guard let repos = try? fm.contentsOfDirectory(at: cacheDirectory, includingPropertiesForKeys: nil) else { return [] }
        var out: [Downloaded] = []
        for repo in repos where repo.lastPathComponent.hasPrefix("models--") {
            let parts = repo.lastPathComponent.dropFirst("models--".count).components(separatedBy: "--")
            guard parts.count >= 2 else { continue }
            let id = parts[0] + "/" + parts.dropFirst().joined(separator: "--")
            let snapshots = repo.appending(path: "snapshots", directoryHint: .isDirectory)
            guard let revisions = try? fm.contentsOfDirectory(at: snapshots, includingPropertiesForKeys: nil) else { continue }
            let snapshot = revisions.first { fm.fileExists(atPath: $0.appending(path: "config.json").path) } ?? revisions.first
            guard let snapshot,
                  let files = try? fm.contentsOfDirectory(at: snapshot, includingPropertiesForKeys: nil),
                  files.contains(where: { $0.pathExtension == "safetensors" }) else { continue }
            let blobs = repo.appending(path: "blobs", directoryHint: .isDirectory)
            let bytes = folderBytes(blobs)
            let modified = (try? repo.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate) ?? .distantPast
            out.append(Downloaded(id: id, bytes: bytes, modified: modified, directory: repo))
        }
        return out.sorted { $0.id < $1.id }
    }

    public static func isDownloaded(_ id: String) -> Bool { downloaded().contains { $0.id == id } }

    /// Removes a model's files. The chosen model falls back to the default if it is the one removed.
    public static func delete(_ id: String) throws {
        guard let model = downloaded().first(where: { $0.id == id }) else { return }
        try FileManager.default.removeItem(at: model.directory)
        if chosenId == id { choose(defaultId) }
    }

    private static func folderBytes(_ url: URL) -> Int64 {
        guard let e = FileManager.default.enumerator(at: url, includingPropertiesForKeys: [.fileSizeKey, .isRegularFileKey]) else { return 0 }
        var total: Int64 = 0
        for case let f as URL in e {
            guard let v = try? f.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey]), v.isRegularFile == true else { continue }
            total += Int64(v.fileSize ?? 0)
        }
        return total
    }

    // MARK: downloading

    /// Fetches the files the loader needs (weights, configs, chat template) into the cache. `progress` is 0…1.
    public static func download(_ id: String, progress: @escaping @Sendable (Double) -> Void) async throws {
        guard let repo = Repo.ID(rawValue: id) else { throw LabError.model("'\(id)' is not a model id (owner/name)") }
        _ = try await HubClient().downloadSnapshot(of: repo, matching: ["*.safetensors", "*.json", "*.jinja", "*.txt", "*.model"]) { p in
            progress(p.fractionCompleted)
        }
    }

    // MARK: finding models

    public struct Found: Identifiable, Sendable, Hashable {
        public let id: String
        public let author: String
        public let downloads: Int
        public let likes: Int
        /// Weight bytes worked out from the safetensors manifest; nil when the Hub does not report one.
        public let bytes: Int64?
        public let updated: Date?
        public let isMLX: Bool
        public let gated: Bool
    }

    /// MLX models on Hugging Face matching `text` (empty: the trending MLX models), most downloaded first.
    public static func search(_ text: String, limit: Int = 40) async throws -> [Found] {
        let q = text.trimmingCharacters(in: .whitespaces)
        let page = try await HubClient().listModels(
            search: q.isEmpty ? nil : q, filter: "mlx", sort: q.isEmpty ? "trendingScore" : "downloads",
            direction: .descending, limit: limit,
            expand: CommaSeparatedList([.known(.downloads), .known(.likes), .known(.lastModified), .known(.safetensors),
                                        .known(.tags), .known(.gated), .known(.author)]))
        return page.items.map { m in
            Found(id: m.id.rawValue, author: m.author ?? m.id.namespace, downloads: m.downloads ?? 0, likes: m.likes ?? 0,
                  bytes: weightBytes(m.safetensors), updated: m.lastModified, isMLX: m.tags?.contains("mlx") ?? true,
                  gated: m.gated.map { $0 != .notGated } ?? false)
        }
    }

    /// safetensors: { "parameters": { "U32": n, "BF16": m, … }, "total": t } → bytes on disk.
    static func weightBytes(_ safetensors: [String: Value]?) -> Int64? {
        guard let safetensors, case .object(let params)? = safetensors["parameters"] else { return nil }
        var bytes = 0.0
        for (dtype, count) in params {
            let n: Double
            switch count { case .int(let i): n = Double(i); case .double(let d): n = d; default: continue }
            let per: Double
            switch dtype.uppercased() {
            case "F64", "I64", "U64": per = 8
            case "F32", "I32", "U32": per = 4
            case "F16", "BF16", "I16", "U16": per = 2
            default: per = 1
            }
            bytes += n * per
        }
        return bytes > 0 ? Int64(bytes) : nil
    }

    // MARK: will it fit?

    public struct Budget: Sendable {
        /// Memory in the machine.
        public let physicalGB: Double
        /// What the GPU may hold at once (Metal's recommended working set); the unified memory a model competes for.
        public let workingSetGB: Double
        /// This app's footprint right now.
        public let inUseGB: Double
    }

    public static func budget() -> Budget {
        let physical = Double(ProcessInfo.processInfo.physicalMemory) / 1_073_741_824
        var working = physical * 0.75
        #if canImport(Metal)
        if let device = MTLCreateSystemDefaultDevice() { working = Double(device.recommendedMaxWorkingSetSize) / 1_073_741_824 }
        #endif
        return Budget(physicalGB: physical, workingSetGB: working, inUseGB: MemoryProbe.footprintMB() / 1024)
    }

    public enum Fit: Sendable { case fits, tight, tooBig }

    /// Weights plus room for the context (about a fifth again, 2 GB at least) against the working set, with the search
    /// model and the app's own use already in it.
    public static func fit(bytes: Int64, budget: Budget = budget()) -> Fit {
        let weightsGB = Double(bytes) / 1_073_741_824
        let need = weightsGB + max(2, weightsGB * 0.2)
        let room = budget.workingSetGB - 1.5                   // search model, reranker and the app itself
        if need <= room * 0.8 { return .fits }
        if need <= room { return .tight }
        return .tooBig
    }
}
