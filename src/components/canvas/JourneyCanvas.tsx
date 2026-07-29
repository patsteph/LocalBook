/**
 * JourneyCanvas — the interactive Journey Canvas shell (P3).
 *
 * A per-notebook, spatial-temporal learning map built on react-flow
 * (`@xyflow/react` v12). Nodes carry an Artifact snapshot rendered through the
 * canonical `<ArtifactRender>` registry; edges express the five journey-canvas
 * relationship states with a distinct visual language + a subtle recency tint.
 *
 * This is the SHELL: it renders + persists whatever layout the backend owns
 * (move, resize, delete, user-drawn edges, viewport, populate). The candidate
 * *engine* (computing proposed connections) is P5/backend — here we only
 * render/style candidate edges that already exist and support the user's own
 * draw gesture.
 *
 * Scope note: only `src/components/canvas/` + `src/services/canvas.ts` +
 * the one-line RenderContext add + the CanvasPanel toggle are touched.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  NodeResizer,
  Handle,
  Position,
  MarkerType,
  useNodesState,
  useEdgesState,
  useReactFlow,
  type Node,
  type Edge,
  type NodeProps,
  type NodeTypes,
  type Connection,
  type Viewport,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Trash2, Sparkles, RefreshCw } from 'lucide-react';
import { ArtifactRender } from '../artifact/RendererRegistry';
import {
  canvasService,
  type CanvasLayout,
  type CanvasNode,
  type CanvasEdge,
  type EdgeState,
} from '../../services/canvas';

// ─── Edge visual language (from the journey-canvas spec) ─────────────────────
interface EdgeVisual {
  stroke: string;
  width: number;
  dash?: string;
  animated?: boolean;
}
const EDGE_VISUAL: Record<EdgeState, EdgeVisual> = {
  candidate: { stroke: '#f59e0b', width: 1.5, dash: '4 4', animated: true }, // amber
  provenance: { stroke: '#06b6d4', width: 1.5 },                              // aqua
  user: { stroke: '#8b5cf6', width: 2.75 },                                   // violet, bold
  curator: { stroke: '#c4b5fd', width: 1.5, dash: '6 4' },                    // lavender, dashed
  researched: { stroke: '#f43f5e', width: 2 },                                // rose
};

const EDGE_LEGEND: { state: EdgeState; label: string }[] = [
  { state: 'candidate', label: 'Candidate' },
  { state: 'provenance', label: 'Made-from' },
  { state: 'user', label: 'Yours' },
  { state: 'curator', label: 'Curator' },
  { state: 'researched', label: 'Researched' },
];

// Recency tint: newer edges/nodes read stronger; older ones fade toward 0.4.
function recencyOpacity(createdAt: string | undefined): number {
  if (!createdAt) return 1;
  const t = Date.parse(createdAt);
  if (Number.isNaN(t)) return 1;
  const ageDays = (Date.now() - t) / 86_400_000;
  const o = 1 - (ageDays / 45) * 0.6;
  return Math.max(0.4, Math.min(1, o));
}

// ─── Custom node ─────────────────────────────────────────────────────────────
type ArtifactNodeData = {
  node: CanvasNode;
  tint: number;
};
type ArtifactFlowNode = Node<ArtifactNodeData, 'artifact'>;

function ArtifactNode({ id, data, selected }: NodeProps<ArtifactFlowNode>) {
  const rf = useReactFlow();
  const { node, tint } = data;

  return (
    <div
      className="flex h-full w-full flex-col overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm dark:border-gray-700 dark:bg-gray-800"
      style={{ opacity: tint }}
    >
      <NodeResizer
        minWidth={200}
        minHeight={120}
        isVisible={!!selected}
        lineClassName="!border-violet-400"
        handleClassName="!h-2.5 !w-2.5 !rounded-sm !border-violet-500 !bg-white"
      />
      {/* Connection handles — target on the left, source on the right. */}
      <Handle type="target" position={Position.Left} className="!h-2 !w-2 !border-gray-400 !bg-white" />
      <Handle type="source" position={Position.Right} className="!h-2 !w-2 !border-violet-500 !bg-violet-400" />

      {/* Title bar */}
      <div className="flex items-center justify-between gap-2 border-b border-gray-100 bg-gray-50/80 px-2.5 py-1.5 dark:border-gray-700 dark:bg-gray-900/50">
        <span className="truncate text-[11px] font-semibold text-gray-700 dark:text-gray-200" title={node.title}>
          {node.title || 'Untitled'}
        </span>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            rf.deleteElements({ nodes: [{ id }] });
          }}
          className="flex-shrink-0 rounded p-0.5 text-gray-400 hover:bg-red-50 hover:text-red-500 dark:hover:bg-red-900/30"
          title="Remove node"
        >
          <Trash2 className="h-3 w-3" />
        </button>
      </div>

      {/* Body — the Artifact snapshot rendered through the canonical registry. */}
      <div className="nodrag flex-1 overflow-auto p-2 text-[12px]">
        <ArtifactRender artifact={node.snapshot} context="canvas-node" />
      </div>
    </div>
  );
}

const nodeTypes: NodeTypes = { artifact: ArtifactNode };

// ─── Layout ⇆ react-flow conversion ──────────────────────────────────────────
function toFlowNode(n: CanvasNode): ArtifactFlowNode {
  return {
    id: n.id,
    type: 'artifact',
    position: { x: n.x, y: n.y },
    data: { node: n, tint: recencyOpacity(n.created_at) },
    zIndex: n.z ?? 0,
    ...(n.width && n.height ? { width: n.width, height: n.height, style: { width: n.width, height: n.height } } : {}),
  };
}

function toFlowEdge(e: CanvasEdge): Edge {
  const v = EDGE_VISUAL[e.state] ?? EDGE_VISUAL.user;
  const tint = recencyOpacity(e.created_at);
  const insight = e.state === 'researched' ? (e.meta?.insight as string | undefined) : undefined;
  const label = insight || e.label || undefined;
  const directed = e.state === 'provenance' || e.state === 'researched';
  return {
    id: e.id,
    source: e.source,
    target: e.target,
    label,
    data: { edge: e },
    animated: !!v.animated,
    style: {
      stroke: v.stroke,
      strokeWidth: v.width,
      strokeDasharray: v.dash,
      opacity: tint,
    },
    labelBgPadding: [5, 2],
    labelBgBorderRadius: 6,
    labelStyle: { fill: v.stroke, fontSize: 10, fontWeight: 600 },
    labelBgStyle: { fill: '#fff', fillOpacity: 0.92, stroke: v.stroke, strokeWidth: 0.5 },
    ...(directed ? { markerEnd: { type: MarkerType.ArrowClosed, color: v.stroke } } : {}),
  };
}

// Rebuild a CanvasNode from its current react-flow representation (position +
// possibly-resized dimensions), preserving all the backend metadata.
function fromFlowNode(fn: ArtifactFlowNode): CanvasNode {
  const base = fn.data.node;
  return {
    ...base,
    x: fn.position.x,
    y: fn.position.y,
    z: fn.zIndex ?? base.z,
    width: fn.width ?? base.width,
    height: fn.height ?? base.height,
  };
}

// ─── Inner canvas (inside ReactFlowProvider so useReactFlow works) ────────────
interface InnerProps {
  notebookId: string;
}

function JourneyCanvasInner({ notebookId }: InnerProps) {
  const [nodes, setNodes, onNodesChange] = useNodesState<ArtifactFlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [loading, setLoading] = useState(true);
  const [populating, setPopulating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedViewport, setSavedViewport] = useState<Viewport | null>(null);

  // Refs mirror the latest state for the full-layout persistence path.
  const nodesRef = useRef<ArtifactFlowNode[]>([]);
  const edgesRef = useRef<Edge[]>([]);
  const viewportRef = useRef<Viewport | null>(null);
  useEffect(() => { nodesRef.current = nodes; }, [nodes]);
  useEffect(() => { edgesRef.current = edges; }, [edges]);

  const applyLayout = useCallback((layout: CanvasLayout) => {
    setNodes((layout.nodes || []).map(toFlowNode));
    setEdges((layout.edges || []).map(toFlowEdge));
    if (layout.viewport) {
      setSavedViewport({ x: layout.viewport.x, y: layout.viewport.y, zoom: layout.viewport.zoom });
      viewportRef.current = { x: layout.viewport.x, y: layout.viewport.y, zoom: layout.viewport.zoom };
    }
  }, [setNodes, setEdges]);

  const load = useCallback(async () => {
    if (!notebookId) return;
    setLoading(true);
    setError(null);
    try {
      const layout = await canvasService.getLayout(notebookId);
      applyLayout(layout);
    } catch (e) {
      console.error('[JourneyCanvas] load failed', e);
      setError('Could not load the canvas layout.');
    } finally {
      setLoading(false);
    }
  }, [notebookId, applyLayout]);

  useEffect(() => { load(); }, [load]);

  // Persist the whole layout (used after delete + resize). Rebuilds from refs.
  const saveLayout = useCallback(() => {
    if (!notebookId) return;
    const layout: CanvasLayout = {
      nodes: nodesRef.current.map(fromFlowNode),
      edges: edgesRef.current.map((e) => (e.data?.edge as CanvasEdge)).filter(Boolean),
      viewport: viewportRef.current ?? { x: 0, y: 0, zoom: 1 },
    };
    canvasService.putLayout(notebookId, layout).catch((e) => console.warn('[JourneyCanvas] saveLayout', e));
  }, [notebookId]);

  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveLayoutDebounced = useCallback(() => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(saveLayout, 400);
  }, [saveLayout]);

  // ── Move: persist the dragged node's position (debounced per node). ──
  const onNodeDragStop = useCallback((_: unknown, node: Node) => {
    canvasService.patchNodeDebounced(notebookId, node.id, node.position.x, node.position.y);
  }, [notebookId]);

  // ── Draw edges: user-authored connections. ──
  const onConnect = useCallback(async (conn: Connection) => {
    if (!conn.source || !conn.target || conn.source === conn.target) return;
    // Optimistic temp edge, reconciled with the server id.
    const tempId = `tmp-${conn.source}-${conn.target}-${Date.now()}`;
    const optimistic: CanvasEdge = {
      id: tempId,
      source: conn.source,
      target: conn.target,
      state: 'user',
      created_at: new Date().toISOString(),
    };
    setEdges((eds) => [...eds, toFlowEdge(optimistic)]);
    try {
      const saved = await canvasService.createEdge(notebookId, {
        source: conn.source,
        target: conn.target,
        state: 'user',
      });
      setEdges((eds) => eds.map((e) => (e.id === tempId ? toFlowEdge(saved) : e)));
    } catch (e) {
      console.warn('[JourneyCanvas] createEdge failed', e);
      setEdges((eds) => eds.filter((e) => e.id !== tempId)); // roll back
    }
  }, [notebookId, setEdges]);

  // ── Delete: edges hit the DELETE endpoint; nodes persist via full-layout PUT. ──
  const onEdgesDelete = useCallback((deleted: Edge[]) => {
    deleted.forEach((e) => {
      if (e.id.startsWith('tmp-')) return;
      canvasService.deleteEdge(notebookId, e.id).catch((err) => console.warn('[JourneyCanvas] deleteEdge', err));
    });
  }, [notebookId]);

  const onNodesDelete = useCallback((_: Node[]) => {
    // react-flow has already removed them from state by the time this fires;
    // persist the surviving layout on the next tick so refs are current.
    setTimeout(saveLayout, 0);
  }, [saveLayout]);

  // ── Resize: NodeResizer mutates dimensions via onNodesChange; persist on idle. ──
  const handleNodesChange = useCallback((changes: Parameters<typeof onNodesChange>[0]) => {
    onNodesChange(changes);
    if (changes.some((c) => c.type === 'dimensions' && (c as { resizing?: boolean }).resizing === false)) {
      saveLayoutDebounced();
    }
  }, [onNodesChange, saveLayoutDebounced]);

  // ── Viewport: persist pan/zoom on move-end (debounced). ──
  const onMoveEnd = useCallback((_: unknown, vp: Viewport) => {
    viewportRef.current = vp;
    canvasService.putViewportDebounced(notebookId, vp);
  }, [notebookId]);

  // ── Populate. ──
  const onPopulate = useCallback(async () => {
    if (!notebookId) return;
    setPopulating(true);
    setError(null);
    try {
      const layout = await canvasService.populate(notebookId);
      applyLayout(layout);
    } catch (e) {
      console.error('[JourneyCanvas] populate failed', e);
      setError('Populate failed.');
    } finally {
      setPopulating(false);
    }
  }, [notebookId, applyLayout]);

  const isEmpty = !loading && nodes.length === 0;

  return (
    <div className="relative h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={handleNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeDragStop={onNodeDragStop}
        onConnect={onConnect}
        onEdgesDelete={onEdgesDelete}
        onNodesDelete={onNodesDelete}
        onMoveEnd={onMoveEnd}
        defaultViewport={savedViewport ?? undefined}
        fitView={!savedViewport}
        deleteKeyCode={['Backspace', 'Delete']}
        minZoom={0.15}
        maxZoom={2.5}
        proOptions={{ hideAttribution: true }}
        className="bg-gray-50 dark:bg-gray-900"
      >
        <Background gap={22} size={1} className="!bg-gray-50 dark:!bg-gray-900" color="#d1d5db" />
        <Controls showInteractive={false} className="!shadow-md" />
        <MiniMap pannable zoomable className="!bg-white/70 dark:!bg-gray-800/70" nodeStrokeWidth={2} />
      </ReactFlow>

      {/* Toolbar */}
      <div className="pointer-events-none absolute left-3 top-3 z-10 flex flex-col gap-2">
        <div className="pointer-events-auto flex items-center gap-2 rounded-lg border border-gray-200 bg-white/90 px-2 py-1.5 shadow-sm backdrop-blur dark:border-gray-700 dark:bg-gray-800/90">
          <button
            type="button"
            onClick={onPopulate}
            disabled={populating}
            className="flex items-center gap-1.5 rounded-md bg-violet-600 px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-violet-700 disabled:opacity-60"
            title="Seed the canvas from this notebook"
          >
            <Sparkles className="h-3 w-3" />
            {populating ? 'Populating…' : 'Populate'}
          </button>
          <button
            type="button"
            onClick={load}
            disabled={loading}
            className="flex items-center gap-1.5 rounded-md border border-gray-200 px-2 py-1 text-[11px] font-medium text-gray-600 hover:bg-gray-50 disabled:opacity-60 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700"
            title="Reload layout"
          >
            <RefreshCw className={`h-3 w-3 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        {/* Edge legend */}
        <div className="pointer-events-auto flex flex-col gap-1 rounded-lg border border-gray-200 bg-white/90 px-2.5 py-2 shadow-sm backdrop-blur dark:border-gray-700 dark:bg-gray-800/90">
          {EDGE_LEGEND.map(({ state, label }) => (
            <div key={state} className="flex items-center gap-2">
              <span
                className="inline-block h-0 w-5 rounded"
                style={{
                  borderTopWidth: EDGE_VISUAL[state].width,
                  borderTopStyle: EDGE_VISUAL[state].dash ? 'dashed' : 'solid',
                  borderTopColor: EDGE_VISUAL[state].stroke,
                }}
              />
              <span className="text-[10px] text-gray-500 dark:text-gray-400">{label}</span>
            </div>
          ))}
        </div>
      </div>

      {/* States */}
      {loading && (
        <div className="pointer-events-none absolute inset-0 flex items-center justify-center text-sm text-gray-400">
          Loading canvas…
        </div>
      )}
      {error && (
        <div className="absolute bottom-3 left-1/2 z-10 -translate-x-1/2 rounded-md border border-red-200 bg-red-50 px-3 py-1.5 text-[11px] text-red-600 dark:border-red-900 dark:bg-red-950/60 dark:text-red-300">
          {error}
        </div>
      )}
      {isEmpty && !error && (
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-1 text-center text-gray-400">
          <p className="text-sm font-medium">This canvas is empty</p>
          <p className="text-[11px]">Use <span className="font-semibold text-violet-500">Populate</span> to seed it from your notebook.</p>
        </div>
      )}
    </div>
  );
}

// ─── Public component ────────────────────────────────────────────────────────
interface JourneyCanvasProps {
  notebookId: string | null;
}

export function JourneyCanvas({ notebookId }: JourneyCanvasProps) {
  if (!notebookId) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-gray-400">
        Select a notebook to open its Journey Canvas.
      </div>
    );
  }
  return (
    <ReactFlowProvider>
      <JourneyCanvasInner notebookId={notebookId} />
    </ReactFlowProvider>
  );
}

export default JourneyCanvas;
