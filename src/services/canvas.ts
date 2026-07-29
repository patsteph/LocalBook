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
  kind: string;
  ref_type: string;
  ref_id: string;
  /** The renderable body — an Artifact envelope. */
  snapshot: Artifact;
  title: string;
  z: number;
  created_at: string;
  /** Optional persisted dimensions (round-tripped via the full-layout PUT). */
  width?: number;
  height?: number;
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

  /** Persist a single node's position. Fire-and-forget from the drag handler. */
  async patchNode(notebookId: string, nodeId: string, x: number, y: number): Promise<void> {
    const resp = await localFetch(`${API_BASE_URL}/canvas/node/${notebookId}/${nodeId}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ x, y }),
    });
    if (!resp.ok) throw new Error(`canvas: patchNode failed (HTTP ${resp.status})`);
  },

  /** Debounced variant for the drag hot path (keyed per node). */
  patchNodeDebounced(notebookId: string, nodeId: string, x: number, y: number, ms = 250): void {
    debounced(`node:${notebookId}:${nodeId}`, () => {
      this.patchNode(notebookId, nodeId, x, y).catch((e) => console.warn('[canvas] patchNode', e));
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
};
