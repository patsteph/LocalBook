import Foundation

/// One request from a phone: a POST with a JSON body. The phone link is one request per connection, so this is all
/// the HTTP the host needs to read.
public struct HTTPRequest: Sendable, Equatable {
    public var method: String
    /// The path alone, without the query (the phone signs the path).
    public var path: String
    /// Header names lower-cased.
    public var headers: [String: String]
    public var body: Data
    /// The query string's pairs (`?notebook_id=x&url=y`), percent-decoded; empty when there is none.
    public var query: [String: String] = [:]

    public init(method: String, path: String, headers: [String: String], body: Data) {
        self.method = method; self.path = path; self.headers = headers; self.body = body
    }
}

/// The little HTTP/1.1 the phone link needs: read one request from the bytes received so far, write one reply.
public enum HTTPWire {
    public enum ParseError: Error, Equatable { case malformed, tooLarge }

    public static let maxHeaderBytes = 64 * 1024
    /// An audio overview arrives in pieces of 600 KB (800 KB as base64); a note's photo may be 6 MB. Room for both.
    public static let maxBodyBytes = 16 * 1024 * 1024

    /// The request at the front of `buffer`, and how many bytes it took. nil when the request is not complete yet.
    public static func parse(_ buffer: Data) throws -> (request: HTTPRequest, consumed: Int)? {
        guard let end = buffer.range(of: Data("\r\n\r\n".utf8)) else {
            if buffer.count > maxHeaderBytes { throw ParseError.tooLarge }
            return nil
        }
        guard end.lowerBound - buffer.startIndex <= maxHeaderBytes else { throw ParseError.tooLarge }
        let head = String(decoding: buffer[buffer.startIndex..<end.lowerBound], as: UTF8.self)
        var lines = head.components(separatedBy: "\r\n")
        guard let first = lines.first else { throw ParseError.malformed }
        lines.removeFirst()
        let parts = first.split(separator: " ", omittingEmptySubsequences: true)
        guard parts.count == 3, parts[2].hasPrefix("HTTP/1.") else { throw ParseError.malformed }
        var headers: [String: String] = [:]
        for line in lines where !line.isEmpty {
            guard let colon = line.firstIndex(of: ":") else { throw ParseError.malformed }
            headers[line[..<colon].lowercased()] = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
        }
        guard let length = Int(headers["content-length"] ?? "0"), length >= 0 else { throw ParseError.malformed }
        guard length <= maxBodyBytes else { throw ParseError.tooLarge }
        let bodyStart = end.upperBound
        guard buffer.endIndex - bodyStart >= length else { return nil }
        let body = Data(buffer[bodyStart..<(bodyStart + length)])
        var path = String(parts[1])
        var query: [String: String] = [:]
        if let q = path.firstIndex(of: "?") {
            query = queryPairs(String(path[path.index(after: q)...]))
            path = String(path[..<q])
        }
        var request = HTTPRequest(method: parts[0].uppercased(), path: path, headers: headers, body: body)
        request.query = query
        return (request, (bodyStart - buffer.startIndex) + length)
    }

    /// `a=1&b=hello%20there` as pairs, `+` and percent escapes decoded; a key without `=` maps to "".
    public static func queryPairs(_ raw: String) -> [String: String] {
        var out: [String: String] = [:]
        for part in raw.split(separator: "&", omittingEmptySubsequences: true) {
            let pieces = part.split(separator: "=", maxSplits: 1, omittingEmptySubsequences: false)
            func decode(_ s: Substring) -> String { s.replacingOccurrences(of: "+", with: " ").removingPercentEncoding ?? String(s) }
            out[decode(pieces[0])] = pieces.count > 1 ? decode(pieces[1]) : ""
        }
        return out
    }

    /// A whole reply with a JSON body; the connection closes after it.
    public static func response(status: Int, json body: Data) -> Data {
        response(status: status, body: body, contentType: "application/json")
    }

    /// A whole reply with any body and extra headers (an MCP session id, CORS for the browser extension).
    public static func response(status: Int, body: Data, contentType: String, headers: [String: String] = [:]) -> Data {
        var head = "HTTP/1.1 \(status) \(reason(status))\r\n"
        head += "Content-Type: \(contentType)\r\nContent-Length: \(body.count)\r\nConnection: close\r\n"
        for (k, v) in headers.sorted(by: { $0.key < $1.key }) { head += "\(k): \(v)\r\n" }
        head += "\r\n"
        return Data(head.utf8) + body
    }

    /// `{"detail": "…"}`, the shape FastAPI gave the phone for a refusal, so the phone's error lines read the same.
    public static func problem(_ status: Int, _ detail: String) -> (Int, Data) {
        (status, (try? JSONSerialization.data(withJSONObject: ["detail": detail])) ?? Data("{}".utf8))
    }

    public static func reason(_ status: Int) -> String {
        switch status {
        case 200: "OK"
        case 202: "Accepted"
        case 204: "No Content"
        case 400: "Bad Request"
        case 401: "Unauthorized"
        case 403: "Forbidden"
        case 404: "Not Found"
        case 405: "Method Not Allowed"
        case 409: "Conflict"
        case 413: "Payload Too Large"
        case 429: "Too Many Requests"
        case 500: "Internal Server Error"
        case 503: "Service Unavailable"
        default: "Status"
        }
    }
}
