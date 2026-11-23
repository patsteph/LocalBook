import Foundation

/// What the critic asks and what it hears back. The critic itself (a look at the finished picture by the answer
/// model, Mac only) lives in the engines; the words it uses and the reading of its reply are here so they can be
/// tested without a model. The five axes are the ones the old app's critic scored.
public struct VisualCritique: Sendable, Codable, Equatable {
    public var legibility: Int
    public var hierarchy: Int
    public var balance: Int
    public var colour: Int
    public var message: Int
    /// The one thing the critic would change, in its words; empty when it had nothing to say.
    public var problem: String

    public init(legibility: Int, hierarchy: Int, balance: Int, colour: Int, message: Int, problem: String = "") {
        self.legibility = legibility; self.hierarchy = hierarchy; self.balance = balance; self.colour = colour; self.message = message; self.problem = problem
    }

    /// The mean of the five, out of 5.
    public var mean: Double { Double(legibility + hierarchy + balance + colour + message) / 5 }
    /// Below this the engine asks the model to fill the picture once more, with the problem attached.
    public static let bar = 3.5
    public var passes: Bool { mean >= Self.bar }
    /// "3.8/5: the labels in the third card crowd the number"
    public var line: String { String(format: "%.1f/5", mean) + (problem.isEmpty ? "" : ": " + problem) }

    public static let instructions = "You judge diagrams for a reader who sees them for the first time. Reply with JSON only."

    /// The question put with the picture.
    public static func prompt(title: String, form: String) -> String {
        "This is a \(form.lowercased()) titled “\(title)”. Score it from 1 (poor) to 5 (excellent) on legibility (can every word be read), hierarchy (is it clear what matters most), balance (is the space used evenly, nothing crowded or empty), colour (do the colours help and stay calm) and message (does the picture make one clear point). Reply with ONLY {\"legibility\": n, \"hierarchy\": n, \"balance\": n, \"colour\": n, \"message\": n, \"problem\": \"the one thing to change, in at most 12 words, or an empty string\"}."
    }

    /// Reads the reply leniently: each axis is the first digit after its name (British or American spelling), the problem
    /// is the quoted text after "problem". nil when fewer than three axes were scored.
    public static func parse(_ raw: String) -> VisualCritique? {
        func score(_ name: String) -> Int? {
            guard let r = raw.range(of: "\"?\(name)\"?\\s*:\\s*([1-5])", options: [.regularExpression, .caseInsensitive]) else { return nil }
            return raw[r].last.flatMap { Int(String($0)) }
        }
        let l = score("legibility"), h = score("hierarchy"), b = score("balance"), c = score("colou?r"), m = score("message")
        let found = [l, h, b, c, m].compactMap { $0 }
        guard found.count >= 3 else { return nil }
        let fill = Int((Double(found.reduce(0, +)) / Double(found.count)).rounded())
        var problem = ""
        if let r = raw.range(of: "\"problem\"\\s*:\\s*\"([^\"]*)\"", options: [.regularExpression, .caseInsensitive]) {
            let inner = String(raw[r])
            if let q1 = inner.range(of: ":"), let open = inner[q1.upperBound...].firstIndex(of: "\""), let close = inner[inner.index(after: open)...].lastIndex(of: "\"") {
                problem = String(inner[inner.index(after: open)..<close]).trimmingCharacters(in: .whitespaces)
            }
        }
        return VisualCritique(legibility: l ?? fill, hierarchy: h ?? fill, balance: b ?? fill, colour: c ?? fill, message: m ?? fill, problem: problem)
    }
}
