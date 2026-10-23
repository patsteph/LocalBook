import Foundation

/// swift-transformers falls back to raw bytes for the "▁" that stands for a space when the next
/// character has a canonical equivalent (a combining mark, U+2126 OHM SIGN → Ω, …). The Mac's tokenizer
/// emits the single "▁" token (236743). A real space is always normalised to "▁", so these byte tokens
/// only ever come from that bug: <0x20> (270), or the UTF-8 bytes of "▁" itself, <0xE2><0x96><0x81>
/// (464, 388, 367).
enum TokenRepair {
    static func spaceFallback(_ ids: [Int]) -> [Int] {
        var out: [Int] = []
        out.reserveCapacity(ids.count)
        var i = 0
        while i < ids.count {
            if ids[i] == 270 {
                out.append(236743); i += 1
            } else if i + 2 < ids.count, ids[i] == 464, ids[i + 1] == 388, ids[i + 2] == 367 {
                out.append(236743); i += 3
            } else {
                out.append(ids[i]); i += 1
            }
        }
        return out
    }
}
