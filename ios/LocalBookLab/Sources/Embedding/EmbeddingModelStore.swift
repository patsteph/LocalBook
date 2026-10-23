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

        // Already downloaded and compiled: skip the Hub check (about a second at every launch).
        let knownTable = root.appending(path: "hub/models/\(repo)/\(tableName)")
        if FileManager.default.fileExists(atPath: compiledURL.path), FileManager.default.fileExists(atPath: knownTable.path) {
            try? FileManager.default.removeItem(at: root.appending(path: "hub/models/\(repo)/\(packageName)"))   // an earlier build kept this copy
            return Location(compiledModel: compiledURL, tokenTable: knownTable)
        }
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
            try? FileManager.default.removeItem(at: package)         // the compiled copy is all that is used: frees ~270 MB
        }
        return Location(compiledModel: compiledURL, tokenTable: table)
    }

    // MARK: picture search (the image encoder, fetched only when the user asks for it)

    static let visionPackage = "EmbeddingGemma2Vision.mlpackage"
    static let positionTable = "position_embeddings.f16"
    static let visionDownloadSize = "about 295 MB"

    private static var compiledVisionURL: URL { root.appending(path: "EmbeddingGemma2Vision.mlmodelc", directoryHint: .isDirectory) }
    private static var visionTableURL: URL { root.appending(path: positionTable) }

    static var visionInstalled: Bool {
        FileManager.default.fileExists(atPath: compiledVisionURL.path) && FileManager.default.fileExists(atPath: visionTableURL.path)
    }

    struct VisionLocation: Sendable { let compiledModel: URL; let positions: URL }

    static func prepareVision(progress: @escaping @Sendable (Double) -> Void) async throws -> VisionLocation {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        if visionInstalled { return VisionLocation(compiledModel: compiledVisionURL, positions: visionTableURL) }
        let hub = HubApi(downloadBase: root.appending(path: "hub", directoryHint: .isDirectory))
        let snapshot = try await hub.snapshot(from: repo, matching: ["\(visionPackage)/*", positionTable, "config.json"]) { p in
            progress(p.fractionCompleted)
        }
        let package = snapshot.appending(path: visionPackage, directoryHint: .isDirectory)
        let table = snapshot.appending(path: positionTable)
        guard FileManager.default.fileExists(atPath: package.path), FileManager.default.fileExists(atPath: table.path) else {
            throw LabError.model("the image model was not downloaded")
        }
        let compiled = try await MLModel.compileModel(at: package)
        try? FileManager.default.removeItem(at: compiledVisionURL)
        try FileManager.default.moveItem(at: compiled, to: compiledVisionURL)
        try? FileManager.default.removeItem(at: visionTableURL)
        try FileManager.default.copyItem(at: table, to: visionTableURL)
        try? FileManager.default.removeItem(at: package)                  // only the compiled copy is used
        return VisionLocation(compiledModel: compiledVisionURL, positions: visionTableURL)
    }

    static func removeVision() {
        try? FileManager.default.removeItem(at: compiledVisionURL)
        try? FileManager.default.removeItem(at: visionTableURL)
        let hubPackage = root.appending(path: "hub", directoryHint: .isDirectory)
        try? FileManager.default.removeItem(at: hubPackage.appending(path: "models/\(repo)/\(visionPackage)"))
    }

    static func reset() throws {
        if FileManager.default.fileExists(atPath: root.path) {
            try FileManager.default.removeItem(at: root)
        }
    }
}
