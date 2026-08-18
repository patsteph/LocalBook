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
  useNodesState,
  useEdgesState,
  useReactFlow,
  type Node,
  type Edge,
  type Connection,
  type Viewport,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import {
  Sparkles, RefreshCw, X, MessagesSquare, Plus, Brain, Compass, Scale,
} from 'lucide-react';
import { ArtifactRender } from '../artifact/RendererRegistry';
import { ThreadWindow } from './ThreadWindow';
import type { Point } from './journeyWindowSizing';
// Node renderers + the pure layout⇆flow math live alongside (split out 2026-08-18).
import {
  nodeTypes,
  type ArtifactNodeData,
  type CanvasFlowNode,
  type NodeCandidate,
} from './journeyNodeTypes';
import {
  pairKey,
  computeRanks,
  computeComposition,
  toFlowNode,
  toFlowEdge,
  fromFlowNode,
  toCandidateRef,
  EDGE_LEGEND,
  EDGE_VISUAL,
  TIME_WINDOWS,
  TOPIC_HEADER_H,
} from './journeyTransforms';
import {
  canvasService,
  type CanvasLayout,
  type CanvasNode,
  type CanvasEdge,
  type CanvasCandidate,
  type CanvasGap,
  type RecallItem,
  type RecallGrade,
  type ElicitSuggestion,
} from '../../services/canvas';
import { synthesisService } from '../../services/synthesis';


// Thread windows float above the canvas and its drawers, below nothing else.
const BASE_WINDOW_Z = 40;
// Past a handful of open windows the map underneath is buried and the feature works against
// itself. Opening more closes the least-recently-touched one.
const MAX_WINDOWS = 4;

interface OpenWindow {
  key: string;
  node: CanvasNode;
  anchor: Point | null;
  /** Monotonic stacking order — the window you last touched is in front. */
  z: number;
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

  // ── P4 orphan intent-elicitation: "what were you exploring here?" → the intent re-assigns the
  //    orphan to the nearest sub-topic (or leaves it orphan) + enqueues away-gated research. ──
  const [elicit, setElicit] = useState<{
    open: boolean;
    node: CanvasNode | null;
    intent: string;
    busy: boolean;
    done: boolean;
    suggestions: ElicitSuggestion[];
    assignedTopicId: string | null;
    error: string | null;
  }>({ open: false, node: null, intent: '', busy: false, done: false,
       suggestions: [], assignedTopicId: null, error: null });

  // ── Thread windows: a thread's REAL content in a small FLOATING window (play the podcast,
  //    read the doc, refresh on the quiz) without leaving the map. Several can be open at once
  //    on purpose — the point of playing a podcast from the map is to keep exploring while it
  //    runs, which a single-slot drawer made impossible. Read-only; never mutates the layout. ──
  const [openWindows, setOpenWindows] = useState<OpenWindow[]>([]);
  const zSeq = useRef(1);

  const openThread = useCallback((n: CanvasNode, anchor?: Point | null) => {
    setOpenWindows((prev) => {
      // Already open → raise it rather than stacking a duplicate on top of itself.
      const existing = prev.find((w) => w.node.id === n.id);
      if (existing) {
        return prev.map((w) => (w.node.id === n.id ? { ...w, z: ++zSeq.current } : w));
      }
      const next = [...prev, { node: n, anchor: anchor ?? null, z: ++zSeq.current, key: n.id }];
      // Bounded: past a handful the map is buried. Drop the OLDEST — the one you touched least
      // recently — rather than refusing to open the thing that was just clicked.
      return next.length > MAX_WINDOWS
        ? [...next].sort((a, b) => a.z - b.z).slice(next.length - MAX_WINDOWS)
        : next;
    });
  }, []);

  const closeWindow = useCallback((key: string) => {
    setOpenWindows((prev) => prev.filter((w) => w.key !== key));
  }, []);

  const raiseWindow = useCallback((key: string) => {
    setOpenWindows((prev) => {
      const top = Math.max(...prev.map((w) => w.z), 0);
      const target = prev.find((w) => w.key === key);
      if (!target || target.z === top) return prev;   // already in front — don't re-render
      return prev.map((w) => (w.key === key ? { ...w, z: ++zSeq.current } : w));
    });
  }, []);

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
    const ranks = computeRanks(layout.nodes || []);
    const composition = computeComposition(layout.nodes || []);
    setNodes((layout.nodes || []).map((n) =>
      toFlowNode(n, { rank: ranks.get(n.id), composition: composition.get(n.id) })));
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

  // Open loops are fetched alongside the layout so the badges are on the map from the start —
  // NOT only after someone opens the gaps panel. Fire-and-forget: a gap-detection failure must
  // never block or fail the canvas, it just means no badges. The badge itself is applied in the
  // effect below, which also covers the panel's own refresh.
  useEffect(() => {
    if (!notebookId) return;
    let cancelled = false;
    canvasService
      .getGaps(notebookId)
      .then((found) => { if (!cancelled) setGaps(found); })
      .catch((e) => console.warn('[JourneyCanvas] gaps preload failed', e));
    return () => { cancelled = true; };
  }, [notebookId]);

  // Decorate question nodes with their open-loop state. Kept separate from `applyLayout` so it
  // doesn't matter whether the gaps or the layout land first, and so a panel refresh re-badges
  // without rebuilding the map. Identity-stable: nodes whose state didn't change are returned
  // as-is, so react-flow doesn't re-render the whole canvas.
  useEffect(() => {
    const reasonByRef = new Map(gaps.map((g) => [g.ref_id, g.reason]));
    setNodes((ns) => {
      let changed = false;
      const next = ns.map((n) => {
        if (n.type !== 'artifact') return n;
        const { node } = n.data;
        const openLoop = node.ref_type === 'exploration_query'
          ? reasonByRef.get(node.ref_id)
          : undefined;
        if (n.data.openLoop === openLoop) return n;
        changed = true;
        return { ...n, data: { ...n.data, openLoop } };
      });
      return changed ? next : ns;
    });
  }, [gaps, setNodes]);

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

  // ── P4 — open the elicitation prompt for an orphan thread. ──
  const openElicit = useCallback((node: CanvasNode) => {
    setElicit({ open: true, node, intent: '', busy: false, done: false,
                suggestions: [], assignedTopicId: null, error: null });
  }, []);

  const onSubmitElicit = useCallback(async () => {
    const node = elicit.node;
    const intent = elicit.intent.trim();
    if (!node || !intent || elicit.busy) return;
    setElicit((p) => ({ ...p, busy: true, error: null }));
    try {
      const res = await canvasService.elicit(notebookId, node.id, intent);
      applyLayout(res.layout); // the (now-assigned) node sheds its orphan styling immediately
      setElicit((p) => ({ ...p, busy: false, done: true,
                          suggestions: res.suggestions || [], assignedTopicId: res.assigned_topic_id }));
    } catch (e) {
      console.warn('[JourneyCanvas] elicit', e);
      setElicit((p) => ({ ...p, busy: false,
                          error: e instanceof Error ? e.message : 'Could not save that.' }));
    }
  }, [notebookId, applyLayout, elicit.node, elicit.intent, elicit.busy]);

  // Project candidates onto their two endpoint nodes (skipping pairs already edged) and
  // hand each node stable promote + perspectives + elicit callbacks — the custom node renders
  // the amber dots, the "supporting/differing view" action, and the orphan "explore" prompt.
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
          onElicit: openElicit,
          onOpen: openThread,
        },
      };
    }));
  }, [candidates, edges, promoteCandidate, openPerspectives, openElicit, setNodes]);

  // ── Delete: edges hit the DELETE endpoint; nodes persist via full-layout PUT. ──
  const onEdgesDelete = useCallback((deleted: Edge[]) => {
    deleted.forEach((e) => {
      if (e.id.startsWith('tmp-')) return;
      canvasService.deleteEdge(notebookId, e.id).catch((err) => console.warn('[JourneyCanvas] deleteEdge', err));
    });
  }, [notebookId]);

  // Double-click a thread to open it — the discoverable gesture alongside the toolbar
  // button. Topic cards are excluded: double-clicking a card is not "open the card".
  const onNodeDoubleClick = useCallback((e: React.MouseEvent, n: Node) => {
    if (n.type !== 'artifact') return;
    const canvasNode = (n.data as ArtifactNodeData | undefined)?.node;
    if (canvasNode) openThread(canvasNode, { x: e.clientX, y: e.clientY });
  }, []);

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
        onNodeDoubleClick={onNodeDoubleClick}
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
      {/* Thread focus — the real artifact behind a chip. Highest z of the panels so opening
          one from behind the perspectives/gaps drawers still lands on top. */}
      {openWindows.map((w, i) => (
        <ThreadWindow
          key={w.key}
          node={w.node}
          notebookId={notebookId}
          anchor={w.anchor}
          takenAnchors={openWindows.slice(0, i).map((o) => o.anchor).filter(Boolean) as Point[]}
          z={BASE_WINDOW_Z + w.z}
          onFocus={() => raiseWindow(w.key)}
          onClose={() => closeWindow(w.key)}
        />
      ))}

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

      {/* P4 — orphan intent-elicitation: "what were you exploring here?" → re-assign + research. */}
      {elicit.open && (
        <div className="pointer-events-auto absolute bottom-3 left-1/2 z-30 flex max-h-[70%] w-[min(520px,92%)] -translate-x-1/2 flex-col rounded-xl border border-violet-200 bg-white/97 shadow-2xl backdrop-blur dark:border-violet-800 dark:bg-gray-800/97">
          <div className="flex items-center justify-between gap-2 border-b border-violet-100 px-3 py-2 dark:border-violet-900/50">
            <div className="flex items-center gap-2">
              <Compass className="h-4 w-4 text-violet-500" />
              <p className="text-[12px] font-semibold text-gray-700 dark:text-gray-200">
                What were you exploring here?
              </p>
            </div>
            <button
              type="button"
              onClick={() => setElicit((p) => ({ ...p, open: false }))}
              className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-700"
              title="Close"
              aria-label="Close"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
          {elicit.node && (
            <p className="px-3 pt-2 text-[11px] text-gray-400">
              on{' '}
              <span className="font-medium text-gray-600 dark:text-gray-300">
                {elicit.node.title || 'this thread'}
              </span>
            </p>
          )}
          <div className="flex items-center gap-2 px-3 py-2">
            <input
              type="text"
              autoFocus
              value={elicit.intent}
              onChange={(e) => setElicit((p) => ({ ...p, intent: e.target.value }))}
              onKeyDown={(e) => { if (e.key === 'Enter' && !elicit.busy) onSubmitElicit(); }}
              placeholder="e.g. comparing mRNA vs viral-vector vaccines…"
              className="flex-1 rounded-md border border-gray-200 bg-white px-2.5 py-1.5 text-[12px] text-gray-700 outline-none focus:border-violet-400 dark:border-gray-600 dark:bg-gray-900 dark:text-gray-200"
            />
            <button
              type="button"
              onClick={onSubmitElicit}
              disabled={elicit.busy || !elicit.intent.trim()}
              className="rounded-md bg-violet-600 px-3 py-1.5 text-[11px] font-semibold text-white hover:bg-violet-700 disabled:opacity-50"
            >
              {elicit.busy ? 'Saving…' : 'Explore'}
            </button>
          </div>
          {elicit.error && <p className="px-3 pb-2 text-[11px] text-red-500">{elicit.error}</p>}
          {elicit.done && (
            <div className="border-t border-gray-100 px-3 py-2 dark:border-gray-700">
              {elicit.assignedTopicId ? (
                <p className="text-[11px] font-medium text-emerald-600 dark:text-emerald-400">
                  Connected to “{elicit.suggestions.find((s) => s.id === elicit.assignedTopicId)?.title || 'a topic'}”.
                  I'll research this while you're away.
                </p>
              ) : (
                <p className="text-[11px] text-gray-500 dark:text-gray-400">
                  Kept as its own thread for now — I'll research it while you're away.
                </p>
              )}
              {elicit.suggestions.length > 0 && (
                <div className="mt-1.5">
                  <p className="text-[10px] font-semibold uppercase tracking-wide text-gray-400">Nearest topics</p>
                  <ul className="mt-1 space-y-0.5">
                    {elicit.suggestions.map((s) => (
                      <li
                        key={s.id}
                        className="flex items-center justify-between gap-2 text-[11px] text-gray-600 dark:text-gray-300"
                      >
                        <span className="truncate">{s.title || 'Untitled topic'}</span>
                        <span className="flex-shrink-0 tabular-nums text-gray-400">{Math.round(s.score * 100)}%</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
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
