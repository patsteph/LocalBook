import Foundation

/// Every top-level JSON object in a model's reply, in order, skipping anything that is not one (prose, fences).
public enum JSONObjects {
    public static func scan(in raw: String) -> [[String: Any]] {
        var out: [[String: Any]] = []
        var depth = 0, inString = false, escaped = false
        var start: String.Index?
        for i in raw.indices {
            let c = raw[i]
            if inString {
                if escaped { escaped = false } else if c == "\\" { escaped = true } else if c == "\"" { inString = false }
                continue
            }
            if c == "\"" { inString = true }
            else if c == "{" { if depth == 0 { start = i }; depth += 1 }
            else if c == "}" && depth > 0 {
                depth -= 1
                if depth == 0, let st = start,
                   let obj = try? JSONSerialization.jsonObject(with: Data(raw[st...i].utf8)) as? [String: Any] { out.append(obj) }
            }
        }
        return out
    }

    /// The first object `scan` finds, or, when the reply holds none that parses, the first object repaired: a trailing
    /// comma before a closing bracket, a single-quoted string, a line comment or a reply cut off mid-list (the token
    /// budget ran out) each break strict JSON, and a small model writes all of them. Nothing is invented: a cut-off
    /// reply keeps the entries that were complete.
    public static func first(in raw: String) -> [String: Any]? {
        if let o = scan(in: raw).first { return o }
        guard let open = raw.firstIndex(of: "{") else { return nil }
        var text = String(raw[open...])
        // Fences and prose after the object.
        if let fence = text.range(of: "```") { text = String(text[..<fence.lowerBound]) }
        text = text.replacingOccurrences(of: #"(?m)^\s*//.*$"#, with: "", options: .regularExpression)
        text = text.replacingOccurrences(of: #",\s*([}\]])"#, with: "$1", options: .regularExpression)
        if let o = parsed(text) { return o }
        // Cut off: drop the unfinished tail back to the last complete value, then close what is open.
        var kept = text
        for _ in 0..<40 {
            if let o = parsed(closed(kept)) { return o }
            guard let cut = kept.lastIndex(where: { $0 == "," }) else { break }
            kept = String(kept[..<cut])
        }
        return nil
    }

    private static func parsed(_ text: String) -> [String: Any]? {
        try? JSONSerialization.jsonObject(with: Data(text.utf8), options: [.fragmentsAllowed]) as? [String: Any]
    }

    /// `text` with an open string, array and object closed in the right order.
    private static func closed(_ text: String) -> String {
        var stack: [Character] = []
        var inString = false, escaped = false
        for c in text {
            if inString {
                if escaped { escaped = false } else if c == "\\" { escaped = true } else if c == "\"" { inString = false }
                continue
            }
            switch c {
            case "\"": inString = true
            case "{": stack.append("}")
            case "[": stack.append("]")
            case "}", "]": if stack.last == c { stack.removeLast() }
            default: break
            }
        }
        var out = text
        if inString { out += "\"" }
        // A dangling key ("title": ) or comma before the close.
        out = out.replacingOccurrences(of: #"[,:]\s*$"#, with: "", options: .regularExpression)
        out = out.replacingOccurrences(of: #",\s*([}\]])"#, with: "$1", options: .regularExpression)
        for c in stack.reversed() { out.append(c) }
        return out
    }
}
