import Foundation

/// The Mac's half of the parity test, written by `backend/scripts/export_cortex_parity.py`.
/// Keys are snake_case on disk; decode with `ParityFixture.decoder`.
public struct ParityFixture: Codable, Sendable {
    public let format: String
    public let createdAt: String
    public let mac: MacSide
    public let chunks: [Item]
    public let queries: [Item]

    public struct MacSide: Codable, Sendable {
        public let modelId: String
        public let profile: CortexProfile
        public let maxLength: Int
        public let hasBos: Bool
        public let hasEos: Bool
        public let tokenizerSource: String
        public let secondsToEmbed: Double
    }

    public struct Item: Codable, Sendable, Identifiable {
        public let id: String
        public let formattedText: String
        public let tokenIds: [Int]
        public let tokenCountFull: Int
        public let vector: [Float]
    }

    public static let supportedFormat = "localbook-parity-fixture/1"

    public static var decoder: JSONDecoder {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }

    public static func load(from url: URL) throws -> ParityFixture {
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
public struct CortexProfile: Codable, Sendable {
    public let format: String
    public let modelId: String
    public let pooling: String
    public let queryTemplate: String
    public let documentTemplate: String
    public let dims: Int
    public let minDtype: String
    public let maxTokensMac: Int
    public let maxTokensPhone: Int
    public let normalize: String
    public let bosTokenId: Int?
    public let eosTokenId: Int?
}

