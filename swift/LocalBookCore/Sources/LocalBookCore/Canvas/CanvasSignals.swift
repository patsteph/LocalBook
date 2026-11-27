import Foundation

/// Gaps: questions the notebook answered weakly, the map's open loops (the old canvas_gaps).
public enum CanvasGaps {
    static let missMarkers = ["couldn't find", "could not find", "not in the documents", "not in your documents", "no information",
                              "isn't covered", "not covered", "don't have", "do not have", "unable to find", "no relevant",
                              "wasn't able to find", "not available in", "don't have enough relevant", "not enough relevant information",
                              "nothing in this notebook", "the notebook does not", "the sources do not mention", "not mentioned in"]
    public static let lowConfidence = 0.45

    public static func readsAsMiss(_ answer: String) -> Bool {
        let lowered = answer.lowercased()
        return missMarkers.contains { lowered.contains($0) }
    }

    /// Why a turn is a gap, or nil when it was answered.
    public static func reason(for turn: JourneyTurn) -> String? {
        if readsAsMiss(turn.answerPreview) { return "the answer wasn't in your sources" }
        if turn.confidence >= 0, turn.confidence < lowConfidence { return "low-confidence answer" }
        return nil
    }

    public struct Gap: Sendable, Hashable, Identifiable {
        public var id: String { refId }
        public let question: String, reason: String, filenames: [String], refId: String
    }

    public static func find(_ turns: [JourneyTurn], max: Int = 12) -> [Gap] {
        var out: [Gap] = []
        var seen = Set<String>()
        for t in turns where CanvasPopulate.isLearning(t.question) {
            let key = t.question.lowercased().trimmingCharacters(in: .whitespaces)
            guard !key.isEmpty, !seen.contains(key), let reason = reason(for: t) else { continue }
            seen.insert(key)
            out.append(Gap(question: t.question, reason: reason, filenames: t.filenames, refId: t.id))
            if out.count >= max { break }
        }
        return out
    }
}

/// Candidate connections between the nodes on the map, from three signals the notebook already has: shared named
/// things (the knowledge graph), similar meaning (the search model's vectors), shared sources. Blended
/// 0.5 · concept + 0.35 · embed + 0.15 · shared, per-node top 3, score ≥ 0.5, at most 12 on the whole map, never
/// stored (the old canvas_candidates).
public enum CanvasCandidates {
    public static let weightConcept = 0.5, weightEmbed = 0.35, weightShared = 0.15
    public static let topK = 3, minScore = 0.5, globalCap = 12, conceptSaturation = 5.0

    public struct Input: Sendable { public let id: String; public let vector: [Float]; public let sourceIds: Set<String>; public let concepts: Set<String>
        public init(id: String, vector: [Float], sourceIds: Set<String>, concepts: Set<String>) { self.id = id; self.vector = vector; self.sourceIds = sourceIds; self.concepts = concepts } }
    public struct Candidate: Sendable, Hashable, Identifiable {
        public var id: String { CanvasEdge.pairId(a, b, "candidate") }
        public let a: String, b: String, score: Double, signal: String
        public var label: String { switch signal { case "concept": "shared concepts"; case "embed": "similar meaning"; default: "shared source" } }
    }

    public static func overlap(_ a: Set<String>, _ b: Set<String>) -> Double {
        guard !a.isEmpty, !b.isEmpty else { return 0 }
        return Double(a.intersection(b).count) / Double(min(a.count, b.count))
    }

    public static func score(_ x: Input, _ y: Input) -> (score: Double, signal: String) {
        let concept = min(1, Double(x.concepts.intersection(y.concepts).count) / conceptSaturation)
        var embed = 0.0
        if !x.vector.isEmpty, x.vector.count == y.vector.count { embed = max(0, min(1, VectorMath.cosine(x.vector, y.vector))) }
        let shared = overlap(x.sourceIds, y.sourceIds)
        let parts = [("concept", weightConcept * concept), ("embed", weightEmbed * embed), ("shared_source", weightShared * shared)]
        let total = parts.reduce(0) { $0 + $1.1 }
        let strongest = parts.max { $0.1 < $1.1 }?.0 ?? "embed"
        return (total, strongest)
    }

    /// Pairs between the given nodes, bounded, skipping pairs already joined by a kept edge.
    public static func compute(_ nodes: [Input], existing: [CanvasEdge]) -> [Candidate] {
        let joined = Set(existing.filter { $0.state != "candidate" }.map { [$0.source, $0.target].sorted().joined(separator: "|") })
        var all: [Candidate] = []
        for i in nodes.indices { for j in nodes.indices where j > i {
            let key = [nodes[i].id, nodes[j].id].sorted().joined(separator: "|")
            if joined.contains(key) { continue }
            let (s, signal) = score(nodes[i], nodes[j])
            if s >= minScore { all.append(Candidate(a: nodes[i].id, b: nodes[j].id, score: s, signal: signal)) }
        } }
        all.sort { $0.score > $1.score || ($0.score == $1.score && $0.id < $1.id) }
        var perNode: [String: Int] = [:]
        var out: [Candidate] = []
        for c in all where (perNode[c.a] ?? 0) < topK && (perNode[c.b] ?? 0) < topK {
            out.append(c); perNode[c.a, default: 0] += 1; perNode[c.b, default: 0] += 1
            if out.count >= globalCap { break }
        }
        return out
    }
}

/// Tension edges: two sources that disagree, from the notebook's contradictions insight (the old canvas_tension's
/// strongest path, the detected pairs). Derived on every populate, never trusted from an old run.
public enum CanvasTension {
    public static let maxPerSource = 4

    public static func edges(from contradictions: ContradictionsInsight, nodes: [CanvasNode]) -> [CanvasEdge] {
        let byRef = Dictionary(nodes.filter { $0.kind == CanvasNode.sourceKind }.compactMap { n in n.refId.map { ($0, n.id) } }, uniquingKeysWith: { a, _ in a })
        var count: [String: Int] = [:]
        var out: [CanvasEdge] = []
        let ranked = contradictions.items.filter { !$0.dismissed }.sorted { rank($0.severity) > rank($1.severity) }
        for item in ranked {
            guard item.sourceA != item.sourceB, let a = byRef[item.sourceA], let b = byRef[item.sourceB] else { continue }
            let id = CanvasEdge.pairId(a, b, "tension")
            guard !out.contains(where: { $0.id == id }), (count[a] ?? 0) < maxPerSource, (count[b] ?? 0) < maxPerSource else { continue }
            out.append(CanvasEdge(id: id, source: a, target: b, state: "tension", label: item.type.isEmpty ? "disagree" : item.type,
                                  meta: ["severity": item.severity, "claim_a": item.claimA, "claim_b": item.claimB, "explanation": item.explanation], createdAt: CanvasStore.stamp()))
            count[a, default: 0] += 1; count[b, default: 0] += 1
        }
        return out
    }
    static func rank(_ severity: String) -> Int { switch severity.lowercased() { case "high": 3; case "medium": 2; default: 1 } }
}

/// Provenance: what a Studio item, insight or answer was made from, drawn as thin edges to its source nodes.
public enum CanvasProvenance {
    public static func edges(for layout: CanvasLayout) -> [CanvasEdge] {
        let sourceNode = Dictionary(layout.nodes.filter { $0.kind == CanvasNode.sourceKind }.compactMap { n in n.refId.map { ($0, n.id) } }, uniquingKeysWith: { a, _ in a })
        var out: [CanvasEdge] = []
        for n in layout.nodes where n.kind == CanvasNode.artifactKind || n.kind == CanvasNode.chatKind {
            for sid in n.sourceIds { if let target = sourceNode[sid] {
                out.append(CanvasEdge(id: CanvasEdge.pairId(n.id, target, "provenance"), source: target, target: n.id, state: "provenance", label: "made from", createdAt: n.createdAt))
            } }
        }
        return out
    }
}

/// Spaced repetition over the map's learning nodes (SM-2, as the old canvas_recall): again / good / easy.
public enum CanvasRecall {
    public enum Grade: String, Sendable, CaseIterable { case again, good, easy }

    public static func due(_ layout: CanvasLayout, now: Date = Date(), limit: Int = 20) -> [CanvasNode] {
        layout.nodes.filter { n in
            guard n.kind == CanvasNode.chatKind || n.kind == CanvasNode.artifactKind else { return false }
            guard let s = layout.recall[n.id] else { return true }          // never reviewed: due now
            return s.nextDue <= now
        }.sorted { a, b in
            let da = layout.recall[a.id]?.nextDue ?? .distantPast, db = layout.recall[b.id]?.nextDue ?? .distantPast
            return da < db
        }.prefix(limit).map { $0 }
    }

    public static func graded(_ state: RecallState?, _ grade: Grade, now: Date = Date()) -> RecallState {
        var s = state ?? RecallState(nextDue: now)
        let quality: Double = grade == .again ? 1 : (grade == .good ? 4 : 5)
        if quality < 3 { s.repetitions = 0; s.interval = 1 }
        else {
            s.repetitions += 1
            s.interval = s.repetitions == 1 ? 1 : (s.repetitions == 2 ? 6 : Int((Double(s.interval) * s.ease).rounded()))
            if grade == .easy { s.interval = max(s.interval, Int((Double(s.interval) * 1.3).rounded())) }
        }
        s.ease = max(1.3, s.ease + 0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
        s.reviews += 1
        s.nextDue = now.addingTimeInterval(Double(s.interval) * 86_400)
        return s
    }
}
