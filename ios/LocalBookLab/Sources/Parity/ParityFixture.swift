import Foundation

/// The Mac's half of the parity test, written by `backend/scripts/export_cortex_parity.py`.
/// Keys are snake_case on disk; decode with `ParityFixture.decoder`.
struct ParityFixture: Codable, Sendable {
    let format: String
    let createdAt: String
    let mac: MacSide
    let chunks: [Item]
    let queries: [Item]

    struct MacSide: Codable, Sendable {
        let modelId: String
        let profile: CortexProfile
        let maxLength: Int
        let hasBos: Bool
        let hasEos: Bool
        let tokenizerSource: String
        let secondsToEmbed: Double
    }

    struct Item: Codable, Sendable, Identifiable {
        let id: String
        let formattedText: String
        let tokenIds: [Int]
        let tokenCountFull: Int
        let vector: [Float]
    }

    static let supportedFormat = "localbook-parity-fixture/1"

    static var decoder: JSONDecoder {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }

    static func load(from url: URL) throws -> ParityFixture {
        let scoped = url.startAccessingSecurityScopedResource()
        defer { if scoped { url.stopAccessingSecurityScopedResource() } }
        let data = try Data(contentsOf: url)
        let fixture = try decoder.decode(ParityFixture.self, from: data)
        guard fixture.format == supportedFormat else {
            throw LabError.fixture("Unsupported fixture format \(fixture.format)")
        }
        return fixture
    }
}

/// The embedder contract every device must honour. Written by the Mac as
/// `cortex_profile.json`; it becomes the key every synced vector carries.
struct CortexProfile: Codable, Sendable {
    let format: String
    let modelId: String
    let pooling: String
    let queryTemplate: String
    let documentTemplate: String
    let dims: Int
    let minDtype: String
    let maxTokensMac: Int
    let maxTokensPhone: Int
    let normalize: String
    let bosTokenId: Int?
    let eosTokenId: Int?
}

enum LabError: LocalizedError {
    case fixture(String)
    case model(String)
    case tooLong(Int)
    case background(String)

    var errorDescription: String? {
        switch self {
        case .fixture(let m): "Fixture: \(m)"
        case .model(let m): "Model: \(m)"
        case .tooLong(let n): "Input is \(n) tokens; the phone model takes at most 512"
        case .background(let m): "Background task: \(m)"
        }
    }
}
