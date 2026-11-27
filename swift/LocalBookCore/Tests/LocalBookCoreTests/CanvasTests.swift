import Foundation
import Testing
@testable import LocalBookCore

@Suite struct CanvasTests {
    private func turn(_ q: String, _ a: String, sources: [String] = ["s1"], id: String? = nil) -> JourneyTurn {
        JourneyTurn(id: id ?? q, notebookId: "nb", question: q, answer: a, sourcesUsed: sources, filenames: sources.map { $0 + ".pdf" })
    }

    @Test func learningFilter() {
        #expect(CanvasPopulate.isLearning("Who founded Harbor Lights?"))
        #expect(CanvasPopulate.isLearning("@research the history of coffee roasting"))
        #expect(CanvasPopulate.isLearning("@curator what is the status of the merger?"))
        #expect(!CanvasPopulate.isLearning("@curator brain status"))
        #expect(!CanvasPopulate.isLearning("@collector show pending"))
        #expect(!CanvasPopulate.isLearning("@correspondent sync now"))
        #expect(!CanvasPopulate.isLearning("status"))
        #expect(!CanvasPopulate.isLearning("   "))
    }

    @Test func gapsAndConfidence() {
        let miss = turn("Who is the CFO?", "I couldn't find that in your documents.")
        let hit = turn("Where is HQ?", "Harbor Lights moved its headquarters to Austin in 2017 [1].")
        #expect(miss.confidence == 0.3 && hit.confidence == 0.7)
        let gaps = CanvasGaps.find([miss, hit, miss, turn("@curator brain status", "no information")])
        #expect(gaps.count == 1 && gaps[0].reason == "the answer wasn't in your sources")
    }

    @Test func buildsMergesAndKeepsPlaces() {
        let turns = [turn("Who founded it?", "Rosa Alvarez founded it in 2014 [1].", sources: ["s1", "s2"]), turn("@collector status", "ok"), turn("Where is HQ?", "Austin [1].", sources: ["s2"])]
        let sources = [CanvasPopulate.SourceRef(id: "s1", title: "history.md"), CanvasPopulate.SourceRef(id: "s2", title: "press.md"), CanvasPopulate.SourceRef(id: "s9", title: "unused.md")]
        let art = [CanvasPopulate.ArtifactRef(id: "q1", kind: "quiz", title: "Quiz", preview: "5 questions", created: Date(), studioKind: "quiz")]
        let built = CanvasPopulate.buildNodes(turns: turns, sources: sources, artifacts: art)
        #expect(built.count == 5)                                  // 2 questions (admin skipped), 2 referenced sources (s9 off the map), 1 artifact
        #expect(built.contains { $0.node.id == "source:s2" } && !built.contains { $0.node.id == "source:s9" })
        var layout = CanvasLayout(nodes: CanvasPopulate.seedLayout(built))
        #expect(layout.nodes.count == 5)
        let xs = Set(layout.nodes.map(\.x)); #expect(xs.count >= 2)    // more than one column
        // The user moves a chip; a second populate with a new question keeps it there and adds the new one below.
        layout.move("exploration_query:Who founded it?", x: 999, y: 777)
        let more = turns + [turn("What do they ship?", "Beans [1].", sources: ["s1"])]
        let merged = CanvasPopulate.merge(layout, with: CanvasPopulate.buildNodes(turns: more, sources: sources, artifacts: art))
        #expect(merged.node("exploration_query:Who founded it?")?.x == 999)
        #expect(merged.nodes.count == 6)
        let fresh = merged.node("exploration_query:What do they ship?")!
        #expect(fresh.y > 777)
        // A source that disappears takes its node and edges with it; a note stays.
        var withNote = merged
        withNote.nodes.append(CanvasNode(id: "note:1", kind: CanvasNode.noteKind, title: "mine", snapshot: .init(payload: "mine")))
        withNote.upsert(CanvasEdge(source: "note:1", target: "source:s2", state: "user"))
        let shrunk = CanvasPopulate.merge(withNote, with: CanvasPopulate.buildNodes(turns: more, sources: [sources[0]], artifacts: art))
        #expect(shrunk.node("source:s2") == nil && shrunk.node("note:1") != nil && shrunk.edges.isEmpty)
    }

    @Test func topicsAreAccretive() {
        func v(_ a: Float, _ b: Float) -> [Float] { VectorMath.normalized([a, b, 0.1]) }
        let existing = CanvasTopic(id: "T", title: "Shipping", centroid: v(1, 0), members: 2)
        let nodes: [(id: String, vector: [Float], topicId: String?)] = [
            ("a", v(0.95, 0.1), nil),          // joins T
            ("b", v(0, 1), nil), ("c", v(0.05, 1), nil),   // seed a new card together
            ("d", v(-1, -1), nil),             // alone: orphan
            ("e", v(0, 1), "T"),               // already on T: stays
            ("f", [], nil)]                    // no vector: left alone
        let r = CanvasPopulate.assignTopics(nodes: nodes, topics: [existing])
        #expect(r.assigned["a"] == "T" && r.assigned["e"] == "T")
        #expect(r.newTopics.count == 1 && Set(r.newTopics[0].members) == ["b", "c"])
        #expect(r.assigned["d"] == nil && r.assigned["f"] == nil)
        #expect(r.newTopics[0].centroid.count == 3)
    }

    @Test func arrangePutsThreadsInTheirCard() {
        var layout = CanvasLayout()
        layout.topics = [CanvasTopic(id: "T", title: "Card", centroid: [], members: 4, createdAt: "2026-01-01")]
        layout.nodes.append(CanvasNode(id: "T", kind: CanvasNode.topicKind, title: "Card", snapshot: .init(payload: "Card"), createdAt: "2026-01-01"))
        for i in 0..<4 { layout.nodes.append(CanvasNode(id: "q\(i)", kind: CanvasNode.chatKind, title: "q\(i)", snapshot: .init(payload: "q"), x: 5000, y: 5000, topicId: "T", createdAt: "2026-01-0\(i + 2)")) }
        layout.nodes.append(CanvasNode(id: "orphan", kind: CanvasNode.chatKind, title: "o", snapshot: .init(payload: "o")))
        layout.nodes.append(CanvasNode(id: "source:s", kind: CanvasNode.sourceKind, refId: "s", title: "s", snapshot: .init(payload: "s")))
        let out = CanvasPopulate.arrange(layout)
        let card = out.node("T")!
        for i in 0..<4 {
            let n = out.node("q\(i)")!
            #expect(n.x >= card.x && n.x + CanvasPopulate.chipWidth <= card.x + card.width! + 0.01)
            #expect(n.y >= card.y && n.y + CanvasPopulate.chipHeight <= card.y + card.height! + 0.01)
        }
        #expect(out.node("orphan")!.y > card.y + card.height!)
        #expect(out.node("source:s")!.y > out.node("orphan")!.y)
    }

    @Test func candidatesAreBounded() {
        func input(_ id: String, _ v: [Float], _ s: Set<String>, _ c: Set<String> = []) -> CanvasCandidates.Input { .init(id: id, vector: VectorMath.normalized(v), sourceIds: s, concepts: c) }
        let a = input("a", [1, 0, 0], ["s1"], ["rosa", "austin"])
        let b = input("b", [0.98, 0.1, 0], ["s1"], ["rosa", "austin", "denver"])
        let c = input("c", [0, 1, 0], ["s7"], [])
        let found = CanvasCandidates.compute([a, b, c], existing: [])
        #expect(found.count == 1 && Set([found[0].a, found[0].b]) == ["a", "b"])
        #expect(found[0].score > 0.65)
        #expect(found[0].signal == "embed" || found[0].signal == "concept")
        // Already joined by hand: no candidate.
        #expect(CanvasCandidates.compute([a, b, c], existing: [CanvasEdge(source: "b", target: "a", state: "user")]).isEmpty)
        // Per-node cap of three.
        var many = [a]
        for i in 0..<6 { many.append(input("n\(i)", [1, Float(i) * 0.01, 0], ["s1"], ["rosa", "austin"])) }
        let capped = CanvasCandidates.compute(many, existing: [])
        #expect(capped.filter { $0.a == "a" || $0.b == "a" }.count <= 3 && capped.count <= 12)
        #expect(CanvasCandidates.overlap(["x", "y"], ["y"]) == 1 && CanvasCandidates.overlap([], ["y"]) == 0)
    }

    @Test func tensionAndProvenanceEdges() {
        let nodes = [CanvasNode(id: "source:s1", kind: CanvasNode.sourceKind, refId: "s1", title: "a", snapshot: .init(payload: "")),
                     CanvasNode(id: "source:s2", kind: CanvasNode.sourceKind, refId: "s2", title: "b", snapshot: .init(payload: "")),
                     CanvasNode(id: "exploration_query:q", kind: CanvasNode.chatKind, refType: "exploration_query", refId: "q", title: "q", snapshot: .init(payload: "q", metadata: .init(sourceIds: ["s1", "s2"])))]
        let items = [ContradictionsInsight.Item(claimA: "63 employees", sourceA: "s1", claimB: "71 employees", sourceB: "s2", type: "numeric", severity: "high", explanation: "counts differ", resolutionHint: nil),
                     ContradictionsInsight.Item(claimA: "x", sourceA: "s1", claimB: "y", sourceB: "s2", type: "date", severity: "low", explanation: "", resolutionHint: nil, dismissed: true),
                     ContradictionsInsight.Item(claimA: "x", sourceA: "s1", claimB: "z", sourceB: "s3", type: "date", severity: "low", explanation: "", resolutionHint: nil)]
        let tension = CanvasTension.edges(from: ContradictionsInsight(items: items, claimsChecked: 3, pairsChecked: 2), nodes: nodes)
        #expect(tension.count == 1 && tension[0].state == "tension" && tension[0].meta["severity"] == "high" && tension[0].label == "numeric")
        let prov = CanvasProvenance.edges(for: CanvasLayout(nodes: nodes))
        #expect(prov.count == 2 && prov.allSatisfy { $0.state == "provenance" && $0.target == "exploration_query:q" })
    }

    @Test func recallSchedule() {
        let now = Date(timeIntervalSince1970: 1_700_000_000)
        var s = CanvasRecall.graded(nil, .good, now: now)
        #expect(s.interval == 1 && s.repetitions == 1 && s.nextDue == now.addingTimeInterval(86_400))
        s = CanvasRecall.graded(s, .good, now: now)
        #expect(s.interval == 6)
        s = CanvasRecall.graded(s, .easy, now: now)
        #expect(s.interval > 6 && s.ease > 2.5)
        s = CanvasRecall.graded(s, .again, now: now)
        #expect(s.interval == 1 && s.repetitions == 0 && s.ease < 2.6)
        var layout = CanvasLayout(nodes: [CanvasNode(id: "q", kind: CanvasNode.chatKind, title: "q", snapshot: .init(payload: "q")),
                                          CanvasNode(id: "source:s", kind: CanvasNode.sourceKind, title: "s", snapshot: .init(payload: "s"))])
        #expect(CanvasRecall.due(layout, now: now).map(\.id) == ["q"])            // never reviewed: due; sources are not reviewed
        layout.recall["q"] = CanvasRecall.graded(nil, .easy, now: now)
        #expect(CanvasRecall.due(layout, now: now).isEmpty)
    }

    @Test func oldAppLayoutOpens() throws {
        // The shape the old app's GET /canvas/layout returned, snake_case and all.
        let json = """
        {"nodes":[{"id":"exploration_query:abc","x":340.0,"y":200.0,"kind":"chat_turn","ref_type":"exploration_query","ref_id":"abc",
                   "snapshot":{"id":"x","type":"markdown","payload":"**Q:** Who?\\n\\nRosa.","metadata":{"sources":2,"answered":true,"preview":"Rosa.","topics":["a.pdf"]}},
                   "title":"Who?","z":0,"width":null,"height":null,"topic_id":null,"parent_id":null,"intent":null,"created_at":"2026-08-01T10:00:00","updated_at":"2026-08-01T10:00:00"},
                  {"id":"source:s1","x":0,"y":0,"kind":"source","ref_type":"source","ref_id":"s1","snapshot":{"type":"markdown","payload":"**Source:** a.pdf"},"title":"a.pdf","z":0,"created_at":"2026-08-01T10:00:00","updated_at":"2026-08-01T10:00:00"}],
         "edges":[{"id":"e1","source":"source:s1","target":"exploration_query:abc","state":"user","label":"","meta":{"score":0.8,"why":"hand"},"created_at":"2026-08-01T10:00:00"}],
         "viewport":{"x":10.0,"y":-20.0,"zoom":0.8}}
        """
        let layout = try #require(CanvasStore.decode(Data(json.utf8)))
        #expect(layout.nodes.count == 2 && layout.edges.count == 1 && layout.viewport.zoom == 0.8)
        #expect(layout.node("exploration_query:abc")?.snapshot.metadata?.preview == "Rosa.")
        #expect(layout.edges[0].meta["score"] == "0.8" && layout.edges[0].meta["why"] == "hand")
        #expect(layout.node("source:s1")?.sourceIds == ["s1"])
        // Round trip through this app's writer keeps the keys the old app used.
        let again = try #require(CanvasStore.decode(CanvasStore.encode(layout)))
        #expect(again == layout)
        let text = String(decoding: CanvasStore.encode(layout), as: UTF8.self)
        #expect(text.contains("\"ref_type\"") && text.contains("\"created_at\"") && text.contains("\"viewport\""))
    }

    @Test func journeyTurnsRoundTrip() throws {
        let t = JourneyTurn(id: "t1", notebookId: "nb", question: "Who?", answer: "Rosa [1].", sourcesUsed: ["s1"], filenames: ["a.pdf"], timestamp: Date(timeIntervalSince1970: 1_700_000_000))
        let e = JSONEncoder(); e.dateEncodingStrategy = .iso8601
        let d = JSONDecoder(); d.dateDecodingStrategy = .iso8601
        let back = try d.decode(JourneyTurn.self, from: try e.encode(t))
        #expect(back == t)
        let text = String(decoding: try e.encode(t), as: UTF8.self)
        #expect(text.contains("\"query\"") && text.contains("\"answer_preview\"") && text.contains("\"sources_used\""))
    }
}

@Suite struct ConstellationTests {
    private func graph() -> GraphInsight {
        func n(_ id: String, _ type: String, _ m: Int, _ s: [String]) -> GraphInsight.Node { .init(id: id, name: id.capitalized, type: type, mentions: m, context: "", sourceIds: s) }
        let nodes = [n("rosa", "person", 12, ["s1", "s2"]), n("harbor", "company", 10, ["s1", "s2", "s3"]), n("austin", "location", 4, ["s1"]), n("daniel", "person", 3, ["s2"]),
                     n("kokoro", "product", 5, ["s4"]), n("tokyo", "location", 2, ["s4"]), n("yuki", "person", 2, ["s4"]), n("lonely", "concept", 1, ["s9"])]
        let edges = [GraphInsight.Edge(a: "rosa", b: "harbor", relation: "founded", weight: 6), .init(a: "rosa", b: "austin", relation: "", weight: 2), .init(a: "harbor", b: "austin", relation: "", weight: 3), .init(a: "rosa", b: "daniel", relation: "hired", weight: 2),
                     .init(a: "kokoro", b: "tokyo", relation: "", weight: 3), .init(a: "kokoro", b: "yuki", relation: "made by", weight: 2), .init(a: "tokyo", b: "yuki", relation: "", weight: 1)]
        return GraphInsight(nodes: nodes, edges: edges)
    }

    @Test func communitiesSplitTheTwoStories() {
        let groups = Constellation.communities(graph())
        let of = Dictionary(uniqueKeysWithValues: groups.enumerated().flatMap { gi, g in g.map { ($0, gi) } })
        #expect(of["rosa"] == of["harbor"] && of["harbor"] == of["austin"] && of["rosa"] == of["daniel"])
        #expect(of["kokoro"] == of["tokyo"] && of["tokyo"] == of["yuki"])
        #expect(of["rosa"] != of["kokoro"])
        #expect(of["lonely"] != nil)
        #expect(groups == Constellation.communities(graph()))             // deterministic
    }

    @Test func sceneHasHubsAndFilters() {
        let scene = Constellation.scene(graph())
        #expect(scene.stars.count == 8 && scene.links.count == 7)
        let hubs = scene.stars.filter(\.isHub)
        #expect(hubs.contains { $0.id == "rosa" || $0.id == "harbor" } && hubs.contains { $0.id == "kokoro" })
        let rosa = scene.star("rosa")!, daniel = scene.star("daniel")!
        #expect(rosa.size > daniel.size)
        #expect(scene.neighbours(of: "rosa") == ["harbor", "austin", "daniel"])
        // Stars of different clusters sit apart.
        let k = scene.star("kokoro")!
        let d = ((rosa.x - k.x) * (rosa.x - k.x) + (rosa.y - k.y) * (rosa.y - k.y) + (rosa.z - k.z) * (rosa.z - k.z)).squareRoot()
        #expect(d > 30)
        let only = Constellation.scene(graph(), limitTo: "s4")
        #expect(Set(only.stars.map(\.id)) == ["kokoro", "tokyo", "yuki"] && only.links.count == 3)
        #expect(scene.clusters.allSatisfy { $0.hue >= 0 && $0.hue < 1 })
    }
}
