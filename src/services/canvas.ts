/**
 * Canvas Service — typed client for the Journey Canvas `/canvas` API.
 *
 * The Journey Canvas is a living, per-notebook spatial-temporal learning map.
 * The backend owns the canonical layout (node positions, edges, viewport); this
 * client mirrors the documented `/canvas` contract and keeps the drag/pan hot
 * paths cheap by debouncing the write-back.
 *
 * Endpoints (per-notebook, `{nb}` = notebook id):
 *   GET    /canvas/layout/{nb}          → CanvasLayout
 *   PUT    /canvas/layout/{nb}          (body: CanvasLayout)
 *   PATCH  /canvas/node/{nb}/{id}       (body: {x, y})            ← drag hot path
 *   POST   /canvas/edge/{nb}            (body: NewEdge)            → CanvasEdge
 *   DELETE /canvas/edge/{nb}/{id}
 *   PUT    /canvas/viewport/{nb}        (body: CanvasViewport)     ← pan/zoom hot path
 *   POST   /canvas/populate/{nb}        → CanvasLayout (seed from notebook)
 */
import { API_BASE_URL, localFetch } from './api';
import type { Artifact } from '../types/artifact';

// ─── Edge states + their visual language (from the journey-canvas spec) ──────
// candidate  = amber   (proposed connection, not yet accepted)
// provenance = aqua    (made-from / derived-from)
// user       = violet  (bold; drawn by the user)
// curator    = lavender(dashed; proposed by the curator brain)
// researched = rose     (backed by an idle-research finding; may carry an insight)
export type EdgeState = 'candidate' | 'provenance' | 'user' | 'curator' | 'researched';

export interface CanvasNode {
  id: string;
  x: number;
  y: number;
  /** Node role. Thread nodes are `chat`/`source`/`artifact`/…; a `'topic'`
   *  node is a GROUP CARD container whose children position RELATIVE to it. */
  kind: string;
  ref_type: string;
  ref_id: string;
  /** The renderable body — an Artifact envelope. Topic cards carry a
   *  `json:topic-card` snapshot `{ payload: { title, synthesis, count } }`. */
  snapshot: Artifact;
  title: string;
  z: number;
  created_at: string;
  /** Optional persisted dimensions (round-tripped via the full-layout PUT). */
  width?: number;
  height?: number;
  /** The topic this thread belongs to (null when it's an orphan). */
  topic_id?: string | null;
  /** The react-flow parent (a `topic:<tid>` card id) when this thread sits
   *  INSIDE a card — its x,y are then RELATIVE to the card. null = orphan
   *  (absolute position in a lane). */
  parent_id?: string | null;
}

export interface CanvasEdge {
  id: string;
  source: string;
  target: string;
  state: EdgeState;
  label?: string;
  meta?: Record<string, unknown> & { insight?: string };
  created_at: string;
}

export interface CanvasViewport {
  x: number;
  y: number;
  zoom: number;
}

export interface CanvasLayout {
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  viewport: CanvasViewport;
}

export interface NewEdge {
  source: string;
  target: string;
  state: EdgeState;
  label?: string;
  id?: string;
  meta?: Record<string, unknown>;
}

/** A visible-node ref sent to the candidate engine (title/text feed embedding similarity). */
export interface CandidateNodeRef {
  id: string;
  ref_type?: string;
  ref_id?: string;
  title?: string;
  text?: string;
}

/** A transient latent-connection suggestion between two visible nodes (never persisted). */
export interface CanvasCandidate {
  a_node: string;
  b_node: string;
  score: number;
  signal: 'concept' | 'embed' | 'shared_source';
}

// Run R3 — a weakly-answered question surfaced as "what to explore next."
export interface CanvasGap {
  query: string;
  reason: string;
  topics: string[];
  ref_id: string;
}

// Run R2 — answer from chatting with a selection (RAG scoped to the selection's sources).
export interface CanvasChatResult {
  answer: string;
  sources: string[];
  source_ids: string[];
  scoped: boolean;
}

// Run R1 — a learning node due for spaced-repetition review.
export interface RecallItem {
  id: string;
  title: string;
  snapshot: Artifact;
  ref_type: string;
  reps: number;
  overdue: number;
}
export interface RecallResult {
  due: RecallItem[];
  due_count: number;
  total: number;
}
export type RecallGrade = 'again' | 'good' | 'easy';

// ─── Debounce helper (per-key trailing-edge) ─────────────────────────────────
// Keeps drag (per-node) and viewport (single) write-backs off the hot path.
const _timers = new Map<string, ReturnType<typeof setTimeout>>();
function debounced(key: string, fn: () => void, ms: number): void {
  const existing = _timers.get(key);
  if (existing) clearTimeout(existing);
  _timers.set(key, setTimeout(() => {
    _timers.delete(key);
    fn();
  }, ms));
}

async function asJson<T>(resp: Response, action: string): Promise<T> {
  if (!resp.ok) throw new Error(`canvas: ${action} failed (HTTP ${resp.status})`);
  const text = await resp.text();
  return (text ? JSON.parse(text) : null) as T;
}

export const canvasService = {
  /** Load the full layout (nodes + edges + viewport) for a notebook. */
  async getLayout(notebookId: string): Promise<CanvasLayout> {
    const resp = await localFetch(`${API_BASE_URL}/canvas/layout/${notebookId}`);
    return asJson<CanvasLayout>(resp, 'getLayout');
  },

  /** Replace the full layout — used for node deletion + resize persistence. */
  async putLayout(notebookId: string, layout: CanvasLayout): Promise<void> {
    const resp = await localFetch(`${API_BASE_URL}/canvas/layout/${notebookId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(layout),
    });
    if (!resp.ok) throw new Error(`canvas: putLayout failed (HTTP ${resp.status})`);
  },

  /** Persist a single node's position (and optionally its resized dimensions). */
  async patchNode(
    notebookId: string, nodeId: string, x: number, y: number,
    width?: number, height?: number,
  ): Promise<void> {
    const body: Record<string, number> = { x, y };
    if (width != null) body.width = width;
    if (height != null) body.height = height;
    const resp = await localFetch(`${API_BASE_URL}/canvas/node/${notebookId}/${nodeId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!resp.ok) throw new Error(`canvas: patchNode failed (HTTP ${resp.status})`);
  },

  /** Debounced variant for the drag/resize hot path (keyed per node). */
  patchNodeDebounced(
    notebookId: string, nodeId: string, x: number, y: number,
    width?: number, height?: number, ms = 250,
  ): void {
    debounced(`node:${notebookId}:${nodeId}`, () => {
      this.patchNode(notebookId, nodeId, x, y, width, height)
        .catch((e) => console.warn('[canvas] patchNode', e));
    }, ms);
  },

  /** Create an edge (user-drawn connections use state:'user'). Returns the persisted edge. */
  async createEdge(notebookId: string, edge: NewEdge): Promise<CanvasEdge> {
    const resp = await localFetch(`${API_BASE_URL}/canvas/edge/${notebookId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(edge),
    });
    return asJson<CanvasEdge>(resp, 'createEdge');
  },

  /** Delete an edge by id. */
  async deleteEdge(notebookId: string, edgeId: string): Promise<void> {
    const resp = await localFetch(`${API_BASE_URL}/canvas/edge/${notebookId}/${edgeId}`, {
      method: 'DELETE',
    });
    if (!resp.ok && resp.status !== 404) {
      throw new Error(`canvas: deleteEdge failed (HTTP ${resp.status})`);
    }
  },

  /** Persist the viewport (pan/zoom). */
  async putViewport(notebookId: string, viewport: CanvasViewport): Promise<void> {
    const resp = await localFetch(`${API_BASE_URL}/canvas/viewport/${notebookId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(viewport),
    });
    if (!resp.ok) throw new Error(`canvas: putViewport failed (HTTP ${resp.status})`);
  },

  /** Debounced viewport persistence (single key — pan/zoom idle write-back). */
  putViewportDebounced(notebookId: string, viewport: CanvasViewport, ms = 500): void {
    debounced(`viewport:${notebookId}`, () => {
      this.putViewport(notebookId, viewport).catch((e) => console.warn('[canvas] putViewport', e));
    }, ms);
  },

  /** Seed the canvas from the notebook's existing artifacts/sources, then return the layout. */
  async populate(notebookId: string): Promise<CanvasLayout> {
    const resp = await localFetch(`${API_BASE_URL}/canvas/populate/${notebookId}`, {
      method: 'POST',
    });
    return asJson<CanvasLayout>(resp, 'populate');
  },

  /** Auto-arrange ("tidy up"): re-cluster + re-position ALL current nodes into a readable
   *  map by similarity, then return the new layout. */
  async relayout(notebookId: string): Promise<CanvasLayout> {
    const resp = await localFetch(`${API_BASE_URL}/canvas/relayout/${notebookId}`, {
      method: 'POST',
    });
    return asJson<CanvasLayout>(resp, 'relayout');
  },

  /**
   * P5 candidate-dot engine — latent connections among the currently-visible nodes.
   * Transient (recomputed, never persisted). POST (not GET): the visible-node payload
   * carries titles/text for embedding similarity, and Fetch forbids a body on GET.
   */
  async getCandidates(
    notebookId: string, nodes: CandidateNodeRef[],
  ): Promise<CanvasCandidate[]> {
    const resp = await localFetch(`${API_BASE_URL}/canvas/candidates/${notebookId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nodes }),
    });
    const data = await asJson<{ candidates: CanvasCandidate[] }>(resp, 'getCandidates');
    return data?.candidates ?? [];
  },

  /** Auto-connect: promote high-confidence candidate pairs into 'curator' (dashed,
   *  suggested) edges, then return the updated layout. */
  async autoConnect(notebookId: string, nodes: CandidateNodeRef[]): Promise<CanvasLayout> {
    const resp = await localFetch(`${API_BASE_URL}/canvas/auto-connect/${notebookId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nodes }),
    });
    return asJson<CanvasLayout>(resp, 'autoConnect');
  },

  // Run R3 — gap detection: questions the notebook answered weakly ("what to explore next").
  async getGaps(notebookId: string): Promise<CanvasGap[]> {
    const resp = await localFetch(`${API_BASE_URL}/canvas/gaps/${notebookId}`);
    const data = await asJson<{ gaps: CanvasGap[] }>(resp, 'getGaps');
    return data.gaps ?? [];
  },

  // Run R2 — chat with a selection: RAG scoped to the sources behind the selected nodes.
  async chat(
    notebookId: string,
    query: string,
    nodes: { ref_type?: string | null; ref_id?: string | null }[],
  ): Promise<CanvasChatResult> {
    const resp = await localFetch(`${API_BASE_URL}/canvas/chat/${notebookId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, nodes }),
    });
    return asJson<CanvasChatResult>(resp, 'chat');
  },

  // Run R1 — recall: learning nodes due for spaced-repetition review.
  async getRecall(notebookId: string): Promise<RecallResult> {
    const resp = await localFetch(`${API_BASE_URL}/canvas/recall/${notebookId}`);
    return asJson<RecallResult>(resp, 'getRecall');
  },

  async reviewRecall(notebookId: string, nodeId: string, grade: RecallGrade): Promise<void> {
    await localFetch(`${API_BASE_URL}/canvas/recall/${notebookId}/${nodeId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ grade }),
    });
  },
};
