/**
 * Journey Canvas TRANSFORMS — the pure layout⇆react-flow math and visual language.
 *
 * Split out of JourneyCanvas.tsx (2026-08-18). Everything here is a PURE function or a
 * constant: no hooks, no fetches, no component state. That is the point — it is the part
 * of the canvas whose behaviour can be asserted directly (see journeyTransforms.test.ts).
 */
import { MarkerType, type Edge } from '@xyflow/react';
import type { CanvasNode, CanvasEdge, CandidateNodeRef, EdgeState } from '../../services/canvas';
import type { ArtifactFlowNode, CanvasFlowNode } from './journeyNodeTypes';

// Unordered pair key so a candidate/edge is de-duped regardless of direction.
export function pairKey(a: string, b: string): string {
  return a < b ? `${a}|${b}` : `${b}|${a}`;
}


// ─── Edge visual language (from the journey-canvas spec) ─────────────────────
export interface EdgeVisual {
  stroke: string;
  width: number;
  dash?: string;
  animated?: boolean;
}
export const EDGE_VISUAL: Record<EdgeState, EdgeVisual> = {
  candidate: { stroke: '#f59e0b', width: 1.5, dash: '4 4', animated: true }, // amber
  provenance: { stroke: '#06b6d4', width: 1.5 },                              // aqua
  user: { stroke: '#8b5cf6', width: 2.75 },                                   // violet, bold
  curator: { stroke: '#c4b5fd', width: 1.5, dash: '6 4' },                    // lavender, dashed
  researched: { stroke: '#f43f5e', width: 2 },                                // rose
  // Tension reads as a WARNING, and is dashed because a disagreement is a live question,
  // not a settled fact like a made-from link.
  tension: { stroke: '#ea580c', width: 2, dash: '2 3' },                      // orange, fine dash
};

export const EDGE_LEGEND: { state: EdgeState; label: string }[] = [
  { state: 'candidate', label: 'Candidate' },
  { state: 'provenance', label: 'Made-from' },
  { state: 'user', label: 'Yours' },
  { state: 'curator', label: 'Curator' },
  { state: 'researched', label: 'Researched' },
  { state: 'tension', label: 'Disagree' },
];

// Recency tint: newer edges/nodes read stronger; older ones fade toward 0.4.
export function recencyOpacity(createdAt: string | undefined): number {
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
export const DAY_MS = 86_400_000;
export const TIME_WINDOWS: { label: string; ms: number | null }[] = [
  { label: 'All', ms: null },
  { label: '24h', ms: DAY_MS },
  { label: '7d', ms: 7 * DAY_MS },
  { label: '30d', ms: 30 * DAY_MS },
];

// Collapsed topic cards shrink to a header-only strip (view-state; never persisted).
export const TOPIC_HEADER_H = 56;

// ─── Layout ⇆ react-flow conversion ──────────────────────────────────────────

/** Extra per-node facts derived from the WHOLE layout (sequence, composition) or a side fetch (gaps). */
export type NodeExtras = {
  rank?: { index: number; total: number };
  openLoop?: string;
  composition?: Array<{ refType: string; count: number }>;
};

/**
 * Rank every card's children into a 1-based reading order.
 *
 * Derived from POSITION (row-major: y, then x), not from `created_at`. The backend already
 * sorted members by real event time before placing them (`canvas_layout_topics`), so position
 * IS the chronological rank — and reading it back off the layout guarantees the numbers match
 * what the eye sees. Re-sorting by timestamp here would risk disagreeing with the placement,
 * because chat nodes are stamped in LOCAL time while source/artifact nodes are stamped in UTC
 * (a known clock-domain split); position sidesteps that entirely.
 */
/**
 * What kinds of thing each topic card holds, biggest group first — the at-a-glance
 * composition drawn in the card header. Cards collapse by default, so without this a topic
 * is an opaque box with a thread count.
 */
export function computeComposition(nodes: CanvasNode[]): Map<string, Array<{ refType: string; count: number }>> {
  const perCard = new Map<string, Map<string, number>>();
  for (const n of nodes) {
    if (!n.parent_id || !n.ref_type) continue;
    let counts = perCard.get(n.parent_id);
    if (!counts) perCard.set(n.parent_id, (counts = new Map()));
    counts.set(n.ref_type, (counts.get(n.ref_type) ?? 0) + 1);
  }
  const out = new Map<string, Array<{ refType: string; count: number }>>();
  for (const [cardId, counts] of perCard) {
    out.set(
      cardId,
      [...counts.entries()]
        .map(([refType, count]) => ({ refType, count }))
        // Biggest group first, then alphabetical so the row is stable across renders.
        .sort((a, b) => b.count - a.count || a.refType.localeCompare(b.refType)),
    );
  }
  return out;
}

export function computeRanks(nodes: CanvasNode[]): Map<string, { index: number; total: number }> {
  const byParent = new Map<string, CanvasNode[]>();
  for (const n of nodes) {
    if (!n.parent_id) continue;
    const sibs = byParent.get(n.parent_id);
    if (sibs) sibs.push(n);
    else byParent.set(n.parent_id, [n]);
  }
  const out = new Map<string, { index: number; total: number }>();
  for (const sibs of byParent.values()) {
    if (sibs.length < 2) continue; // "1/1" is noise, not a sequence
    const ordered = [...sibs].sort((a, b) => (a.y - b.y) || (a.x - b.x));
    ordered.forEach((n, i) => out.set(n.id, { index: i + 1, total: ordered.length }));
  }
  return out;
}

export function toFlowNode(n: CanvasNode, extras?: NodeExtras): CanvasFlowNode {
  // Topic GROUP card — the absolute-positioned container. Its children arrive
  // AFTER it in the layout array (react-flow's parent-before-child requirement).
  if (n.kind === 'topic') {
    return {
      id: n.id,
      type: 'topicCard',
      position: { x: n.x, y: n.y },
      data: { node: n, collapsed: true, onToggle: () => {}, composition: extras?.composition },
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
    data: {
      node: n,
      tint: recencyOpacity(n.created_at),
      isOrphan,
      rank: extras?.rank,
      openLoop: extras?.openLoop,
    },
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

export function toFlowEdge(e: CanvasEdge): Edge {
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
export function fromFlowNode(fn: CanvasFlowNode): CanvasNode {
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
export function toCandidateRef(n: CanvasNode): CandidateNodeRef {
  const payload = (n.snapshot as { payload?: unknown } | undefined)?.payload;
  const text = typeof payload === 'string' ? payload : '';
  return { id: n.id, ref_type: n.ref_type, ref_id: n.ref_id, title: n.title, text };
}