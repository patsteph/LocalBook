import Foundation

/// The ingestion diff of the Phase 3 gate: the same documents read and cut by the old app and by this Mac, compared.
///
/// `scripts/ingestion_diff_export.py` writes `ingestion-fixture.json` into a folder of documents with what the
/// Python readers and chunker made of each. This type holds that fixture, compares one file's Swift result against
/// it, and turns the results into a report with a summary and a Markdown page a person can read.
///
/// Reading the fixture, extracting the text and chunking happen in the Engines (`IngestionDiffRunner`); the compare
/// lives here, in pure Foundation, so Swift Testing can prove it.
public enum IngestionDiff {
    public static let fixtureName = "ingestion-fixture.json"
    public static let supportedFormat = "localbook-ingestion-fixture/1"

    public struct Fixture: Codable, Sendable {
        public let format: String
        public let createdAt: String
        public let folder: String
        public let recursive: Bool
        public let python: String
        public let tokenCounter: String
        public let tokenBudget: Int
        public let files: [File]

        public struct File: Codable, Sendable {
            public let name: String
            public let format: String
            public let bytes: Int
            public let sha256: String
            public let text: String?
            public let chunks: [String]?
            public let error: String?
        }

        public static func load(_ data: Data) throws -> Fixture {
            let d = JSONDecoder(); d.keyDecodingStrategy = .convertFromSnakeCase
            let f = try d.decode(Fixture.self, from: data)
            guard f.format == supportedFormat else { throw Problem.unsupported(f.format) }
            return f
        }
    }

    public enum Problem: Error, LocalizedError {
        case unsupported(String)
        public var errorDescription: String? {
            switch self { case .unsupported(let f): return "This is not an ingestion fixture this app reads (\(f))." }
        }
    }

    /// What this Mac made of one file, or why it could not.
    public enum Here: Sendable {
        case read(text: String, chunks: [String])
        case failed(String)
        case missing            // the file is not beside the fixture
        case unreadable         // a kind this Mac has no reader for
    }

    public enum Verdict: String, Codable, Sendable {
        case identical          // same text (whitespace aside) and the very same chunks
        case sameText           // same text, cut differently: a chunker difference
        case close              // most lines agree (≥ 95 %), the rest is the reader's
        case differs            // the readers disagree
        case failedHere         // this Mac could not read it
        case failedThere        // the old app could not read it
        case missing            // the file was not beside the fixture
        case unreadable         // this Mac has no reader for the kind
    }

    public struct FileResult: Codable, Sendable, Identifiable {
        public var id: String { name }
        public let name: String
        public let format: String
        public let verdict: Verdict
        /// Share of lines the two texts have in common, 0…1 (1 when both are empty).
        public let textScore: Double
        public let pythonChunks: Int
        public let swiftChunks: Int
        /// Python chunks found, byte for byte, among this Mac's chunks.
        public let chunksMatched: Int
        public let pythonCharacters: Int
        public let swiftCharacters: Int
        /// The first line only one side has, each way, for the report.
        public let onlyPython: String?
        public let onlySwift: String?
        public let note: String?
    }

    public struct Summary: Codable, Sendable {
        public var files = 0
        public var identical = 0
        public var sameText = 0
        public var close = 0
        public var differs = 0
        public var failed = 0
        public var missing = 0
        public var unreadable = 0
        /// Files whose chunks match exactly, over the files both sides read.
        public var identicalRate: Double {
            let compared = identical + sameText + close + differs
            return compared == 0 ? .nan : Double(identical) / Double(compared)
        }
        public init() {}
    }

    public struct Report: Codable, Sendable {
        public var format = "localbook-ingestion-diff/1"
        public let createdAt: String
        public let fixtureFolder: String
        public let fixtureMade: String
        public let pythonTokenCounter: String
        public let swiftTokenCounter: String
        public let device: String
        public let summary: Summary
        public let files: [FileResult]

        enum CodingKeys: String, CodingKey { case format, createdAt, fixtureFolder, fixtureMade, pythonTokenCounter, swiftTokenCounter, device, summary, files }

        public init(fixture: Fixture, files: [FileResult], swiftTokenCounter: String, device: String) {
            createdAt = ISO8601DateFormatter().string(from: Date())
            fixtureFolder = fixture.folder
            fixtureMade = fixture.createdAt
            pythonTokenCounter = fixture.tokenCounter
            self.swiftTokenCounter = swiftTokenCounter
            self.device = device
            var s = Summary()
            s.files = files.count
            for f in files {
                switch f.verdict {
                case .identical: s.identical += 1
                case .sameText: s.sameText += 1
                case .close: s.close += 1
                case .differs: s.differs += 1
                case .failedHere, .failedThere: s.failed += 1
                case .missing: s.missing += 1
                case .unreadable: s.unreadable += 1
                }
            }
            summary = s
            // Worst first: what needs explaining is at the top.
            self.files = files.sorted { Self.rank($0) < Self.rank($1) }
        }

        private static func rank(_ f: FileResult) -> (Int, Double, String) {
            let order: [Verdict: Int] = [.differs: 0, .failedHere: 1, .close: 2, .sameText: 3, .missing: 4, .failedThere: 5, .unreadable: 6, .identical: 7]
            return (order[f.verdict] ?? 8, f.textScore, f.name)
        }

        public func json() throws -> Data {
            let e = JSONEncoder(); e.outputFormatting = [.prettyPrinted, .sortedKeys]; e.keyEncodingStrategy = .convertToSnakeCase
            return try e.encode(self)
        }

        /// The page a person reads: the summary, then every file that is not identical with the first lines only
        /// one side has, then the identical ones in a line each.
        public func markdown() -> String {
            var out = ["# Ingestion diff: \(fixtureFolder)", ""]
            out.append("Fixture made \(fixtureMade) by the old app (token counter: \(pythonTokenCounter)); compared \(createdAt) on \(device) (token counter: \(swiftTokenCounter)).")
            out.append("")
            out.append("| Files | Identical chunks | Same text, cut differently | Close (≥ 95 % lines) | Differ | Failed | Missing | No reader here |")
            out.append("|---|---|---|---|---|---|---|---|")
            out.append("| \(summary.files) | \(summary.identical) | \(summary.sameText) | \(summary.close) | \(summary.differs) | \(summary.failed) | \(summary.missing) | \(summary.unreadable) |")
            out.append("")
            if !summary.identicalRate.isNaN {
                out.append("Identical on \(Int((summary.identicalRate * 100).rounded())) % of the files both sides read. The gate wants every difference below explained.")
                out.append("")
            }
            let worth = files.filter { $0.verdict != .identical }
            if !worth.isEmpty {
                out.append("## Differences")
                out.append("")
                for f in worth {
                    out.append("### \(f.name) · \(f.format) · \(f.verdict.rawValue)")
                    out.append("")
                    out.append("- Lines in common: \(Int((f.textScore * 100).rounded())) %; characters \(f.pythonCharacters) there, \(f.swiftCharacters) here")
                    out.append("- Chunks: \(f.pythonChunks) there, \(f.swiftChunks) here, \(f.chunksMatched) the same")
                    if let n = f.note { out.append("- \(n)") }
                    if let p = f.onlyPython { out.append("- Only the old app has: `\(p)`") }
                    if let s = f.onlySwift { out.append("- Only this Mac has: `\(s)`") }
                    out.append("")
                }
            }
            let same = files.filter { $0.verdict == .identical }
            if !same.isEmpty {
                out.append("## Identical")
                out.append("")
                for f in same { out.append("- \(f.name) · \(f.format) · \(f.pythonChunks) chunks") }
                out.append("")
            }
            return out.joined(separator: "\n")
        }
    }

    // MARK: Compare

    /// One file's result from the fixture's record and what this Mac made.
    public static func compare(_ file: Fixture.File, here: Here) -> FileResult {
        func result(_ verdict: Verdict, textScore: Double = 0, swiftText: String = "", swiftChunks: [String] = [], matched: Int = 0,
                    onlyPython: String? = nil, onlySwift: String? = nil, note: String? = nil) -> FileResult {
            FileResult(name: file.name, format: file.format, verdict: verdict, textScore: textScore,
                       pythonChunks: file.chunks?.count ?? 0, swiftChunks: swiftChunks.count, chunksMatched: matched,
                       pythonCharacters: file.text.map(PyText.len) ?? 0, swiftCharacters: PyText.len(swiftText),
                       onlyPython: onlyPython, onlySwift: onlySwift, note: note)
        }
        if let error = file.error { return result(.failedThere, note: "The old app failed: \(error)") }
        guard let pythonText = file.text, let pythonChunks = file.chunks else { return result(.failedThere, note: "The fixture has no text for this file.") }
        switch here {
        case .missing: return result(.missing, note: "The file is not beside the fixture.")
        case .unreadable: return result(.unreadable, note: "This Mac has no reader for .\(file.format) yet.")
        case .failed(let why): return result(.failedHere, note: "This Mac failed: \(why)")
        case .read(let swiftText, let swiftChunks):
            let p = lines(pythonText), s = lines(swiftText)
            let score = similarity(p, s)
            let swiftSet = Set(swiftChunks)
            let matched = pythonChunks.filter { swiftSet.contains($0) }.count
            let only = firstOnly(p, s)
            let verdict: Verdict
            if pythonChunks == swiftChunks { verdict = .identical }
            else if p == s { verdict = .sameText }
            else if score >= 0.95 { verdict = .close }
            else { verdict = .differs }
            return result(verdict, textScore: score, swiftText: swiftText, swiftChunks: swiftChunks, matched: matched,
                          onlyPython: only.python, onlySwift: only.swift)
        }
    }

    /// Non-empty lines with runs of whitespace folded, so that spacing never counts as a difference.
    static func lines(_ text: String) -> [String] {
        text.split(omittingEmptySubsequences: true, whereSeparator: \.isNewline).compactMap { line in
            let folded = line.split(whereSeparator: \.isWhitespace).joined(separator: " ")
            return folded.isEmpty ? nil : folded
        }
    }

    /// `2 × common ÷ (a + b)` over lines as bags: what `difflib.SequenceMatcher.ratio()` reports when order is not in
    /// question, and cheap enough for a long document.
    static func similarity(_ a: [String], _ b: [String]) -> Double {
        if a.isEmpty && b.isEmpty { return 1 }
        var counts: [String: Int] = [:]
        for line in a { counts[line, default: 0] += 1 }
        var common = 0
        for line in b where (counts[line] ?? 0) > 0 { counts[line]! -= 1; common += 1 }
        return 2 * Double(common) / Double(a.count + b.count)
    }

    static func firstOnly(_ a: [String], _ b: [String]) -> (python: String?, swift: String?) {
        let setA = Set(a), setB = Set(b)
        func clip(_ s: String?) -> String? { s.map { $0.count > 120 ? String($0.prefix(117)) + "…" : $0 } }
        return (clip(a.first { !setB.contains($0) }), clip(b.first { !setA.contains($0) }))
    }
}
