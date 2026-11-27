import AppKit
import LocalBookCore
import LocalBookEngines
import SceneKit
import SwiftUI

/// The Constellation: the notebook's knowledge graph as a star map in SceneKit. Each named thing is a star, sized by
/// how connected it is; clusters (communities of the graph) have a colour and a patch of sky; the strongest star of a
/// cluster sits at its centre. Click a star to read what it is, what it is connected to and which sources name it;
/// pick a source to see only its stars. Drag to orbit, scroll to zoom.
struct ConstellationPane: View {
    @Environment(AppModel.self) private var model
    @Environment(\.openWindow) private var openWindow
    @State private var engine = InsightEngine()
    @State private var notebookId: String?
    @State private var graphItem: Insight?
    @State private var scene = Constellation.Scene(stars: [], links: [], clusters: [])
    @State private var picked: String?
    @State private var focusCluster: Int?
    @State private var sourceFilter: String?
    @State private var sources: [LibraryStore.SourceInfo] = []
    @State private var showLabels = true

    private var library: LibraryModel { model.library }
    private var notebook: LibraryNotebookInfo? { library.notebooks.first { $0.id == notebookId } }
    private var graph: GraphInsight? { graphItem?.decode(GraphInsight.self) }

    var body: some View {
        HSplitView {
            VStack(spacing: 0) {
                toolbar
                Divider()
                if scene.stars.isEmpty { empty }
                else {
                    ConstellationSceneView(scene: scene, picked: $picked, focusCluster: focusCluster, showLabels: showLabels)
                }
            }
            .frame(minWidth: 520, maxWidth: .infinity, maxHeight: .infinity)
            inspector.frame(minWidth: 260, idealWidth: 300, maxWidth: 380)
        }
        .onAppear { if notebookId == nil || notebook == nil { notebookId = library.scopeToNotebook ?? library.notebooks.first?.id } }
        .onChange(of: library.scopeToNotebook) { _, id in if let id { notebookId = id } }
        .onChange(of: notebookId, initial: true) { _, _ in reload() }
        .onChange(of: sourceFilter) { _, _ in rebuild() }
    }

    private func reload() {
        guard let notebookId else { return }
        graphItem = InsightStore.all(notebook: notebookId).first { $0.kind == .graph }
        picked = nil; focusCluster = nil; sourceFilter = nil
        Task { if let store = library.store { sources = await store.sources(notebook: notebookId) } }
        rebuild()
    }
    private func rebuild() {
        scene = graph.map { Constellation.scene($0, limitTo: sourceFilter) } ?? Constellation.Scene(stars: [], links: [], clusters: [])
        if let picked, scene.star(picked) == nil { self.picked = nil }
    }
    private func build() {
        guard let notebook else { return }
        Task {
            if let made = await engine.makeGraph(in: notebook, library: library) { graphItem = made; rebuild() }
        }
    }

    private var toolbar: some View {
        HStack(spacing: 10) {
            Button { build() } label: { Label(graphItem == nil ? "Build the constellation" : "Rebuild", systemImage: "sparkles") }.disabled(engine.working || notebook == nil)
            if let g = graphItem { Text("\(scene.stars.count) stars · \(scene.links.count) links · \(scene.clusters.count) clusters · made \(g.created.formatted(date: .abbreviated, time: .shortened))").font(.caption).foregroundStyle(.secondary).lineLimit(1) }
            Spacer()
            if engine.working { ProgressView().controlSize(.small); Text(engine.status).font(.caption).foregroundStyle(.secondary).lineLimit(1) }
            else if let e = engine.error { Text(e).font(.caption).foregroundStyle(.red).lineLimit(1) }
            Toggle(isOn: $showLabels) { Label("Names", systemImage: "textformat") }.toggleStyle(.button)
            Picker("Source", selection: $sourceFilter) {
                Text("All sources").tag(String?.none)
                ForEach(sources) { s in Text(s.name).tag(String?.some(s.id)) }
            }
            .frame(maxWidth: 220)
        }
        .controlSize(.small)
        .padding(.horizontal, 12).padding(.vertical, 8)
    }

    private var empty: some View {
        VStack(spacing: 10) {
            Image(systemName: "sparkles").font(.system(size: 36)).foregroundStyle(.secondary)
            Text(graphItem == nil ? "No constellation yet" : "Nothing to show").font(.title3)
            Text(graphItem == nil ? "Build it from the people, places and things named in \(notebook?.title ?? "this notebook"). It is the knowledge graph from Insights, drawn as a sky."
                                   : "No star of this graph comes from that source.").font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 440)
            if graphItem == nil { Button("Build the constellation") { build() }.disabled(engine.working || notebook == nil) }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.black.opacity(0.85))
    }

    private var inspector: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                if let id = picked, let star = scene.star(id) { starDetail(star) }
                else {
                    Text(notebook?.title ?? "Constellation").font(.headline)
                    Text("Click a star to read about it. Clusters:").font(.caption).foregroundStyle(.secondary)
                    ForEach(scene.clusters) { c in
                        Button {
                            focusCluster = focusCluster == c.id ? nil : c.id
                        } label: {
                            HStack(spacing: 8) {
                                Circle().fill(Color(hue: c.hue, saturation: 0.65, brightness: 0.95)).frame(width: 10, height: 10)
                                Text(c.name).font(.callout).lineLimit(1)
                                Spacer()
                                Text("\(c.members.count)").font(.caption).foregroundStyle(.secondary)
                            }
                            .padding(.vertical, 3).padding(.horizontal, 6)
                            .background(RoundedRectangle(cornerRadius: 6).fill(focusCluster == c.id ? Color.accentColor.opacity(0.15) : .clear))
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
            .padding(12)
        }
        .background(Color(nsColor: .windowBackgroundColor))
    }

    @ViewBuilder private func starDetail(_ s: Constellation.Star) -> some View {
        HStack {
            Circle().fill(Color(hue: scene.clusters.first { $0.id == s.cluster }?.hue ?? 0, saturation: 0.65, brightness: 0.95)).frame(width: 12, height: 12)
            Text(s.type.capitalized).font(.caption).foregroundStyle(.secondary)
            Spacer()
            Button { picked = nil } label: { Image(systemName: "xmark.circle") }.buttonStyle(.borderless)
        }
        Text(s.name).font(.title3.weight(.semibold))
        if !s.context.isEmpty { Text(s.context).font(.callout) }
        Text("\(s.mentions) mention\(s.mentions == 1 ? "" : "s") · \(s.connections) connection\(s.connections == 1 ? "" : "s") · in “\(scene.clusters.first { $0.id == s.cluster }?.name ?? "")”").font(.caption).foregroundStyle(.secondary)
        let links = scene.links.filter { $0.a == s.id || $0.b == s.id }.sorted { $0.weight > $1.weight }
        if !links.isEmpty {
            Text("Connected to").font(.subheadline.weight(.semibold))
            ForEach(links) { l in
                let other = l.a == s.id ? l.b : l.a
                Button { picked = other } label: {
                    HStack {
                        Text(scene.star(other)?.name ?? other).font(.callout).lineLimit(1)
                        Spacer()
                        Text(l.relation.isEmpty ? "×\(l.weight)" : l.relation).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                    }
                }
                .buttonStyle(.plain)
            }
        }
        let named = sources.filter { s.sourceIds.contains($0.id) }
        if !named.isEmpty {
            Text("Named in").font(.subheadline.weight(.semibold))
            ForEach(named) { src in
                Button { if let notebookId { openWindow(id: "source", value: SourceWindowKey(notebookId: notebookId, sourceId: src.id, name: src.name)) } } label: {
                    Label(src.name, systemImage: "doc").font(.callout).lineLimit(1)
                }
                .buttonStyle(.plain)
            }
        }
        HStack {
            Button("Ask about this") { if let notebookId { library.query = "What do my sources say about \(s.name)?"; model.selection = .notebook(notebookId) } }
            Button("Keep as finding") {
                guard let notebookId else { return }
                FindingsStore.save(Finding(notebookId: notebookId, kind: .highlight, title: s.name, text: s.context.isEmpty ? "\(s.name) (\(s.type))" : s.context, refs: s.sourceIds))
                NotificationCenter.default.post(name: .findingsChanged, object: nil)
            }
        }
        .controlSize(.small)
    }
}

/// The SceneKit view: stars as spheres, links as lines, names as billboarded text, a field of faint stars behind.
struct ConstellationSceneView: NSViewRepresentable {
    let scene: Constellation.Scene
    @Binding var picked: String?
    let focusCluster: Int?
    let showLabels: Bool

    func makeCoordinator() -> Coordinator { Coordinator(picked: $picked) }

    func makeNSView(context: Context) -> SCNView {
        let view = SCNView()
        view.backgroundColor = NSColor(calibratedRed: 0.04, green: 0.04, blue: 0.08, alpha: 1)
        view.allowsCameraControl = true
        view.autoenablesDefaultLighting = false
        view.antialiasingMode = .multisampling4X
        view.scene = SCNScene()
        let click = NSClickGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.click(_:)))
        view.addGestureRecognizer(click)
        context.coordinator.view = view
        context.coordinator.rebuild(scene, labels: showLabels)
        context.coordinator.style(picked: picked, focus: focusCluster, in: scene)
        return view
    }

    func updateNSView(_ view: SCNView, context: Context) {
        if context.coordinator.built != scene || context.coordinator.labels != showLabels { context.coordinator.rebuild(scene, labels: showLabels) }
        context.coordinator.style(picked: picked, focus: focusCluster, in: scene)
    }

    @MainActor final class Coordinator: NSObject {
        var picked: Binding<String?>
        weak var view: SCNView?
        var built: Constellation.Scene?
        var labels = true
        private var starNodes: [String: SCNNode] = [:]
        private var labelNodes: [String: SCNNode] = [:]
        private var linkNodes: [(a: String, b: String, node: SCNNode)] = []
        private var root = SCNNode()

        init(picked: Binding<String?>) { self.picked = picked }

        func rebuild(_ scene: Constellation.Scene, labels: Bool) {
            guard let view, let scn = view.scene else { return }
            built = scene; self.labels = labels
            root.removeFromParentNode()
            root = SCNNode()
            starNodes = [:]; labelNodes = [:]; linkNodes = []
            scn.rootNode.addChildNode(root)

            // Sky: a few hundred faint points far away.
            var pts: [SCNVector3] = []
            var seed: UInt64 = 7
            func next() -> Float { seed = seed &* 6364136223846793005 &+ 1442695040888963407; return Float(seed >> 33) / Float(UInt32.max) }
            for _ in 0..<600 {
                let u = next() * 2 - 1, t = next() * 2 * .pi, r: Float = 320 + next() * 80
                let s = (1 - u * u).squareRoot()
                pts.append(SCNVector3(r * s * cos(t), r * u, r * s * sin(t)))
            }
            let src = SCNGeometrySource(vertices: pts)
            let idx = (0..<pts.count).map { UInt32($0) }
            let el = SCNGeometryElement(indices: idx, primitiveType: .point)
            el.pointSize = 1.5; el.minimumPointScreenSpaceRadius = 0.6; el.maximumPointScreenSpaceRadius = 1.6
            let sky = SCNGeometry(sources: [src], elements: [el])
            sky.firstMaterial?.diffuse.contents = NSColor(white: 0.85, alpha: 0.55)
            sky.firstMaterial?.lightingModel = .constant
            root.addChildNode(SCNNode(geometry: sky))

            let ambient = SCNNode(); ambient.light = SCNLight(); ambient.light?.type = .ambient; ambient.light?.intensity = 350; ambient.light?.color = NSColor.white
            root.addChildNode(ambient)
            let omni = SCNNode(); omni.light = SCNLight(); omni.light?.type = .omni; omni.light?.intensity = 900; omni.light?.color = NSColor(calibratedRed: 0.75, green: 0.65, blue: 1, alpha: 1)
            omni.position = SCNVector3(60, 80, 120)
            root.addChildNode(omni)

            let hues = Dictionary(uniqueKeysWithValues: scene.clusters.map { ($0.id, $0.hue) })
            for s in scene.stars {
                let sphere = SCNSphere(radius: CGFloat(s.size))
                sphere.segmentCount = s.isHub ? 32 : 14
                let color = NSColor(hue: hues[s.cluster] ?? 0, saturation: 0.6, brightness: 0.95, alpha: 1)
                sphere.firstMaterial?.diffuse.contents = color
                sphere.firstMaterial?.emission.contents = color.withAlphaComponent(0.35)
                let node = SCNNode(geometry: sphere)
                node.position = SCNVector3(s.x, s.y, s.z)
                node.name = s.id
                root.addChildNode(node)
                starNodes[s.id] = node

                let text = SCNText(string: s.name, extrusionDepth: 0)
                text.font = NSFont.systemFont(ofSize: s.isHub ? 5.5 : 3.6, weight: s.isHub ? .semibold : .regular)
                text.flatness = 0.3
                text.firstMaterial?.diffuse.contents = NSColor(white: 0.95, alpha: s.isHub ? 1 : 0.85)
                text.firstMaterial?.lightingModel = .constant
                let label = SCNNode(geometry: text)
                let (minB, maxB) = text.boundingBox
                label.pivot = SCNMatrix4MakeTranslation((maxB.x - minB.x) / 2, 0, 0)
                label.position = SCNVector3(s.x, s.y - s.size - 5, s.z)
                label.constraints = [SCNBillboardConstraint()]
                label.isHidden = !labels
                root.addChildNode(label)
                labelNodes[s.id] = label
            }
            let stars = Dictionary(uniqueKeysWithValues: scene.stars.map { ($0.id, $0) })
            for l in scene.links {
                guard let a = stars[l.a], let b = stars[l.b] else { continue }
                let v = [SCNVector3(a.x, a.y, a.z), SCNVector3(b.x, b.y, b.z)]
                let g = SCNGeometry(sources: [SCNGeometrySource(vertices: v)], elements: [SCNGeometryElement(indices: [UInt32(0), UInt32(1)], primitiveType: .line)])
                g.firstMaterial?.diffuse.contents = NSColor(white: 0.7, alpha: min(0.75, 0.25 + 0.1 * Double(l.weight)))
                g.firstMaterial?.lightingModel = .constant
                let node = SCNNode(geometry: g)
                root.addChildNode(node)
                linkNodes.append((l.a, l.b, node))
            }
            let camera = SCNNode(); camera.camera = SCNCamera(); camera.camera?.zFar = 1_000; camera.camera?.fieldOfView = 60
            camera.position = SCNVector3(0, 20, 230)
            camera.look(at: SCNVector3Zero)
            root.addChildNode(camera)
            view.pointOfView = camera
        }

        /// Dims what is not the picked star and its neighbours (or not the focused cluster).
        func style(picked: String?, focus: Int?, in scene: Constellation.Scene) {
            let neighbours = picked.map { scene.neighbours(of: $0) } ?? []
            let members = focus.flatMap { f in scene.clusters.first { $0.id == f } }.map { Set($0.members) }
            for s in scene.stars {
                let lit: Bool = {
                    if let picked { return s.id == picked || neighbours.contains(s.id) }
                    if let members { return members.contains(s.id) }
                    return true
                }()
                starNodes[s.id]?.opacity = lit ? 1 : 0.18
                labelNodes[s.id]?.opacity = lit ? 1 : 0.12
                labelNodes[s.id]?.isHidden = !labels && !(picked == s.id || neighbours.contains(s.id))
                if s.id == picked { starNodes[s.id]?.scale = SCNVector3(1.5, 1.5, 1.5) } else { starNodes[s.id]?.scale = SCNVector3(1, 1, 1) }
            }
            for l in linkNodes {
                let lit: Bool = {
                    if let picked { return l.a == picked || l.b == picked }
                    if let members { return members.contains(l.a) && members.contains(l.b) }
                    return true
                }()
                l.node.opacity = lit ? 1 : 0.1
            }
        }

        @objc func click(_ g: NSClickGestureRecognizer) {
            guard let view else { return }
            let p = g.location(in: view)
            let hits = view.hitTest(p, options: [.searchMode: SCNHitTestSearchMode.all.rawValue as NSNumber, .ignoreHiddenNodes: true])
            if let hit = hits.first(where: { $0.node.name != nil && starNodes[$0.node.name!] != nil }) { picked.wrappedValue = hit.node.name }
            else { picked.wrappedValue = nil }
        }
    }
}
