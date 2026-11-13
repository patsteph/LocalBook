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
}
