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
import {
  Trash2, Sparkles, RefreshCw, Scale, X, Compass, MessagesSquare, Plus, Brain, ChevronDown, ChevronRight,
  Mic, Video, HelpCircle, BarChart3, Image, FileText, Files, Circle,
  type LucideIcon,
} from 'lucide-react';
import { ArtifactRender } from '../artifact/RendererRegistry';
import {
  canvasService,
  type CanvasLayout,
  type CanvasNode,
  type CanvasEdge,
  type CanvasCandidate,
  type CandidateNodeRef,
  type CanvasGap,
  type RecallItem,
  type RecallGrade,
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

// Collapsed topic cards shrink to a header-only strip (view-state; never persisted).
const TOPIC_HEADER_H = 56;

// ─── Custom node ─────────────────────────────────────────────────────────────
type ArtifactNodeData = {
  node: CanvasNode;
  tint: number;
  candidates?: NodeCandidate[];
  onPromote?: (peerId: string) => void;
  onPerspectives?: (node: CanvasNode) => void;
  /** A thread outside any topic card — dashed, muted, invites exploration (P4 wires the click). */
  isOrphan?: boolean;
};
type ArtifactFlowNode = Node<ArtifactNodeData, 'artifact'>;

// A topic GROUP card — the react-flow container its child threads position inside.
type TopicCardNodeData = {
  node: CanvasNode;
  collapsed: boolean;
  onToggle: (id: string) => void;
};
type TopicFlowNode = Node<TopicCardNodeData, 'topicCard'>;

// Every node the canvas renders is one of these two.
type CanvasFlowNode = ArtifactFlowNode | TopicFlowNode;

const SIGNAL_LABEL: Record<string, string> = {
  concept: 'shared concepts',
  embed: 'similar meaning',
  shared_source: 'shared source',
};

// A thread's `ref_type` → compact chip presentation (medium icon + muted type
// label). Threads render as clean, legible chips — NOT the full artifact body
// squished into a ~200×120 tile (which read as blurry/undefined). Multiple
// ref_types collapse onto the shared 💬 chat icon.
const THREAD_CHIP: Record<string, { Icon: LucideIcon; label: string }> = {
  audio: { Icon: Mic, label: 'Podcast' },
  video: { Icon: Video, label: 'Video' },
  quiz: { Icon: HelpCircle, label: 'Quiz' },
  visual: { Icon: BarChart3, label: 'Visual' },
  infographic: { Icon: Image, label: 'Infographic' },
  document: { Icon: FileText, label: 'Document' },
  source: { Icon: Files, label: 'Source' },
  exploration_query: { Icon: MessagesSquare, label: 'Question' },
  chat_turn: { Icon: MessagesSquare, label: 'Chat' },
  canvas_answer: { Icon: MessagesSquare, label: 'Answer' },
};

function threadChip(refType: string): { Icon: LucideIcon; label: string } {
  const hit = THREAD_CHIP[refType];
  if (hit) return hit;
  const label = refType ? refType.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()) : 'Item';
  return { Icon: Circle, label };
}

function ArtifactNode({ id, data, selected }: NodeProps<ArtifactFlowNode>) {
  const rf = useReactFlow();
  const { node, tint, candidates, onPromote, onPerspectives, isOrphan } = data;

  // Recency tint stays subtle for chips — clamp so threads never read as
  // "blurred/undefined"; orphans keep a slightly lower floor but stay legible.
  const chipOpacity = isOrphan ? Math.max(0.8, tint) : Math.max(0.85, tint);
  const { Icon: ChipIcon, label: chipLabel } = threadChip(node.ref_type);

  return (
    <div
      className={`relative flex h-full w-full cursor-grab flex-col overflow-hidden rounded-xl border bg-white shadow-sm active:cursor-grabbing dark:bg-gray-800 ${
        isOrphan
          ? 'border-dashed border-gray-300 dark:border-gray-600'
          : 'border-gray-200 dark:border-gray-700'
      }`}
      style={{ opacity: chipOpacity }}
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

      {/* Body — a compact, legible CHIP (icon + type label + clamped title).
          We deliberately DON'T render the full <ArtifactRender> here: squished
          into a ~200×120 tile it read as blurry/undefined. Full-content viewing
          is a later "focus" action (out of scope). NOT `nodrag`: the node must
          drag from its body (the bulk of the card). */}
      <div className="flex h-full w-full flex-1 flex-col gap-1.5 overflow-hidden bg-white p-2.5 dark:bg-gray-800">
        <div className="flex items-center gap-1.5 text-gray-400 dark:text-gray-500">
          <ChipIcon className="h-4 w-4 flex-shrink-0" />
          <span className="truncate text-[10px] font-semibold uppercase tracking-wide">{chipLabel}</span>
        </div>
        <p className="line-clamp-3 text-[12px] font-medium leading-snug text-gray-800 dark:text-gray-100">
          {node.title || 'Untitled'}
        </p>
      </div>

      {/* Orphan affordance (P3) — a thread that hasn't landed in a topic card yet.
          Non-functional placeholder; P4 wires the click into the exploration loop. */}
      {isOrphan && (
        <div
          className="pointer-events-none absolute bottom-1 right-1 z-10 flex items-center gap-0.5 rounded-full bg-gray-100/90 px-1.5 py-0.5 text-[9px] font-medium text-gray-400 dark:bg-gray-700/80 dark:text-gray-400"
          title="Not yet part of a topic — explore to connect it"
        >
          <Plus className="h-2.5 w-2.5" />
          explore
        </div>
      )}
    </div>
  );
}

// ─── Topic GROUP card (P3) ────────────────────────────────────────────────────
// The container react-flow node: child threads position INSIDE it. Renders a
// header (title + one-line synthesis + thread-count chip + collapse chevron);
// the body area below is intentionally transparent so children sit "inside".
function TopicCardNode({ id, data }: NodeProps<TopicFlowNode>) {
  const { node, collapsed, onToggle } = data;
  const payload = (node.snapshot?.payload ?? {}) as {
    title?: string;
    synthesis?: string;
    count?: number;
  };
  const title = payload.title || node.title || 'Topic';
  const synthesis = payload.synthesis || '';
  const count = payload.count ?? 0;

  return (
    <div className="flex h-full w-full flex-col overflow-hidden rounded-2xl border border-violet-200/70 bg-violet-50/40 shadow-sm backdrop-blur-[2px] dark:border-violet-800/50 dark:bg-violet-950/20">
      <div className="flex items-start gap-2 rounded-t-2xl border-b border-violet-100/70 bg-white/70 px-3 py-2 dark:border-violet-900/40 dark:bg-gray-900/60">
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onToggle(id);
          }}
          className="nodrag mt-[1px] flex-shrink-0 rounded p-0.5 text-violet-500 hover:bg-violet-100 dark:text-violet-300 dark:hover:bg-violet-900/40"
          title={collapsed ? 'Expand topic' : 'Collapse topic'}
          aria-label={collapsed ? 'Expand topic' : 'Collapse topic'}
        >
          {collapsed ? <ChevronRight className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span
              className="truncate text-[12px] font-bold text-gray-800 dark:text-gray-100"
              title={title}
            >
              {title}
            </span>
            <span className="flex-shrink-0 rounded-full bg-violet-100 px-1.5 py-0.5 text-[9px] font-semibold text-violet-700 dark:bg-violet-900/50 dark:text-violet-200">
              {count} thread{count === 1 ? '' : 's'}
            </span>
          </div>
          {synthesis && (
            <p
              className="mt-0.5 truncate text-[10px] text-gray-500 dark:text-gray-400"
              title={synthesis}
            >
              {synthesis}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

const nodeTypes: NodeTypes = { artifact: ArtifactNode, topicCard: TopicCardNode };

// ─── Layout ⇆ react-flow conversion ──────────────────────────────────────────
function toFlowNode(n: CanvasNode): CanvasFlowNode {
  // Topic GROUP card — the absolute-positioned container. Its children arrive
  // AFTER it in the layout array (react-flow's parent-before-child requirement).
  if (n.kind === 'topic') {
    return {
      id: n.id,
      type: 'topicCard',
      position: { x: n.x, y: n.y },
      data: { node: n, collapsed: true, onToggle: () => {} },
      zIndex: 0,
      width: n.width,
      height: n.height,
      style: { width: n.width, height: n.height },
    };
  }

  // A thread node. Inside a card ⇒ parentId + relative position; else an orphan.
  const isOrphan = !n.parent_id && n.topic_id == null && n.kind !== 'topic';
  const fn: ArtifactFlowNode = {
    id: n.id,
    type: 'artifact',
    position: { x: n.x, y: n.y },
    data: { node: n, tint: recencyOpacity(n.created_at), isOrphan },
    zIndex: n.z ?? 0,
    // Fixed compact tile so the map reads as uniform "readable tiles" — without this,
    // react-flow sizes each node to its content and the wide chat tiles overlap. A
    // user resize (NodeResizer) still wins via n.width/height.
    width: n.width ?? 300,
    height: n.height ?? 180,
    style: { width: n.width ?? 300, height: n.height ?? 180 },
  };
  if (n.parent_id) {
    // v12: children reference their container via `parentId` + are clamped to it.
    fn.parentId = n.parent_id;
    fn.extent = 'parent';
  }
  return fn;
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
function fromFlowNode(fn: CanvasFlowNode): CanvasNode {
  const base = fn.data.node;
  // Topic cards keep their BACKEND dimensions — the collapse height-swap is a
  // view-only concern and must never round-trip back into the stored layout.
  const isTopic = fn.type === 'topicCard';
  return {
    ...base,
    x: fn.position.x,
    y: fn.position.y,
    z: fn.zIndex ?? base.z,
    width: isTopic ? base.width : (fn.width ?? base.width),
    height: isTopic ? base.height : (fn.height ?? base.height),
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
  const [nodes, setNodes, onNodesChange] = useNodesState<CanvasFlowNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  // Topic cards that are currently collapsed (view-state; default = ALL on load).
  const [collapsedTopics, setCollapsedTopics] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [populating, setPopulating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedViewport, setSavedViewport] = useState<Viewport | null>(null);
  const [candidates, setCandidates] = useState<CanvasCandidate[]>([]);

  const rf = useReactFlow();

  // ── Time-as-a-lens (P6): recency window filter. `null` = show all. ──
  const [timeWindowMs, setTimeWindowMs] = useState<number | null>(null);

  // ── Run R3 gap panel: weakly-answered questions as "what to explore next". ──
  const [gaps, setGaps] = useState<CanvasGap[]>([]);
  const [showGaps, setShowGaps] = useState(false);
  const [gapsLoading, setGapsLoading] = useState(false);

  // ── Run R2 chat-with-selection: ask a question scoped to the selected nodes' sources. ──
  const [selectedNodes, setSelectedNodes] = useState<CanvasNode[]>([]);
  const [chatOpen, setChatOpen] = useState(false);
  const [chatQuery, setChatQuery] = useState('');
  const [chatBusy, setChatBusy] = useState(false);
  const [chatAnswer, setChatAnswer] = useState<string | null>(null);
  const [chatScoped, setChatScoped] = useState(true);
  const chatAnchorRef = useRef<{ x: number; y: number } | null>(null);

  // ── Run R1 recall: spaced-repetition review of learning nodes ("what to revisit"). ──
  const [recallOpen, setRecallOpen] = useState(false);
  const [recallQueue, setRecallQueue] = useState<RecallItem[]>([]);
  const [recallIdx, setRecallIdx] = useState(0);
  const [recallRevealed, setRecallRevealed] = useState(false);
  const [recallTotal, setRecallTotal] = useState(0);
  const [recallLoading, setRecallLoading] = useState(false);

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
  const nodesRef = useRef<CanvasFlowNode[]>([]);
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

  // Collapse/expand a topic card (view-only — never persisted to the backend).
  const toggleTopic = useCallback((id: string) => {
    setCollapsedTopics((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const applyLayout = useCallback((layout: CanvasLayout) => {
    setNodes((layout.nodes || []).map(toFlowNode));
    setEdges((layout.edges || []).map(toFlowEdge));
    // Collapse ALL topic cards by default whenever a fresh layout loads.
    setCollapsedTopics(
      new Set((layout.nodes || []).filter((n) => n.kind === 'topic').map((n) => n.id)),
    );
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
    setNodes((nds) => nds.map((n) => {
      if (n.type !== 'artifact') return n; // topic cards carry no candidate dots
      return {
        ...n,
        data: {
          ...n.data,
          candidates: byNode.get(n.id) ?? [],
          onPromote: (peerId: string) => promoteCandidate(n.id, peerId),
          onPerspectives: openPerspectives,
        },
      };
    }));
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

  // ── Auto-arrange ("tidy up"): re-cluster + re-position all current nodes. ──
  const onRelayout = useCallback(async () => {
    if (!notebookId) return;
    setPopulating(true);
    setError(null);
    try {
      applyLayout(await canvasService.relayout(notebookId));
    } catch (e) {
      console.error('[JourneyCanvas] relayout failed', e);
      setError('Auto-arrange failed.');
    } finally {
      setPopulating(false);
    }
  }, [notebookId, applyLayout]);

  // ── Auto-connect: promote high-confidence candidates to suggested (dashed) edges. ──
  const onAutoConnect = useCallback(async () => {
    if (!notebookId) return;
    setPopulating(true);
    setError(null);
    try {
      const refs = nodesRef.current.map((fn) => toCandidateRef(fn.data.node));
      applyLayout(await canvasService.autoConnect(notebookId, refs));
    } catch (e) {
      console.error('[JourneyCanvas] auto-connect failed', e);
      setError('Auto-connect failed.');
    } finally {
      setPopulating(false);
    }
  }, [notebookId, applyLayout]);

  // ── Run R3 gaps: load "what to explore next" + toggle the panel. ──
  const onToggleGaps = useCallback(async () => {
    if (showGaps) { setShowGaps(false); return; }
    setShowGaps(true);
    setGapsLoading(true);
    try {
      setGaps(await canvasService.getGaps(notebookId));
    } catch (e) {
      console.warn('[JourneyCanvas] getGaps failed', e);
      setGaps([]);
    } finally {
      setGapsLoading(false);
    }
  }, [notebookId, showGaps]);

  // Center the canvas on the weakly-answered question node behind a gap.
  const onFocusGap = useCallback((gap: CanvasGap) => {
    const match = nodesRef.current.find(
      (fn) => fn.data.node.ref_type === 'exploration_query' && fn.data.node.ref_id === gap.ref_id,
    );
    if (match) {
      rf.setCenter(match.position.x + 150, match.position.y + 90, { zoom: 1.1, duration: 500 });
    }
  }, [rf]);

  // ── Run R2 chat-with-selection ──
  const onSelectionChange = useCallback(({ nodes: sel }: { nodes: Node[] }) => {
    setSelectedNodes(sel.map((n) => (n.data as { node: CanvasNode }).node).filter(Boolean));
  }, []);

  const onAskSelection = useCallback(() => {
    // Anchor the answer-node near the centroid of the current selection.
    if (selectedNodes.length) {
      const cx = selectedNodes.reduce((s, n) => s + n.x, 0) / selectedNodes.length;
      const cy = selectedNodes.reduce((s, n) => s + n.y, 0) / selectedNodes.length;
      chatAnchorRef.current = { x: cx, y: cy };
    } else {
      chatAnchorRef.current = null;
    }
    setChatAnswer(null);
    setChatOpen(true);
  }, [selectedNodes]);

  const onSubmitChat = useCallback(async () => {
    const q = chatQuery.trim();
    if (!q || !notebookId) return;
    setChatBusy(true);
    setChatAnswer(null);
    try {
      const refs = selectedNodes.map((n) => ({ ref_type: n.ref_type, ref_id: n.ref_id }));
      const res = await canvasService.chat(notebookId, q, refs);
      setChatAnswer(res.answer || '_No answer._');
      setChatScoped(res.scoped);
    } catch (e) {
      console.error('[JourneyCanvas] canvas chat failed', e);
      setChatAnswer('_Something went wrong answering that._');
    } finally {
      setChatBusy(false);
    }
  }, [chatQuery, notebookId, selectedNodes]);

  // Drop the answer back onto the canvas as a new node ("the map generates the map"),
  // linked to each selected node with a provenance edge, then persist.
  const onAddAnswerNode = useCallback(async () => {
    if (!chatAnswer || !notebookId) return;
    const anchor = chatAnchorRef.current ?? { x: 0, y: 0 };
    const id = (crypto?.randomUUID?.() ?? `ans-${Date.now()}`);
    const q = chatQuery.trim();
    const newNode: CanvasNode = {
      id,
      x: anchor.x + 360,
      y: anchor.y,
      kind: 'chat_turn',
      ref_type: 'canvas_answer',
      ref_id: id,
      title: q.length > 60 ? `${q.slice(0, 59)}…` : q,
      snapshot: { id, type: 'markdown', payload: `**Q:** ${q}\n\n${chatAnswer}` },
      z: 0,
      created_at: new Date().toISOString(),
    };
    setNodes((ns) => [...ns, toFlowNode(newNode)]);
    // Provenance edges from each selected node into the answer.
    for (const sn of selectedNodes) {
      canvasService
        .createEdge(notebookId, { source: sn.id, target: id, state: 'provenance', label: 'answered' })
        .then((e) => setEdges((es) => [...es, toFlowEdge(e)]))
        .catch(() => {});
    }
    saveLayoutDebounced();
    setChatOpen(false);
    setChatQuery('');
    setChatAnswer(null);
  }, [chatAnswer, chatQuery, notebookId, selectedNodes, setNodes, setEdges, saveLayoutDebounced]);

  // ── Run R1 recall ──
  const onToggleRecall = useCallback(async () => {
    if (recallOpen) { setRecallOpen(false); return; }
    setRecallOpen(true);
    setRecallLoading(true);
    setRecallIdx(0);
    setRecallRevealed(false);
    try {
      const res = await canvasService.getRecall(notebookId);
      setRecallQueue(res.due);
      setRecallTotal(res.due_count);
    } catch (e) {
      console.warn('[JourneyCanvas] getRecall failed', e);
      setRecallQueue([]);
      setRecallTotal(0);
    } finally {
      setRecallLoading(false);
    }
  }, [notebookId, recallOpen]);

  const onGradeRecall = useCallback(async (grade: RecallGrade) => {
    const item = recallQueue[recallIdx];
    if (!item) return;
    canvasService.reviewRecall(notebookId, item.id, grade).catch(() => {});
    setRecallRevealed(false);
    setRecallIdx((i) => i + 1);  // advance; the panel shows "all caught up" past the end
  }, [recallQueue, recallIdx, notebookId]);

  const isEmpty = !loading && nodes.length === 0;

  // ── Derived view (P3 + P6): fold TWO reversible view-concerns onto `nodes`
  //    without ever mutating the stored layout —
  //      (1) collapse: a topic card shrinks to header-only + its children hide;
  //      (2) time-window: nodes/edges outside the recency window hide.
  //    Switching a topic open / picking "All" fully restores the map. ──
  const displayNodes = useMemo(() => {
    const cutoff = timeWindowMs == null ? null : Date.now() - timeWindowMs;
    return nodes.map((n): CanvasFlowNode => {
      let hidden = false;
      if (cutoff != null) {
        const t = Date.parse(n.data.node.created_at);
        hidden = !Number.isNaN(t) && t < cutoff;
      }
      if (n.type === 'topicCard') {
        const collapsed = collapsedTopics.has(n.id);
        const h = collapsed ? TOPIC_HEADER_H : (n.data.node.height ?? 220);
        return {
          ...n,
          hidden,
          height: h,
          style: { ...(n.style || {}), width: n.data.node.width, height: h },
          data: { ...n.data, collapsed, onToggle: toggleTopic },
        };
      }
      // A thread also hides when its containing topic card is collapsed.
      if (n.parentId && collapsedTopics.has(n.parentId)) hidden = true;
      return !!n.hidden === hidden ? n : { ...n, hidden };
    });
  }, [nodes, timeWindowMs, collapsedTopics, toggleTopic]);

  const displayEdges = useMemo(() => {
    const hiddenNodeIds = new Set(displayNodes.filter((n) => n.hidden).map((n) => n.id));
    if (hiddenNodeIds.size === 0) return edges;
    return edges.map((e) => {
      const hidden = hiddenNodeIds.has(e.source) || hiddenNodeIds.has(e.target);
      return !!e.hidden === hidden ? e : { ...e, hidden };
    });
  }, [edges, displayNodes]);

  // Only the time-window lens contributes to the "−N outside this window" chip
  // (collapsed children are a separate, self-evident affordance).
  const hiddenCount = useMemo(() => {
    if (timeWindowMs == null) return 0;
    const cutoff = Date.now() - timeWindowMs;
    return nodes.filter((n) => {
      const t = Date.parse(n.data.node.created_at);
      return !Number.isNaN(t) && t < cutoff;
    }).length;
  }, [nodes, timeWindowMs]);

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
        onSelectionChange={onSelectionChange}
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
            onClick={onRelayout}
            disabled={populating || isEmpty}
            className="flex items-center gap-1.5 rounded-md border border-violet-200 px-2.5 py-1 text-[11px] font-semibold text-violet-700 hover:bg-violet-50 disabled:opacity-60 dark:border-violet-700 dark:text-violet-300 dark:hover:bg-violet-900/30"
            title="Auto-arrange: re-cluster the map into readable groups by similarity"
          >
            Tidy up
          </button>
          <button
            type="button"
            onClick={onAutoConnect}
            disabled={populating || isEmpty}
            className="flex items-center gap-1.5 rounded-md border border-cyan-200 px-2.5 py-1 text-[11px] font-semibold text-cyan-700 hover:bg-cyan-50 disabled:opacity-60 dark:border-cyan-700 dark:text-cyan-300 dark:hover:bg-cyan-900/30"
            title="Auto-connect: draw suggested (dashed) edges between strongly-related nodes"
          >
            Auto-connect
          </button>
          <button
            type="button"
            onClick={onToggleGaps}
            className={`flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px] font-semibold disabled:opacity-60 ${
              showGaps
                ? 'border-amber-400 bg-amber-100 text-amber-800 dark:border-amber-500 dark:bg-amber-900/40 dark:text-amber-200'
                : 'border-amber-200 text-amber-700 hover:bg-amber-50 dark:border-amber-700 dark:text-amber-300 dark:hover:bg-amber-900/30'
            }`}
            title="Gaps: questions your sources answered weakly — what to explore next"
          >
            <Compass className="h-3 w-3" />
            Gaps
          </button>
          <button
            type="button"
            onClick={onAskSelection}
            disabled={selectedNodes.length === 0}
            className="flex items-center gap-1.5 rounded-md border border-emerald-200 px-2.5 py-1 text-[11px] font-semibold text-emerald-700 hover:bg-emerald-50 disabled:opacity-40 dark:border-emerald-700 dark:text-emerald-300 dark:hover:bg-emerald-900/30"
            title={selectedNodes.length ? `Ask a question across the ${selectedNodes.length} selected node(s)` : 'Select nodes first, then ask across them'}
          >
            <MessagesSquare className="h-3 w-3" />
            Ask{selectedNodes.length ? ` (${selectedNodes.length})` : ''}
          </button>
          <button
            type="button"
            onClick={onToggleRecall}
            disabled={isEmpty}
            className={`flex items-center gap-1.5 rounded-md border px-2.5 py-1 text-[11px] font-semibold disabled:opacity-40 ${
              recallOpen
                ? 'border-indigo-400 bg-indigo-100 text-indigo-800 dark:border-indigo-500 dark:bg-indigo-900/40 dark:text-indigo-200'
                : 'border-indigo-200 text-indigo-700 hover:bg-indigo-50 dark:border-indigo-700 dark:text-indigo-300 dark:hover:bg-indigo-900/30'
            }`}
            title="Recall: review what you've learned (spaced repetition)"
          >
            <Brain className="h-3 w-3" />
            Recall
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

      {/* Run R3 — "what to explore next" panel: questions your sources answered
          weakly. Click one to fly the canvas to that question node. Read-only. */}
      {showGaps && (
        <div className="pointer-events-auto absolute bottom-3 right-3 z-20 flex max-h-[60%] w-[min(340px,85%)] flex-col rounded-lg border border-amber-200 bg-white/95 shadow-xl backdrop-blur dark:border-amber-800 dark:bg-gray-800/95">
          <div className="flex items-center justify-between gap-2 border-b border-amber-100 px-3 py-2 dark:border-amber-900/50">
            <div className="flex items-center gap-2">
              <Compass className="h-4 w-4 text-amber-500" />
              <p className="text-[12px] font-semibold text-gray-700 dark:text-gray-200">What to explore next</p>
            </div>
            <button
              type="button"
              onClick={() => setShowGaps(false)}
              className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-700"
              title="Close"
              aria-label="Close gaps"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="flex-1 overflow-auto p-2">
            {gapsLoading && (
              <div className="flex items-center justify-center gap-2 py-6 text-gray-400">
                <RefreshCw className="h-4 w-4 animate-spin" />
                <span className="text-[11px]">Finding gaps…</span>
              </div>
            )}
            {!gapsLoading && gaps.length === 0 && (
              <p className="px-2 py-6 text-center text-[11px] text-gray-400">
                No weak spots found — your sources answered your questions well.
              </p>
            )}
            {!gapsLoading && gaps.map((gap) => (
              <button
                key={gap.ref_id || gap.query}
                type="button"
                onClick={() => onFocusGap(gap)}
                className="mb-1 w-full rounded-md border border-transparent px-2 py-1.5 text-left hover:border-amber-200 hover:bg-amber-50 dark:hover:border-amber-800 dark:hover:bg-amber-900/20"
                title="Center the canvas on this question"
              >
                <p className="truncate text-[12px] font-medium text-gray-700 dark:text-gray-200">{gap.query}</p>
                <p className="text-[10px] text-amber-600 dark:text-amber-400">{gap.reason}</p>
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Run R2 — chat with the selection: ask a question scoped to the SOURCES behind the
          selected nodes; drop the answer back onto the canvas as a new node. */}
      {chatOpen && (
        <div className="pointer-events-auto absolute bottom-3 left-1/2 z-30 flex max-h-[70%] w-[min(560px,92%)] -translate-x-1/2 flex-col rounded-xl border border-emerald-200 bg-white/97 shadow-2xl backdrop-blur dark:border-emerald-800 dark:bg-gray-800/97">
          <div className="flex items-center justify-between gap-2 border-b border-emerald-100 px-3 py-2 dark:border-emerald-900/50">
            <div className="flex items-center gap-2">
              <MessagesSquare className="h-4 w-4 text-emerald-500" />
              <p className="text-[12px] font-semibold text-gray-700 dark:text-gray-200">
                Ask across {selectedNodes.length} selected node{selectedNodes.length === 1 ? '' : 's'}
              </p>
            </div>
            <button
              type="button"
              onClick={() => setChatOpen(false)}
              className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-700"
              title="Close"
              aria-label="Close chat"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="flex items-center gap-2 px-3 py-2">
            <input
              type="text"
              autoFocus
              value={chatQuery}
              onChange={(e) => setChatQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && !chatBusy) onSubmitChat(); }}
              placeholder="Ask a question about the selected nodes…"
              className="flex-1 rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-[12px] text-gray-700 outline-none focus:border-emerald-400 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-200"
            />
            <button
              type="button"
              onClick={onSubmitChat}
              disabled={chatBusy || !chatQuery.trim()}
              className="rounded-md bg-emerald-600 px-3 py-1.5 text-[11px] font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              {chatBusy ? 'Asking…' : 'Ask'}
            </button>
          </div>
          {chatAnswer !== null && (
            <div className="flex-1 overflow-auto border-t border-gray-100 px-3 py-2 dark:border-gray-700">
              {!chatScoped && (
                <p className="mb-1 text-[10px] italic text-gray-400">
                  The selection had no linked sources — answered across the whole notebook.
                </p>
              )}
              <ArtifactRender
                artifact={{ id: 'canvas-chat-answer', type: 'markdown', payload: chatAnswer }}
                context="canvas-full"
              />
              <div className="mt-2 flex justify-end">
                <button
                  type="button"
                  onClick={onAddAnswerNode}
                  className="flex items-center gap-1.5 rounded-md border border-emerald-300 px-2.5 py-1 text-[11px] font-semibold text-emerald-700 hover:bg-emerald-50 dark:border-emerald-700 dark:text-emerald-300 dark:hover:bg-emerald-900/30"
                  title="Add this answer to the canvas as a new node"
                >
                  <Plus className="h-3 w-3" />
                  Add to canvas
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Run R1 — recall: spaced-repetition review of learning nodes. Flashcard flow:
          show the question → reveal the answer → grade (advances the SM-2 schedule). */}
      {recallOpen && (
        <div className="pointer-events-auto absolute bottom-3 left-1/2 z-30 flex max-h-[72%] w-[min(560px,92%)] -translate-x-1/2 flex-col rounded-xl border border-indigo-200 bg-white/97 shadow-2xl backdrop-blur dark:border-indigo-800 dark:bg-gray-800/97">
          <div className="flex items-center justify-between gap-2 border-b border-indigo-100 px-3 py-2 dark:border-indigo-900/50">
            <div className="flex items-center gap-2">
              <Brain className="h-4 w-4 text-indigo-500" />
              <p className="text-[12px] font-semibold text-gray-700 dark:text-gray-200">
                Recall
                {!recallLoading && recallQueue.length > 0 && recallIdx < recallQueue.length && (
                  <span className="ml-1 font-normal text-gray-400">
                    · {recallIdx + 1} of {recallQueue.length}
                  </span>
                )}
              </p>
            </div>
            <button
              type="button"
              onClick={() => setRecallOpen(false)}
              className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-700"
              title="Close"
              aria-label="Close recall"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          <div className="flex-1 overflow-auto p-3">
            {recallLoading && (
              <div className="flex items-center justify-center gap-2 py-8 text-gray-400">
                <RefreshCw className="h-4 w-4 animate-spin" />
                <span className="text-[11px]">Finding what to review…</span>
              </div>
            )}
            {!recallLoading && (recallQueue.length === 0 || recallIdx >= recallQueue.length) && (
              <div className="flex flex-col items-center gap-1 py-8 text-center">
                <Brain className="h-6 w-6 text-indigo-300" />
                <p className="text-[13px] font-medium text-gray-600 dark:text-gray-300">All caught up</p>
                <p className="text-[11px] text-gray-400">
                  {recallTotal === 0
                    ? 'Nothing to review yet — explore some questions first.'
                    : "You've reviewed everything due. Come back later."}
                </p>
              </div>
            )}
            {!recallLoading && recallIdx < recallQueue.length && (
              <div>
                <p className="mb-2 text-[10px] font-medium uppercase tracking-wide text-indigo-400">
                  Do you remember?
                </p>
                <p className="text-[15px] font-semibold text-gray-800 dark:text-gray-100">
                  {recallQueue[recallIdx].title}
                </p>
                {!recallRevealed ? (
                  <button
                    type="button"
                    onClick={() => setRecallRevealed(true)}
                    className="mt-4 w-full rounded-md bg-indigo-600 px-3 py-1.5 text-[12px] font-semibold text-white hover:bg-indigo-700"
                  >
                    Show answer
                  </button>
                ) : (
                  <>
                    <div className="mt-3 border-t border-gray-100 pt-3 dark:border-gray-700">
                      <ArtifactRender
                        artifact={recallQueue[recallIdx].snapshot}
                        context="canvas-full"
                      />
                    </div>
                    <div className="mt-3 flex gap-2">
                      <button
                        type="button"
                        onClick={() => onGradeRecall('again')}
                        className="flex-1 rounded-md border border-rose-300 px-2 py-1.5 text-[11px] font-semibold text-rose-700 hover:bg-rose-50 dark:border-rose-700 dark:text-rose-300 dark:hover:bg-rose-900/30"
                      >
                        Again
                      </button>
                      <button
                        type="button"
                        onClick={() => onGradeRecall('good')}
                        className="flex-1 rounded-md border border-indigo-300 px-2 py-1.5 text-[11px] font-semibold text-indigo-700 hover:bg-indigo-50 dark:border-indigo-700 dark:text-indigo-300 dark:hover:bg-indigo-900/30"
                      >
                        Good
                      </button>
                      <button
                        type="button"
                        onClick={() => onGradeRecall('easy')}
                        className="flex-1 rounded-md border border-emerald-300 px-2 py-1.5 text-[11px] font-semibold text-emerald-700 hover:bg-emerald-50 dark:border-emerald-700 dark:text-emerald-300 dark:hover:bg-emerald-900/30"
                      >
                        Easy
                      </button>
                    </div>
                  </>
                )}
              </div>
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
