import Foundation

/// PowerPoint 97-2003 (.ppt): the slides' text in the shape `PPTXReader` gives a .pptx (`=== Slide N ===`, each
/// text run's paragraphs on their own lines, `Speaker Notes:`), read from the record tree of the
/// "PowerPoint Document" stream: placeholder text from the document's slide list, the rest from each slide's
/// drawing, notes from the notes containers by the slide they point at. The old app only fished sentences out of
/// the raw bytes here, so this reads more than it did.
public enum PPTLegacyReader {
    public enum Failure: LocalizedError {
        case noDocument, noText
        public var errorDescription: String? {
            switch self {
            case .noDocument: return "No presentation was found in the file"
            case .noText: return "No readable text found in PPT file"
            }
        }
    }

    struct Record {
        let version: UInt16, instance: UInt16, type: UInt16
        let start: Int, length: Int                                        // of the data
        var isContainer: Bool { version == 0xF }
        var end: Int { start + length }
    }

    static func records(_ d: Data, from: Int, to: Int) -> [Record] {
        var out: [Record] = [], off = from
        while off + 8 <= to {
            let vi = d.u16(off), type = d.u16(off + 2), len = Int(d.u32(off + 4))
            guard len >= 0, off + 8 + len <= to else { break }
            out.append(Record(version: vi & 0xF, instance: vi >> 4, type: type, start: off + 8, length: len))
            off += 8 + len
        }
        return out
    }

    // Record types.
    static let document: UInt16 = 0x03E8, slide: UInt16 = 0x03EE, notes: UInt16 = 0x03F0, notesAtom: UInt16 = 0x03F1
    static let slidePersistAtom: UInt16 = 0x03F3, slideListWithText: UInt16 = 0x0FF0
    static let textChars: UInt16 = 0x0FA0, textBytes: UInt16 = 0x0FA8

    public static func read(_ data: Data) throws -> String {
        let ole = try OLE2(data)
        guard let d = ole.stream("PowerPoint Document") else { throw Failure.noDocument }
        let top = records(d, from: 0, to: d.count)

        // 1. The document's slide list: text per slide, groups opened by SlidePersistAtom (which carries the slide id).
        var slideIds: [UInt32] = []
        var listText: [[String]] = []
        if let doc = top.first(where: { $0.type == document && $0.isContainer }) {
            for list in records(d, from: doc.start, to: doc.end) where list.type == slideListWithText && list.isContainer && list.instance == 0 {
                for r in records(d, from: list.start, to: list.end) {
                    switch r.type {
                    case slidePersistAtom:
                        slideIds.append(r.length >= 16 ? d.u32(r.start + 12) : 0); listText.append([])
                    case textChars, textBytes:
                        if let t = text(d, r), !listText.isEmpty { listText[listText.count - 1].append(t) }
                    default: break
                    }
                }
            }
        }

        // 2. Each slide container's drawing text, in file order (the same order as the list).
        let drawingText: [[String]] = top.filter { $0.type == slide && $0.isContainer }.map { allText(d, in: $0) }

        // 3. Notes containers, each pointing at its slide.
        var notesFor: [Int: [String]] = [:]        // slide index → notes lines
        var looseNotes: [[String]] = []
        for n in top where n.type == notes && n.isContainer {
            let lines = allText(d, in: n).flatMap { $0.split(separator: "\n").map(String.init) }
                .filter { Int($0.trimmingCharacters(in: .whitespaces)) == nil && !$0.trimmingCharacters(in: .whitespaces).isEmpty }
            guard !lines.isEmpty else { continue }
            let atom = records(d, from: n.start, to: n.end).first { $0.type == notesAtom }
            if let atom, atom.length >= 4, let idx = slideIds.firstIndex(of: d.u32(atom.start)) { notesFor[idx, default: []] += lines }
            else { looseNotes.append(lines) }
        }

        let count = max(listText.count, drawingText.count)
        guard count > 0 else { throw Failure.noText }
        var parts: [String] = []
        var any = false
        for i in 0..<count {
            var slideLines = ["=== Slide \(i + 1) ==="]
            var seen = Set<String>()
            for t in (i < listText.count ? listText[i] : []) + (i < drawingText.count ? drawingText[i] : []) {
                let clean = t.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !clean.isEmpty, !seen.contains(clean) else { continue }
                seen.insert(clean); slideLines.append(clean); any = true
            }
            var noteLines = notesFor[i] ?? []
            if notesFor[i] == nil, i < looseNotes.count, slideIds.isEmpty { noteLines = looseNotes[i] }
            if !noteLines.isEmpty { slideLines.append("Speaker Notes: " + noteLines.joined(separator: "\n")); any = true }
            parts.append(contentsOf: slideLines)
            parts.append("")
        }
        guard any else { throw Failure.noText }
        return parts.joined(separator: "\n")
    }

    /// Every text atom inside `container`, depth first.
    static func allText(_ d: Data, in container: Record) -> [String] {
        var out: [String] = []
        func walk(_ from: Int, _ to: Int) {
            for r in records(d, from: from, to: to) {
                if r.isContainer { walk(r.start, r.end) }
                else if r.type == textChars || r.type == textBytes, let t = text(d, r) { out.append(t) }
            }
        }
        walk(container.start, container.end)
        return out
    }

    /// A text atom's paragraphs, one per line.
    static func text(_ d: Data, _ r: Record) -> String? {
        let bytes = d.sub(r.start, r.length)
        let raw = r.type == textChars ? String(data: bytes, encoding: .utf16LittleEndian) : String(data: bytes, encoding: .windowsCP1252)
        guard let raw else { return nil }
        let lines = raw.replacingOccurrences(of: "\r", with: "\n").replacingOccurrences(of: "\u{0B}", with: "\n")
            .split(separator: "\n", omittingEmptySubsequences: false)
            .map { $0.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: "\u{0}", with: "") }
            .filter { !$0.isEmpty }
        return lines.isEmpty ? nil : lines.joined(separator: "\n")
    }
}
