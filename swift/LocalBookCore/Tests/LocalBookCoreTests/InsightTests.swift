import Foundation
import Testing
@testable import LocalBookCore

@Suite struct InsightTests {
    @Test func dateKeys() {
        #expect(DateKey.parse("2023-06-30")! > 2023.45 && DateKey.parse("2023-06-30")! < 2023.5)
        #expect(DateKey.parse("June 2023") == DateKey.parse("2023-06"))
        #expect(DateKey.parse("Q1 2025")! < DateKey.parse("Q3 2025")!)
        #expect(DateKey.parse("1990s") == 1990)
        #expect(DateKey.parse("mid-2024") == 2024.5)
        #expect(DateKey.parse("late 2019")! > DateKey.parse("early 2019")!)
        #expect(DateKey.parse("March 3, 2021")! > DateKey.parse("March 2021")!)
        #expect(DateKey.parse("FY23") == 2023)
        #expect(DateKey.parse("6/30/2023") == DateKey.parse("2023-06-30"))
        #expect(DateKey.parse("someday") == nil)
        #expect(DateKey.parse("") == nil)
        #expect(DateKey.label(2023.5).hasPrefix("Jul 2023"))
        #expect(DateKey.label(2019) == "2019")
    }

    @Test func timelineReadsAndMerges() {
        let raw = """
        Here is the timeline:
        ```json
        {"events": [{"date": "June 2023", "title": "FY23 review", "description": "Results published", "importance": "high", "passage": 2},
                    {"date": "Q1 2024", "title": "Launch", "description": "", "importance": "silly", "passage": 9},
                    {"date": "", "title": "Undated note", "description": "x", "importance": "low"}],
         "time_span": "2023 to 2024", "context": "A product's year"}
        ```
        """
        let part = InsightPrompts.timelineEvents(from: raw, sources: [("s1", "a.pdf"), ("s2", "b.pdf")])
        #expect(part.events.count == 3)
        #expect(part.events[0].filename == "b.pdf" && part.events[0].importance == "high")
        #expect(part.events[1].filename == nil && part.events[1].importance == "medium")
        #expect(part.timeSpan == "2023 to 2024")
        let other = InsightPrompts.timelineEvents(from: "[{\"date\": \"2022\", \"title\": \"Founding\"}, {\"date\": \"June 2023\", \"title\": \"FY23 Review\"}]")
        let merged = InsightPrompts.mergeTimeline([part, other])
        #expect(merged.events.map(\.title) == ["Founding", "FY23 review", "Launch", "Undated note"])
        #expect(merged.timeSpan == "2023 to 2024")
        #expect(InsightPrompts.mergeTimeline([other]).timeSpan == "2022 to 2023")
    }

    @Test func graphMergesNamesAndWeighsEdges() {
        let w1 = InsightPrompts.entities(from: """
        [{"name": "Chris Norman", "type": "Person", "context": "the CEO", "passages": [1, 2]},
         {"name": "Acme Corp", "type": "company", "context": "", "passages": [1]},
         {"name": "Boston", "type": "location", "passages": [2]}]
        """)
        let w2 = InsightPrompts.entities(from: "[{\"name\": \"Chris Norman's\", \"type\": \"person\", \"context\": \"\", \"passages\": [1]}, {\"name\": \"Acme Corp\", \"type\": \"company\", \"passages\": [1]}, {\"name\": \"X\", \"type\": \"concept\"}]")
        #expect(w1.count == 3 && w2.count == 2)      // one-letter names are dropped
        let graph = InsightPrompts.buildGraph(windows: [(w1, ["s1", "s2"]), (w2, ["s3"])], relations: [("chris norman", "ACME CORP", "leads")])
        #expect(graph.nodes.count == 3)
        let chris = graph.nodes.first { $0.id == "chrisnorman" }
        #expect(chris?.mentions == 3 && chris?.type == "person" && chris?.sourceIds == ["s1", "s2", "s3"])
        let edge = graph.edges.first { $0.a == "acmecorp" && $0.b == "chrisnorman" }
        #expect(edge?.weight == 2 && edge?.relation == "leads")
        #expect(graph.edges.first { $0.b == "chrisnorman" && $0.a == "boston" }?.weight == 1)
        #expect(graph.edges.count == 2)
    }

    @Test func claimsAndContradictions() {
        let claims = InsightPrompts.claims(from: "[{\"text\": \"Revenue grew 40% in 2023.\", \"type\": \"statistical\", \"passage\": 1}, {\"text\": \"short\", \"type\": \"factual\"}]", sources: [("s1", "a.pdf")])
        #expect(claims.count == 1 && claims[0].filename == "a.pdf")
        let b = InsightPrompts.Claim(text: "Revenue grew 25% in 2023.", type: "statistical", sourceId: "s2", filename: "b.pdf")
        let yes = InsightPrompts.contradiction(from: "{\"contradicts\": true, \"type\": \"statistical\", \"severity\": \"high\", \"explanation\": \"40 vs 25\", \"resolution_hint\": \"different fiscal years\"}", claims[0], b)
        #expect(yes?.severity == "high" && yes?.sourceB == "b.pdf" && yes?.resolutionHint == "different fiscal years")
        #expect(InsightPrompts.contradiction(from: "{\"contradicts\": false}", claims[0], b) == nil)
        #expect(InsightPrompts.contradiction(from: "{\"contradicts\": \"true\", \"severity\": \"huge\"}", claims[0], b)?.severity == "medium")
        #expect(InsightPrompts.contradictionPrompt(claims[0], b).contains("Claim 2 (from \"b.pdf\")"))
    }

    @Test func comparisonPerspectivesTopics() {
        let c = InsightPrompts.comparison(from: "{\"similarities\": [\"both cover Q3\"], \"differences\": [], \"unique_to_first\": [\"a\"], \"unique_to_second\": [\"b\", 3], \"synthesis\": \"Together…\"}",
                                          sourceA: "s1", titleA: "A", sourceB: "s2", titleB: "B", focus: nil)
        #expect(c?.similarities == ["both cover Q3"] && c?.uniqueToB == ["b", "3"] && c?.synthesis == "Together…")
        #expect(InsightPrompts.comparison(from: "nothing here", sourceA: "", titleA: "", sourceB: "", titleB: "", focus: nil) == nil)
        let p = InsightPrompts.perspective(from: "{\"take\": \"Says yes.\", \"claims\": [\"one\", \"two\", \"three\", \"four\"]}", sourceId: "s1", filename: "a.pdf")
        #expect(p?.claims.count == 3)
        #expect(InsightPrompts.perspective(from: "{\"take\": \"\", \"claims\": []}", sourceId: "s1", filename: "a.pdf") == nil)
        let cons = InsightPrompts.consensus(from: "{\"consensus\": [\"x\"], \"contested\": [\"y\"], \"synthesis\": \"z\"}")
        #expect(cons.consensus == ["x"] && cons.contested == ["y"] && cons.synthesis == "z")
        #expect(InsightPrompts.topicNames(from: "[{\"group\": 1, \"name\": \"Pricing\"}, {\"group\": 7, \"name\": \"out of range\"}, {\"group\": \"2\", \"name\": \"Hiring\"}]", count: 2) == [0: "Pricing", 1: "Hiring"])
    }

    @Test func clustersSeparateTwoSubjects() {
        // Two tight groups of unit vectors in 4 dimensions, with distinct vocabularies.
        func unit(_ v: [Float]) -> [Float] { let n = v.map { $0 * $0 }.reduce(0, +).squareRoot(); return v.map { $0 / n } }
        var items: [TextClusters.Item] = []
        for i in 0..<6 {
            items.append(.init(id: "a\(i)", sourceId: "s1", text: "pricing tiers discount revenue margin plan \(i)", vector: unit([1, 0.1 * Float(i), 0, 0])))
            items.append(.init(id: "b\(i)", sourceId: "s2", text: "hiring interview candidates offer onboarding team \(i)", vector: unit([0, 0, 1, 0.1 * Float(i)])))
        }
        let clusters = TextClusters.kMeans(items, k: 2)
        #expect(clusters.count == 2)
        for c in clusters {
            let ids = Set(c.members.map { items[$0].id.prefix(1) })
            #expect(ids.count == 1)
            #expect(c.keywords.count >= 5)
        }
        #expect(clusters.flatMap(\.keywords).contains("pricing") && clusters.flatMap(\.keywords).contains("hiring"))
        #expect(!clusters.flatMap(\.keywords).contains("the"))
        #expect(TextClusters.clusterCount(for: 40) == 3 && TextClusters.clusterCount(for: 4_000) == 12)
        #expect(TextClusters.kMeans(items, k: 2) == clusters)       // a rebuild repeats
    }

    @Test func writingPromptAndClean() {
        let p = WritingAssistant.prompt(text: "Our Q3 was fine.", task: .expand, format: .email, maxWords: 150, reference: ["Q3 revenue rose 12 %."])
        #expect(p.contains("Expand the text") && p.contains("as an email body") && p.contains("about 150 words") && p.contains("[1] Q3 revenue"))
        #expect(WritingAssistant.clean("```markdown\nHello there.\n```") == "Hello there.")
        #expect(WritingAssistant.clean("Here is the improved text:\n\nBetter.") == "Better.")
        #expect(WritingAssistant.wordCount("one two  three\nfour") == 4)
        #expect(WritingAssistant.Task.allCases.count == 8 && WritingAssistant.Format.allCases.count == 8)
    }

    @Test func storesRoundTrip() {
        let saved = AppFolders.base
        AppFolders.base = FileManager.default.temporaryDirectory.appending(path: "lb-insights-\(UUID().uuidString)", directoryHint: .isDirectory)
        defer { try? FileManager.default.removeItem(at: AppFolders.base); AppFolders.base = saved }
        let t = TimelineInsight(events: [.init(date: "2023", title: "A", description: "", importance: "high", sortKey: 2023)], timeSpan: "2023", context: "")
        let item = Insight(notebookId: "nb", kind: .timeline, title: "Timeline", body: Insight.encode(t))
        InsightStore.save(item)
        #expect(InsightStore.all(notebook: "nb").first?.decode(TimelineInsight.self) == t)
        #expect(InsightStore.all(notebook: "other").isEmpty)
        InsightStore.delete(item)
        #expect(InsightStore.all(notebook: nil).isEmpty)
        FindingsStore.save(Finding(notebookId: "nb", kind: .answer, title: "x", text: "y", tags: ["b", "a"]))
        FindingsStore.save(Finding(notebookId: "nb", kind: .note, title: "z", text: "w", tags: ["a"], starred: true))
        let all = FindingsStore.all(notebook: "nb")
        #expect(all.count == 2 && all[0].starred)
        #expect(FindingsStore.tags(notebook: "nb") == ["a", "b"])
    }
}
