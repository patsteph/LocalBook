import Foundation

public enum VectorMath {
    public static func normalized(_ v: [Float]) -> [Float] {
        let n = v.reduce(0) { $0 + $1 * $1 }.squareRoot()
        return n > 0 ? v.map { $0 / n } : v
    }

    public static func dot(_ a: [Float], _ b: [Float]) -> Float {
        precondition(a.count == b.count)
        var s: Float = 0
        for i in 0..<a.count { s += a[i] * b[i] }
        return s
    }

    /// Cosine of two vectors (both are unit length here, but normalise to be safe).
    public static func cosine(_ a: [Float], _ b: [Float]) -> Double {
        guard a.count == b.count, !a.isEmpty else { return .nan }
        return Double(dot(normalized(a), normalized(b)))
    }

    /// Indices of the top-k corpus vectors for a query, best first.
    public static func topK(_ query: [Float], in corpus: [[Float]], k: Int) -> [Int] {
        let q = normalized(query)
        let scored = corpus.enumerated().map { ($0.offset, dot(q, normalized($0.element))) }
        return scored.sorted { $0.1 > $1.1 }.prefix(k).map(\.0)
    }
}
