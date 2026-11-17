import Foundation

/// A Jupyter notebook as the old app read it (`DocumentProcessor._extract_from_jupyter` over nbformat): every cell
/// numbered from 1, Markdown sources as they are, code in a python fence, stream outputs as `Output:` and plain-text
/// results as `Result:`, a blank line after each cell. Sources and output texts given as line lists are joined, as
/// nbformat's reader joins them. Proved by `Fixtures/jupyter_golden.json` (`scripts/ci/jupyter_golden.py`).
public enum JupyterText {
    public static func text(_ data: Data) throws -> String {
        guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw NSError(domain: "JupyterText", code: 1, userInfo: [NSLocalizedDescriptionKey: "Failed to process Jupyter notebook: not a notebook"])
        }
        // nbformat 3 kept cells under worksheets; 4 lists them at the top.
        var cells = root["cells"] as? [[String: Any]] ?? []
        if cells.isEmpty, let sheets = root["worksheets"] as? [[String: Any]] {
            cells = sheets.flatMap { $0["cells"] as? [[String: Any]] ?? [] }
        }
        var parts: [String] = []
        for (i, cell) in cells.enumerated() {
            let n = i + 1
            let source = joined(cell["source"] ?? cell["input"])
            switch cell["cell_type"] as? String {
            case "markdown":
                parts.append("[Markdown Cell \(n)]")
                parts.append(source)
            case "code":
                parts.append("[Code Cell \(n)]")
                parts.append("```python\n\(source)\n```")
                for output in cell["outputs"] as? [[String: Any]] ?? [] {
                    switch output["output_type"] as? String {
                    case "stream": parts.append("Output: \(joined(output["text"]))")
                    case "execute_result":
                        if let data = output["data"] as? [String: Any], let plain = data["text/plain"] { parts.append("Result: \(joined(plain))") }
                    default: break
                    }
                }
            default: break
            }
            parts.append("")
        }
        return parts.joined(separator: "\n")
    }

    /// nbformat's `rejoin_lines`: a list of lines becomes one string.
    static func joined(_ value: Any?) -> String {
        if let s = value as? String { return s }
        if let lines = value as? [String] { return lines.joined() }
        return ""
    }
}
