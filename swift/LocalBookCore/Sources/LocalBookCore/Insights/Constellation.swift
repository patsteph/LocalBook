import Foundation

/// The Constellation: the knowledge graph as a star map. Named things are stars, grouped into clusters (communities
/// found by label propagation, deterministic), each cluster on its own patch of a sphere, the most connected star of a
/// cluster at its centre and the biggest. Pure geometry; the Mac draws it in SceneKit.
public enum Constellation {
    public struct Star: Sendable, Hashable, Identifiable {
        public let id: String
        public let name: String
        public let type: String
        public let cluster: Int
        public let connections: Int
        public let mentions: Int
        public let sourceIds: [String]
        public let context: String
        public var x: Float, y: Float, z: Float
        public var size: Float
        public var isHub: Bool
    }
    public struct Link: Sendable, Hashable, Identifiable {
        public var id: String { a + "→" + b }
        public let a: String, b: String, relation: String, weight: Int
    }
    public struct Cluster: Sendable, Hashable, Identifiable {
        public let id: Int
        public let name: String
        public let members: [String]
        public let hue: Double            // 0…1, spread evenly around the wheel
    }
    public struct Scene: Sendable, Hashable {
        public var stars: [Star]
        public var links: [Link]
        public var clusters: [Cluster]
        public init(stars: [Star] = [], links: [Link] = [], clusters: [Cluster] = []) { self.stars = stars; self.links = links; self.clusters = clusters }
        public func star(_ id: String) -> Star? { stars.first { $0.id == id } }
        public func neighbours(of id: String) -> Set<String> { Set(links.filter { $0.a == id || $0.b == id }.map { $0.a == id ? $0.b : $0.a }) }
    }

    public static let outerRadius: Float = 90
    public static let innerRadius: Float = 25

    /// Communities by label propagation with a fixed order, so the same graph gives the same clusters. Isolated nodes
    /// join a cluster of their own kind ("their type"), and tiny communities fold into the nearest big one by type.
    public static func communities(_ graph: GraphInsight, maxClusters: Int = 12) -> [[String]] {
        let ids = graph.nodes.map(\.id)
        guard !ids.isEmpty else { return [] }
        var adjacency: [String: [(String, Int)]] = [:]
        for e in graph.edges { adjacency[e.a, default: []].append((e.b, e.weight)); adjacency[e.b, default: []].append((e.a, e.weight)) }
        var label = Dictionary(uniqueKeysWithValues: ids.enumerated().map { ($0.element, $0.offset) })
        let order = ids.sorted { (adjacency[$0]?.count ?? 0) > (adjacency[$1]?.count ?? 0) || ((adjacency[$0]?.count ?? 0) == (adjacency[$1]?.count ?? 0) && $0 < $1) }
        for _ in 0..<20 {
            var changed = false
            for id in order {
                guard let neighbours = adjacency[id], !neighbours.isEmpty else { continue }
                var weight: [Int: Int] = [:]
                for (n, w) in neighbours { if let l = label[n] { weight[l, default: 0] += w } }
                guard let best = weight.max(by: { a, b in a.value < b.value || (a.value == b.value && a.key > b.key) })?.key else { continue }
                if best != label[id] { label[id] = best; changed = true }
            }
            if !changed { break }
        }
        var groups: [Int: [String]] = [:]
        for id in ids { groups[label[id]!, default: []].append(id) }
        let types = Dictionary(uniqueKeysWithValues: graph.nodes.map { ($0.id, $0.type) })
        var big = groups.values.filter { $0.count >= 2 }.sorted { $0.count > $1.count || ($0.count == $1.count && $0[0] < $1[0]) }
        let singles = groups.values.filter { $0.count < 2 }.flatMap { $0 }
        // Lone stars gather by type into one cluster each.
        var byType: [String: [String]] = [:]
        for s in singles { byType[types[s] ?? "other", default: []].append(s) }
        for (_, members) in byType.sorted(by: { $0.key < $1.key }) { big.append(members.sorted()) }
        // Too many clusters: fold the smallest into the largest that shares a type.
        while big.count > maxClusters, let last = big.popLast() {
            let t = types[last[0]] ?? ""
            let into = big.indices.first { big[$0].contains { types[$0] == t } } ?? 0
            big[into].append(contentsOf: last)
        }
        return big
    }

    /// Lays the graph out: cluster centres spread on the outer sphere (golden-angle spiral), each cluster's hub at its
    /// centre, members on rings around it, nudged inward or outward by how connected they are.
    public static func scene(_ graph: GraphInsight, limitTo sourceId: String? = nil) -> Scene {
        var g = graph
        if let sourceId {
            let keep = Set(g.nodes.filter { $0.sourceIds.contains(sourceId) }.map(\.id))
            g = GraphInsight(nodes: g.nodes.filter { keep.contains($0.id) }, edges: g.edges.filter { keep.contains($0.a) && keep.contains($0.b) })
        }
        let groups = communities(g)
        var degree: [String: Int] = [:]
        for e in g.edges { degree[e.a, default: 0] += 1; degree[e.b, default: 0] += 1 }
        let nodes = Dictionary(uniqueKeysWithValues: g.nodes.map { ($0.id, $0) })
        var stars: [Star] = []
        var clusters: [Cluster] = []
        let golden = Float.pi * (3 - Float(5).squareRoot())
        for (ci, members) in groups.enumerated() {
            let t = groups.count == 1 ? 0.5 : (Float(ci) + 0.5) / Float(groups.count)
            let phi = acos(1 - 2 * t)
            let theta = golden * Float(ci)
            let centreR = groups.count == 1 ? 0 : outerRadius * 0.55
            let cx = centreR * sin(phi) * cos(theta), cy = centreR * cos(phi), cz = centreR * sin(phi) * sin(theta)
            let ranked = members.sorted { a, b in
                let da = degree[a] ?? 0, db = degree[b] ?? 0
                if da != db { return da > db }
                let ma = nodes[a]?.mentions ?? 0, mb = nodes[b]?.mentions ?? 0
                if ma != mb { return ma > mb }
                return a < b
            }
            let hubName = ranked.first.flatMap { nodes[$0]?.name } ?? "Cluster \(ci + 1)"
            let second = ranked.dropFirst().first.flatMap { nodes[$0]?.name }
            clusters.append(Cluster(id: ci, name: second.map { "\(hubName) · \($0)" } ?? hubName, members: ranked, hue: Double(ci) / Double(max(1, groups.count))))
            for (mi, id) in ranked.enumerated() {
                guard let n = nodes[id] else { continue }
                let d = degree[id] ?? 0
                let size: Float = min(6, 1.6 + 0.35 * Float(d) + 0.08 * Float(min(n.mentions, 20)))
                if mi == 0 {
                    stars.append(Star(id: id, name: n.name, type: n.type, cluster: ci, connections: d, mentions: n.mentions, sourceIds: n.sourceIds, context: n.context, x: cx, y: cy, z: cz, size: size * 1.25, isHub: true))
                } else {
                    let ring = 1 + (mi - 1) / 8
                    let slot = (mi - 1) % 8
                    let count = min(8, ranked.count - 1 - 8 * (ring - 1))
                    let angle = Float(slot) / Float(max(1, count)) * 2 * .pi + Float(ci) * 0.7
                    let r = innerRadius * 0.55 * Float(ring) * (1.2 - 0.2 * min(1, Float(d) / 6))
                    let x = cx + r * cos(angle), y = cy + r * sin(angle) * 0.6 + Float(ring) * 3, z = cz + r * sin(angle) * 0.35
                    stars.append(Star(id: id, name: n.name, type: n.type, cluster: ci, connections: d, mentions: n.mentions, sourceIds: n.sourceIds, context: n.context, x: x, y: y, z: z, size: size, isHub: false))
                }
            }
        }
        let links = g.edges.filter { nodes[$0.a] != nil && nodes[$0.b] != nil }.map { Link(a: $0.a, b: $0.b, relation: $0.relation, weight: $0.weight) }
        return Scene(stars: stars, links: links, clusters: clusters)
    }
}
