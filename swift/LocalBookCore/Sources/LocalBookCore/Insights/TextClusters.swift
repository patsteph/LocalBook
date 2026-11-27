import Foundation

/// The notebook's topics without a model: k-means over the chunks' vectors (the same ones search uses), then the words
/// that mark each cluster (class-based TF-IDF, the way BERTopic names topics before a model polishes the name).
public enum TextClusters {
    public struct Item: Sendable { public var id: String; public var sourceId: String; public var text: String; public var vector: [Float]
        public init(id: String, sourceId: String, text: String, vector: [Float]) { self.id = id; self.sourceId = sourceId; self.text = text; self.vector = vector } }
    public struct Cluster: Sendable, Equatable { public var members: [Int]; public var keywords: [String] }

    /// How many clusters for `n` chunks: a handful for a small notebook, up to twelve for a big one.
    public static func clusterCount(for n: Int) -> Int { max(2, min(12, Int((Double(n) / 6).squareRoot().rounded()))) }

    /// Clusters by cosine (vectors are unit length), seeded by k-means++ with a fixed seed so a rebuild repeats.
    public static func kMeans(_ items: [Item], k requested: Int? = nil, rounds: Int = 25) -> [Cluster] {
        guard items.count >= 4, let dims = items.first?.vector.count else { return items.isEmpty ? [] : [Cluster(members: Array(items.indices), keywords: keywords(for: items.map(\.text)))] }
        let k = min(requested ?? clusterCount(for: items.count), items.count / 2)
        var rng = SplitMix(seed: 42)
        var centers: [[Float]] = [items[Int(rng.next() % UInt64(items.count))].vector]
        while centers.count < k {
            let d = items.map { it in centers.map { 1 - dot(it.vector, $0) }.min() ?? 1 }
            let total = d.reduce(0, +)
            var pick = Float(Double(rng.next() % 1_000_000) / 1_000_000) * total
            var chosen = items.count - 1
            for (i, v) in d.enumerated() { pick -= v; if pick <= 0 { chosen = i; break } }
            centers.append(items[chosen].vector)
        }
        var assignment = [Int](repeating: 0, count: items.count)
        for _ in 0..<rounds {
            var moved = false
            for (i, it) in items.enumerated() {
                var best = 0, bestDot: Float = -2
                for (c, center) in centers.enumerated() { let s = dot(it.vector, center); if s > bestDot { bestDot = s; best = c } }
                if assignment[i] != best { assignment[i] = best; moved = true }
            }
            var sums = [[Float]](repeating: [Float](repeating: 0, count: dims), count: k)
            var counts = [Int](repeating: 0, count: k)
            for (i, it) in items.enumerated() { counts[assignment[i]] += 1; for d in 0..<dims { sums[assignment[i]][d] += it.vector[d] } }
            for c in 0..<k where counts[c] > 0 {
                var norm: Float = 0
                for d in 0..<dims { norm += sums[c][d] * sums[c][d] }
                norm = norm.squareRoot()
                if norm > 0 { centers[c] = sums[c].map { $0 / norm } }
            }
            if !moved { break }
        }
        var groups = [[Int]](repeating: [], count: k)
        for (i, c) in assignment.enumerated() { groups[c].append(i) }
        let texts = groups.map { $0.map { items[$0].text }.joined(separator: "\n") }
        let allCounts = texts.map { wordCounts($0) }
        return groups.enumerated().filter { !$0.element.isEmpty }.map { c, members in
            Cluster(members: members, keywords: topWords(allCounts[c], against: allCounts))
        }.sorted { $0.members.count > $1.members.count }
    }

    /// The words that mark `texts` against nothing else (for a single group).
    public static func keywords(for texts: [String], count: Int = 8) -> [String] {
        Array(wordCounts(texts.joined(separator: "\n")).sorted { $0.value > $1.value || ($0.value == $1.value && $0.key < $1.key) }.prefix(count).map(\.key))
    }

    /// Class-based TF-IDF: a word's frequency in this cluster, scaled down by how many clusters it appears in.
    static func topWords(_ counts: [String: Int], against all: [[String: Int]], count: Int = 8) -> [String] {
        let total = max(1, counts.values.reduce(0, +))
        var scores: [(String, Double)] = []
        for (w, c) in counts {
            let inClusters = all.filter { ($0[w] ?? 0) > 0 }.count
            let idf = log(1 + Double(all.count) / Double(max(1, inClusters)))
            scores.append((w, Double(c) / Double(total) * idf))
        }
        return Array(scores.sorted { $0.1 > $1.1 || ($0.1 == $1.1 && $0.0 < $1.0) }.prefix(count).map(\.0))
    }

    static func wordCounts(_ text: String) -> [String: Int] {
        var counts: [String: Int] = [:]
        for raw in text.lowercased().split(whereSeparator: { !$0.isLetter && !$0.isNumber && $0 != "-" }) {
            let w = String(raw)
            guard w.count > 2, !stopwords.contains(w), !w.allSatisfy(\.isNumber) else { continue }
            counts[w, default: 0] += 1
        }
        return counts
    }

    static func dot(_ a: [Float], _ b: [Float]) -> Float {
        var s: Float = 0
        for i in 0..<min(a.count, b.count) { s += a[i] * b[i] }
        return s
    }

    public static let stopwords: Set<String> = Set("""
    the and for are but not you all any can had her was one our out day get has him his how man new now old see two way who boy did its let put say she too use that with have this will your from they know want been good much some time very when come here just like long make many more only over such take than them well were what where which while with would about after again also back because before being between both could does down each even every first found from give going great have here high however into just keep know large last later least less life like little long made make many might more most much must name need never next often once only other over part people place point right same seen several shall should since small some still such take tell than that their them then there these they thing think this those though three through time under until upon used very want well went were what when where whether which while whole whose will with within without work world would year years also including based using used use via per etc one two three four five six seven eight nine ten
    """.split(whereSeparator: \.isWhitespace).map(String.init))

    /// A small deterministic generator, so the clusters of a notebook come out the same each time.
    struct SplitMix { var state: UInt64; init(seed: UInt64) { state = seed }
        mutating func next() -> UInt64 { state &+= 0x9E3779B97F4A7C15; var z = state; z = (z ^ (z >> 30)) &* 0xBF58476D1CE4E5B9; z = (z ^ (z >> 27)) &* 0x94D049BB133111EB; return z ^ (z >> 31) } }
}
