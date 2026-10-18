import CoreML
import Foundation
import Hub

/// Downloads and compiles the Core ML build of EmbeddingGemma 2 once, then keeps it in
/// Application Support. Text model only (~530 MB): the audio and vision encoders are not
/// needed for test 1.
enum EmbeddingModelStore {
    static let repo = "FluidInference/embeddinggemma-2-coreml"
    static let packageName = "EmbeddingGemma2Text.mlpackage"
    static let tableName = "embeddings.bf16"

    struct Location: Sendable {
        let compiledModel: URL   // .mlmodelc
        let tokenTable: URL      // embeddings.bf16, 262,144 × 512 bfloat16
    }

    private static var root: URL {
        URL.applicationSupportDirectory.appending(path: "LocalBookLab", directoryHint: .isDirectory)
    }

    private static var compiledURL: URL {
        root.appending(path: "EmbeddingGemma2Text.mlmodelc", directoryHint: .isDirectory)
    }

    /// Download (if needed) and compile (if needed). `progress` receives 0…1 for the download.
    static func prepare(progress: @escaping @Sendable (Double) -> Void) async throws -> Location {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)

        let hub = HubApi(downloadBase: root.appending(path: "hub", directoryHint: .isDirectory))
        let snapshot = try await hub.snapshot(
            from: repo,
            matching: ["\(packageName)/*", tableName, "config.json"]
        ) { p in
            progress(p.fractionCompleted)
        }

        let table = snapshot.appending(path: tableName)
        guard FileManager.default.fileExists(atPath: table.path) else {
            throw LabError.model("\(tableName) was not downloaded")
        }

        if !FileManager.default.fileExists(atPath: compiledURL.path) {
            let package = snapshot.appending(path: packageName, directoryHint: .isDirectory)
            guard FileManager.default.fileExists(atPath: package.path) else {
                throw LabError.model("\(packageName) was not downloaded")
            }
            let compiled = try await MLModel.compileModel(at: package)
            try? FileManager.default.removeItem(at: compiledURL)
            try FileManager.default.moveItem(at: compiled, to: compiledURL)
        }
        return Location(compiledModel: compiledURL, tokenTable: table)
    }

    static func reset() throws {
        if FileManager.default.fileExists(atPath: root.path) {
            try FileManager.default.removeItem(at: root)
        }
    }
}
