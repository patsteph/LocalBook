import Foundation
import LocalBookCore
import Tokenizers

/// Mode B of the parity test: tokenise every string in the fixture with this device's tokenizer (the same tokenizer
/// files the Mac loads), so the report can prove new sources will be tokenised identically. Shared by the phone's
/// parity screen and the Mac's Settings › Diagnostics, so neither app links the tokenizer library itself.
public enum ParityTokenizer {
    public static func ids(for fixture: ParityFixture) async throws -> [String: [Int]] {
        let tokenizer = try await AutoTokenizer.from(pretrained: fixture.mac.tokenizerSource)
        var ids: [String: [Int]] = [:]
        for item in fixture.chunks + fixture.queries {
            ids[item.id] = TokenRepair.spaceFallback(tokenizer.encode(text: item.formattedText))
        }
        return ids
    }
}
