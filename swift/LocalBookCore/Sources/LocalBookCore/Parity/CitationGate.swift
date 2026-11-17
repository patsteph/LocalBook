import Foundation

/// The Phase 3 citation gate: the old app's citations for real questions (`scripts/citation_gate_export.py`) against
/// what this app cites for the same questions over its migrated copy. A citation is a (source id, chunk index) pair,
/// the same on both sides because the import keeps the old ids. This is the pure half: the fixture, the comparison
/// of one question, and the report with its bar.
public enum CitationGate {
    public static let bar = 0.95

    public struct Citation: Codable, Sendable, Hashable {
        public let sourceId: String
        public let chunkIndex: Int
        public var filename: String? = nil
        public var confidence: Double? = nil
        public init(sourceId: String, chunkIndex: Int, filename: String? = nil, confidence: Double? = nil) {
            self.sourceId = sourceId; self.chunkIndex = chunkIndex; self.filename = filename; self.confidence = confidence
        }
        public var key: String { "\(sourceId)/\(chunkIndex)" }
    }

    public struct Question: Codable, Sendable, Identifiable {
        public var id: String { "\(notebookId)|\(question)" }
        public let notebookId: String
        public let notebookTitle: String
        public let question: String
        public let citations: [Citation]
        public var lowConfidence: Bool? = nil
        public var candidates: Int? = nil
    }

    public struct Fixture: Codable, Sendable {
        public struct MacSide: Codable, Sendable {
            public let device: String
            public let embedder: String
            public let reranker: String
            public let analysis: String
        }
        public let format: String
        public let createdAt: String
        public let mac: MacSide
        public let questions: [Question]

        public static let supportedFormat = "localbook-citation-fixture/1"

        public static func load(_ data: Data) throws -> Fixture {
            let d = JSONDecoder(); d.keyDecodingStrategy = .convertFromSnakeCase
            let f = try d.decode(Fixture.self, from: data)
            guard f.format == supportedFormat else { throw LibraryError.badResponse("not a citation fixture (\(f.format))") }
            return f
        }
    }

    // MARK: one question

    public enum Verdict: String, Codable, Sendable, CaseIterable {
        case identical          // the same citations in the same order (both empty counts)
        case sameSet            // the same citations, ordered differently
        case overlap            // at least one citation shared
        case different          // nothing shared
        case notebookMissing    // this Mac has no such notebook
        case failed             // retrieval threw here
    }

    public struct Result: Codable, Sendable, Identifiable {
        public var id: String { "\(notebookId)|\(question)" }
        public let notebookId: String
        public let notebookTitle: String
        public let question: String
        public let verdict: Verdict
        public let theirs: [Citation]
        public let ours: [Citation]
        public let shared: Int
        public var problem: String? = nil
        public var seconds: Double = 0

        public init(_ q: Question, ours: [Citation], seconds: Double = 0) {
            notebookId = q.notebookId; notebookTitle = q.notebookTitle; question = q.question
            theirs = q.citations; self.ours = ours; self.seconds = seconds
            let a = q.citations.map(\.key), b = ours.map(\.key)
            shared = Set(a).intersection(b).count
            if a == b { verdict = .identical }
            else if Set(a) == Set(b) { verdict = .sameSet }
            else if shared > 0 { verdict = .overlap }
            else { verdict = .different }
        }

        public init(_ q: Question, verdict: Verdict, problem: String?) {
            notebookId = q.notebookId; notebookTitle = q.notebookTitle; question = q.question
            theirs = q.citations; ours = []; shared = 0; self.verdict = verdict; self.problem = problem
        }
    }

    // MARK: the report

    public struct Summary: Codable, Sendable {
        public var questions = 0, identical = 0, sameSet = 0, overlap = 0, different = 0, missing = 0, failed = 0
        /// Questions cited the same (in any order) over the questions both sides answered.
        public var compared: Int { identical + sameSet + overlap + different }
        public var sameRate: Double { compared == 0 ? .nan : Double(identical + sameSet) / Double(compared) }
        public var exactRate: Double { compared == 0 ? .nan : Double(identical) / Double(compared) }
        public var passes: Bool { !sameRate.isNaN && sameRate >= CitationGate.bar }
        public init() {}
    }

    public struct Report: Codable, Sendable {
        public var format = "localbook-citation-gate/1"
        public let createdAt: String
        public let fixtureMade: String
        public let theirMac: Fixture.MacSide
        public let device: String
        public let recipe: String
        public let summary: Summary
        public let results: [Result]

        public init(fixture: Fixture, results: [Result], device: String, recipe: String) {
            createdAt = ISO8601DateFormatter().string(from: Date())
            fixtureMade = fixture.createdAt; theirMac = fixture.mac; self.device = device; self.recipe = recipe
            var s = Summary(); s.questions = results.count
            for r in results {
                switch r.verdict {
                case .identical: s.identical += 1
                case .sameSet: s.sameSet += 1
                case .overlap: s.overlap += 1
                case .different: s.different += 1
                case .notebookMissing: s.missing += 1
                case .failed: s.failed += 1
                }
            }
            summary = s
            let order: [Verdict: Int] = [.different: 0, .failed: 1, .overlap: 2, .notebookMissing: 3, .sameSet: 4, .identical: 5]
            self.results = results.sorted { (order[$0.verdict] ?? 9, $0.notebookTitle, $0.question) < (order[$1.verdict] ?? 9, $1.notebookTitle, $1.question) }
        }

        public func json() throws -> Data {
            let e = JSONEncoder(); e.outputFormatting = [.prettyPrinted, .sortedKeys]; e.keyEncodingStrategy = .convertToSnakeCase
            return try e.encode(self)
        }

        static func pct(_ v: Double) -> String { v.isNaN ? "–" : "\(Int((v * 100).rounded())) %" }

        /// The page a person reads: the verdict against the bar, the counts, then every question that was not cited
        /// the same with both sides' citations, then the rest in a line each.
        public func markdown() -> String {
            var out = ["# Citation gate", ""]
            out.append("Fixture made \(fixtureMade) on \(theirMac.device) (embedder \(theirMac.embedder), reranker \(theirMac.reranker), query analysis: \(theirMac.analysis)); compared \(createdAt) on \(device) (\(recipe)).")
            out.append("")
            out.append(summary.passes ? "**PASS**: cited the same on \(Self.pct(summary.sameRate)) of the questions both sides answered (bar \(Int(CitationGate.bar * 100)) %); \(Self.pct(summary.exactRate)) in the same order."
                                      : "**NOT YET**: cited the same on \(Self.pct(summary.sameRate)) of the questions both sides answered (bar \(Int(CitationGate.bar * 100)) %); \(Self.pct(summary.exactRate)) in the same order. Every row below the table needs an explanation.")
            out.append("")
            out.append("| Questions | Identical | Same set, other order | Overlap | Different | Notebook missing | Failed here |")
            out.append("|---|---|---|---|---|---|---|")
            out.append("| \(summary.questions) | \(summary.identical) | \(summary.sameSet) | \(summary.overlap) | \(summary.different) | \(summary.missing) | \(summary.failed) |")
            out.append("")
            func line(_ c: Citation) -> String { "\(c.filename ?? c.sourceId) #\(c.chunkIndex)" + (c.confidence.map { " (\(Int(($0 * 100).rounded())) %)" } ?? "") }
            let worth = results.filter { $0.verdict != .identical && $0.verdict != .sameSet }
            if !worth.isEmpty {
                out.append("## To explain"); out.append("")
                for r in worth {
                    out.append("### \(r.notebookTitle): \(r.question)"); out.append("")
                    out.append("Verdict: **\(r.verdict.rawValue)**" + (r.problem.map { " — \($0)" } ?? "") + (r.shared > 0 ? ", \(r.shared) shared" : ""))
                    out.append("")
                    out.append("| | Old app | This Mac |"); out.append("|---|---|---|")
                    for i in 0..<max(r.theirs.count, r.ours.count) {
                        out.append("| [\(i + 1)] | \(i < r.theirs.count ? line(r.theirs[i]) : "") | \(i < r.ours.count ? line(r.ours[i]) : "") |")
                    }
                    if r.theirs.isEmpty && r.ours.isEmpty { out.append("| | (nothing cited) | (nothing cited) |") }
                    out.append("")
                }
            }
            let same = results.filter { $0.verdict == .identical || $0.verdict == .sameSet }
            if !same.isEmpty {
                out.append("## Cited the same"); out.append("")
                for r in same { out.append("- \(r.notebookTitle): \(r.question) (\(r.theirs.count) citation\(r.theirs.count == 1 ? "" : "s")\(r.verdict == .sameSet ? ", other order" : ""))") }
                out.append("")
            }
            return out.joined(separator: "\n")
        }
    }
}
