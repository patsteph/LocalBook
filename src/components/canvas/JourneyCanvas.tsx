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
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
import { Trash2, Sparkles, RefreshCw, Scale, X } from 'lucide-react';
import { ArtifactRender } from '../artifact/RendererRegistry';
import {
  canvasService,
  type CanvasLayout,
  type CanvasNode,
  type CanvasEdge,
  type CanvasCandidate,
  type CandidateNodeRef,
  type EdgeState,
} from '../../services/canvas';
import { synthesisService } from '../../services/synthesis';

// Unordered pair key so a candidate/edge is de-duped regardless of direction.
function pairKey(a: string, b: string): string {
  return a < b ? `${a}|${b}` : `${b}|${a}`;
}

// A latent-connection suggestion as seen from one node (its peer on the other end).
interface NodeCandidate {
  peerId: string;
  score: number;
  signal: string;
}

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

// ─── Time-as-a-lens window filter (P6) ───────────────────────────────────────
// Position still encodes meaning; this only hides (never removes) nodes outside
// the chosen recency window, so it's fully reversible ("All" restores everything).
const DAY_MS = 86_400_000;
const TIME_WINDOWS: { label: string; ms: number | null }[] = [
  { label: 'All', ms: null },
  { label: '24h', ms: DAY_MS },
  { label: '7d', ms: 7 * DAY_MS },
  { label: '30d', ms: 30 * DAY_MS },
];

// ─── Custom node ─────────────────────────────────────────────────────────────
type ArtifactNodeData = {
  node: CanvasNode;
  tint: number;
  candidates?: NodeCandidate[];
  onPromote?: (peerId: string) => void;
  onPerspectives?: (node: CanvasNode) => void;
};
type ArtifactFlowNode = Node<ArtifactNodeData, 'artifact'>;

const SIGNAL_LABEL: Record<string, string> = {
  concept: 'shared concepts',
  embed: 'similar meaning',
  shared_source: 'shared source',
};

function ArtifactNode({ id, data, selected }: NodeProps<ArtifactFlowNode>) {
  const rf = useReactFlow();
  const { node, tint, candidates, onPromote, onPerspectives } = data;

  return (
    <div
      className="flex h-full w-full cursor-grab flex-col overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm active:cursor-grabbing dark:border-gray-700 dark:bg-gray-800"
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

      {/* Candidate dots (P5) — amber pulsing invitations to draw a latent connection.
          Click one to promote that pair to a real `user` edge. */}
      {candidates && candidates.length > 0 && (
        <div className="nodrag nopan absolute -top-3 left-1/2 z-20 flex -translate-x-1/2 items-center gap-1">
          {candidates.map((c) => (
            <button
              key={c.peerId}
              type="button"
              onClick={(e) => {
                e.stopPropagation();
                onPromote?.(c.peerId);
              }}
              title={`Connect — ${SIGNAL_LABEL[c.signal] ?? c.signal} (${Math.round(c.score * 100)}%)`}
              aria-label={`Draw a connection (${SIGNAL_LABEL[c.signal] ?? c.signal})`}
              className="h-2.5 w-2.5 animate-pulse rounded-full border border-amber-500 bg-amber-400 shadow-sm transition-transform hover:scale-150 hover:animate-none"
            />
          ))}
        </div>
      )}

      {/* Title bar */}
      <div className="flex items-center justify-between gap-2 border-b border-gray-100 bg-gray-50/80 px-2.5 py-1.5 dark:border-gray-700 dark:bg-gray-900/50">
        <span className="truncate text-[11px] font-semibold text-gray-700 dark:text-gray-200" title={node.title}>
          {node.title || 'Untitled'}
        </span>
        <div className="nodrag flex flex-shrink-0 items-center gap-0.5">
          {/* Supporting / differing views on demand (P6) — reuses the existing
              /synthesis/perspectives engine (consensus + contested claims). */}
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onPerspectives?.(node);
            }}
            className="rounded p-0.5 text-gray-400 hover:bg-violet-50 hover:text-violet-600 dark:hover:bg-violet-900/30 dark:hover:text-violet-300"
            title="Supporting / differing views on this topic"
            aria-label="Show supporting and differing views"
          >
            <Scale className="h-3 w-3" />
          </button>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              rf.deleteElements({ nodes: [{ id }] });
            }}
            className="rounded p-0.5 text-gray-400 hover:bg-red-50 hover:text-red-500 dark:hover:bg-red-900/30"
            title="Remove node"
          >
            <Trash2 className="h-3 w-3" />
          </button>
        </div>
      </div>

      {/* Body — the Artifact snapshot rendered through the canonical registry.
          NOT `nodrag`: the node must drag from its body (the bulk of the card);
          the delete button + candidate dots keep `nodrag`/`nopan` for interaction. */}
      <div className="flex-1 overflow-hidden p-2 text-[12px]">
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

// Visible-node → candidate-engine ref (title + snapshot text feed embedding similarity).
function toCandidateRef(n: CanvasNode): CandidateNodeRef {
  const payload = (n.snapshot as { payload?: unknown } | undefined)?.payload;
  const text = typeof payload === 'string' ? payload : '';
  return { id: n.id, ref_type: n.ref_type, ref_id: n.ref_id, title: n.title, text };
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
  const [candidates, setCandidates] = useState<CanvasCandidate[]>([]);

  // ── Time-as-a-lens (P6): recency window filter. `null` = show all. ──
  const [timeWindowMs, setTimeWindowMs] = useState<number | null>(null);

  // ── Per-node supporting/differing view (P6): a right-side drawer that reuses
  //    the existing /synthesis/perspectives engine. Read-only; never persisted. ──
  const [perspective, setPerspective] = useState<{
    open: boolean;
    topic: string;
    loading: boolean;
    html: string | null;
    error: string | null;
  }>({ open: false, topic: '', loading: false, html: null, error: null });

  // Refs mirror the latest state for the full-layout persistence path.
  const nodesRef = useRef<ArtifactFlowNode[]>([]);
  const edgesRef = useRef<Edge[]>([]);
  const viewportRef = useRef<Viewport | null>(null);
  useEffect(() => { nodesRef.current = nodes; }, [nodes]);
  useEffect(() => { edgesRef.current = edges; }, [edges]);

  // ── Candidate dots (P5): fetch transient latent-connection suggestions. ──
  const refreshCandidates = useCallback(async (nodeList: CanvasNode[]) => {
    if (!notebookId || nodeList.length < 2) {
      setCandidates([]);
      return;
    }
    try {
      const found = await canvasService.getCandidates(notebookId, nodeList.map(toCandidateRef));
      setCandidates(found);
    } catch (e) {
      console.warn('[JourneyCanvas] candidates', e);
      setCandidates([]);
    }
  }, [notebookId]);

  const applyLayout = useCallback((layout: CanvasLayout) => {
    setNodes((layout.nodes || []).map(toFlowNode));
    setEdges((layout.edges || []).map(toFlowEdge));
    if (layout.viewport) {
      setSavedViewport({ x: layout.viewport.x, y: layout.viewport.y, zoom: layout.viewport.zoom });
      viewportRef.current = { x: layout.viewport.x, y: layout.viewport.y, zoom: layout.viewport.zoom };
    }
    refreshCandidates(layout.nodes || []);
  }, [setNodes, setEdges, refreshCandidates]);

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

  // ── Promote a candidate dot to a real user edge (same gesture as onConnect). ──
  const promoteCandidate = useCallback(async (aId: string, bId: string) => {
    // Drop the suggestion immediately (optimistic — the edge effect will also hide it).
    setCandidates((cs) => cs.filter((c) => pairKey(c.a_node, c.b_node) !== pairKey(aId, bId)));
    const tempId = `tmp-${aId}-${bId}-${Date.now()}`;
    const optimistic: CanvasEdge = {
      id: tempId, source: aId, target: bId, state: 'user', created_at: new Date().toISOString(),
    };
    setEdges((eds) => [...eds, toFlowEdge(optimistic)]);
    try {
      const saved = await canvasService.createEdge(notebookId, { source: aId, target: bId, state: 'user' });
      setEdges((eds) => eds.map((e) => (e.id === tempId ? toFlowEdge(saved) : e)));
    } catch (e) {
      console.warn('[JourneyCanvas] promoteCandidate failed', e);
      setEdges((eds) => eds.filter((e) => e.id !== tempId)); // roll back
    }
  }, [notebookId, setEdges]);

  // ── Supporting/differing view: fetch perspectives for a node's topic. ──
  // Read-only reuse of the existing /synthesis/perspectives engine — its
  // consensus/contested claim clusters ARE the "supporting vs differing" split.
  const openPerspectives = useCallback(async (node: CanvasNode) => {
    const topic = (node.title || '').trim();
    if (!topic) return;
    setPerspective({ open: true, topic, loading: true, html: null, error: null });
    try {
      const { html } = await synthesisService.findPerspectives(topic, notebookId, false, 8);
      setPerspective({ open: true, topic, loading: false, html, error: null });
    } catch (e) {
      console.warn('[JourneyCanvas] perspectives', e);
      setPerspective({
        open: true, topic, loading: false, html: null,
        error: e instanceof Error ? e.message : 'Could not load perspectives for this topic.',
      });
    }
  }, [notebookId]);

  // Project candidates onto their two endpoint nodes (skipping pairs already edged) and
  // hand each node stable promote + perspectives callbacks — the custom node renders the
  // amber dots and the per-node "supporting/differing view" action.
  useEffect(() => {
    const connected = new Set(edges.map((e) => pairKey(e.source, e.target)));
    const byNode = new Map<string, NodeCandidate[]>();
    const add = (nodeId: string, peerId: string, score: number, signal: string) => {
      const list = byNode.get(nodeId) ?? [];
      list.push({ peerId, score, signal });
      byNode.set(nodeId, list);
    };
    for (const c of candidates) {
      if (connected.has(pairKey(c.a_node, c.b_node))) continue;
      add(c.a_node, c.b_node, c.score, c.signal);
      add(c.b_node, c.a_node, c.score, c.signal);
    }
    setNodes((nds) => nds.map((n) => ({
      ...n,
      data: {
        ...n.data,
        candidates: byNode.get(n.id) ?? [],
        onPromote: (peerId: string) => promoteCandidate(n.id, peerId),
        onPerspectives: openPerspectives,
      },
    })));
  }, [candidates, edges, promoteCandidate, openPerspectives, setNodes]);

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

  // ── Time-window filter: hide (never remove) nodes/edges outside the window. ──
  // Purely a view concern — the stored layout is untouched, so switching back to
  // "All" fully restores the map. Position still encodes meaning throughout.
  const displayNodes = useMemo(() => {
    if (timeWindowMs == null) return nodes;
    const cutoff = Date.now() - timeWindowMs;
    return nodes.map((n) => {
      const t = Date.parse(n.data.node.created_at);
      const hidden = !Number.isNaN(t) && t < cutoff;
      return !!n.hidden === hidden ? n : { ...n, hidden };
    });
  }, [nodes, timeWindowMs]);

  const displayEdges = useMemo(() => {
    if (timeWindowMs == null) return edges;
    const hiddenNodeIds = new Set(displayNodes.filter((n) => n.hidden).map((n) => n.id));
    if (hiddenNodeIds.size === 0) return edges;
    return edges.map((e) => {
      const hidden = hiddenNodeIds.has(e.source) || hiddenNodeIds.has(e.target);
      return !!e.hidden === hidden ? e : { ...e, hidden };
    });
  }, [edges, displayNodes, timeWindowMs]);

  const hiddenCount = timeWindowMs == null ? 0 : displayNodes.filter((n) => n.hidden).length;

  return (
    <div className="relative h-full w-full">
      <ReactFlow
        nodes={displayNodes}
        edges={displayEdges}
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

        {/* Time-as-a-lens window filter (P6) — a reversible recency lens; older
            nodes already read fainter (recency tint), this narrows to a window. */}
        <div className="pointer-events-auto flex items-center gap-1 rounded-lg border border-gray-200 bg-white/90 p-1 shadow-sm backdrop-blur dark:border-gray-700 dark:bg-gray-800/90">
          {TIME_WINDOWS.map(({ label, ms }) => {
            const active = timeWindowMs === ms;
            return (
              <button
                key={label}
                type="button"
                onClick={() => setTimeWindowMs(ms)}
                className={`rounded-md px-2 py-0.5 text-[10px] font-semibold transition-colors ${
                  active
                    ? 'bg-violet-600 text-white'
                    : 'text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700'
                }`}
                title={ms == null ? 'Show all nodes' : `Show nodes from the last ${label}`}
              >
                {label}
              </button>
            );
          })}
          {hiddenCount > 0 && (
            <span className="pl-1 pr-0.5 text-[10px] text-gray-400" title={`${hiddenCount} node(s) outside this window`}>
              −{hiddenCount}
            </span>
          )}
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

      {/* Supporting / differing views drawer (P6) — server-composed perspectives
          HTML rendered through the canonical Artifact registry. Read-only. */}
      {perspective.open && (
        <div className="absolute inset-y-0 right-0 z-20 flex w-[min(440px,90%)] flex-col border-l border-gray-200 bg-white shadow-2xl dark:border-gray-700 dark:bg-gray-800">
          <div className="flex items-center justify-between gap-2 border-b border-gray-100 px-3 py-2 dark:border-gray-700">
            <div className="flex min-w-0 items-center gap-2">
              <Scale className="h-4 w-4 flex-shrink-0 text-violet-500" />
              <div className="min-w-0">
                <p className="text-[10px] font-medium uppercase tracking-wide text-gray-400">Supporting / differing views</p>
                <p className="truncate text-[12px] font-semibold text-gray-700 dark:text-gray-200" title={perspective.topic}>
                  {perspective.topic}
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => setPerspective((p) => ({ ...p, open: false }))}
              className="flex-shrink-0 rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-700"
              title="Close"
              aria-label="Close perspectives"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="flex-1 overflow-auto p-3">
            {perspective.loading && (
              <div className="flex h-full flex-col items-center justify-center gap-2 text-gray-400">
                <RefreshCw className="h-5 w-5 animate-spin" />
                <p className="text-[11px]">Gathering perspectives across your sources…</p>
              </div>
            )}
            {perspective.error && (
              <div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-[11px] text-red-600 dark:border-red-900 dark:bg-red-950/60 dark:text-red-300">
                {perspective.error}
              </div>
            )}
            {!perspective.loading && !perspective.error && perspective.html && (
              <ArtifactRender
                artifact={{
                  id: `perspectives-${perspective.topic}`,
                  type: 'html',
                  payload: perspective.html,
                  title: perspective.topic,
                }}
                context="canvas-full"
              />
            )}
          </div>
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
