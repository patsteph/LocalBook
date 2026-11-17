import Foundation
import Testing
@testable import LocalBookCore

/// `JupyterText` against what nbformat and the old app's extractor made of the same notebooks.
struct JupyterTextTests {
    struct Golden: Decodable { let cases: [Case] }
    struct Case: Decodable { let name: String; let notebook: String; let text: String }

    static func golden() throws -> Golden {
        let url = try #require(Bundle.module.url(forResource: "jupyter_golden", withExtension: "json", subdirectory: "Fixtures"))
        return try JSONDecoder().decode(Golden.self, from: Data(contentsOf: url))
    }

    @Test func notebooksMatchTheOldApp() throws {
        for c in try Self.golden().cases {
            let got = try JupyterText.text(Data(c.notebook.utf8))
            #expect(got == c.text, "\(c.name): got \(got.debugDescription)")
        }
    }

    @Test func notANotebookThrows() {
        #expect(throws: (any Error).self) { try JupyterText.text(Data("[1, 2]".utf8)) }
    }
}
