/**
 * Journey Canvas NODE COMPONENTS — the two react-flow node renderers and their data shapes.
 *
 * Split out of JourneyCanvas.tsx (2026-08-18): that file had grown to ~1,750 lines with a
 * ~1,100-line inner component, and these two renderers are self-contained presentation with
 * no canvas state of their own. Pairs with `journeyTransforms.ts` (the pure layout⇆flow math).
 */
import { Handle, Position, NodeResizer, useReactFlow, type Node, type NodeProps, type NodeTypes } from '@xyflow/react';
import {
  Trash2, Scale, Compass, MessagesSquare, ChevronDown, ChevronRight,
  Mic, Video, HelpCircle, BarChart3, Image, FileText, Files, Circle, Layers3, CircleDashed,
  PlayCircle, Maximize2,
  type LucideIcon,
} from 'lucide-react';
import type { CanvasNode } from '../../services/canvas';

// A latent-connection suggestion as seen from one node (its peer on the other end).
export interface NodeCandidate {
  peerId: string;
  score: number;
  signal: string;
}

// ─── Custom node ─────────────────────────────────────────────────────────────
export type ArtifactNodeData = {
  node: CanvasNode;
  tint: number;
  candidates?: NodeCandidate[];
  onPromote?: (peerId: string) => void;
  onPerspectives?: (node: CanvasNode) => void;
  /** P4 — open the "what were you exploring here?" prompt for an orphan thread. */
  onElicit?: (node: CanvasNode) => void;
  /** A thread outside any topic card — dashed, muted, invites exploration (P4 wires the click). */
  isOrphan?: boolean;
  /**
   * Position of this thread in its card's story, 1-based ("3/7"). The map already CONTAINS
   * the order — the backend sorts card members by real event time and lays them out row-major
   * — it just never drew it, so a journey read as a bag of chips with no direction of travel.
   */
  rank?: { index: number; total: number };
  /** An unresolved question (backend `canvas_gaps`), with the reason for the tooltip. */
  openLoop?: string;
  /** Open this thread's REAL content in the focus panel (play the podcast, read the doc…). */
  onOpen?: (node: CanvasNode, anchor?: { x: number; y: number } | null) => void;
};
export type ArtifactFlowNode = Node<ArtifactNodeData, 'artifact'>;

// A topic GROUP card — the react-flow container its child threads position inside.
export type TopicCardNodeData = {
  node: CanvasNode;
  collapsed: boolean;
  onToggle: (id: string) => void;
  /**
   * What KINDS of thing live in this card, biggest group first. Cards collapse by default,
   * so without this a topic reads as an opaque box with a thread count — you couldn't see
   * that it holds a podcast and two documents without opening it.
   */
  composition?: Array<{ refType: string; count: number }>;
};
export type TopicFlowNode = Node<TopicCardNodeData, 'topicCard'>;

// Every node the canvas renders is one of these two.
export type CanvasFlowNode = ArtifactFlowNode | TopicFlowNode;

const SIGNAL_LABEL: Record<string, string> = {
  concept: 'shared concepts',
  embed: 'similar meaning',
  shared_source: 'shared source',
};

// A thread's `ref_type` → compact chip presentation (medium icon + muted type
// label). Threads render as clean, legible chips — NOT the full artifact body
// squished into a ~200×120 tile (which read as blurry/undefined). Multiple
// ref_types collapse onto the shared 💬 chat icon.
export const THREAD_CHIP: Record<string, { Icon: LucideIcon; label: string }> = {
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

/** Threads whose "open" is really a "play" — the affordance should say so. */
export const PLAYABLE_REFS = new Set(['audio', 'video']);

/** Depth/output facts the backend stashes on a thread's snapshot (canvas_populate). */
export interface ThreadMeta {
  sources?: number;
  answered?: boolean;
  preview?: string;
  topics?: string[];
}

export function threadChip(refType: string): { Icon: LucideIcon; label: string } {
  const hit = THREAD_CHIP[refType];
  if (hit) return hit;
  const label = refType ? refType.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()) : 'Item';
  return { Icon: Circle, label };
}

function ArtifactNode({ id, data, selected }: NodeProps<ArtifactFlowNode>) {
  const rf = useReactFlow();
  const {
    node, tint, candidates, onPromote, onPerspectives, onElicit, isOrphan, rank, openLoop, onOpen,
  } = data;
  const researchInsight = (node.snapshot as { research_insight?: string } | undefined)?.research_insight;

  // Recency tint stays subtle for chips — clamp so threads never read as
  // "blurred/undefined"; orphans keep a slightly lower floor but stay legible.
  const chipOpacity = isOrphan ? Math.max(0.8, tint) : Math.max(0.85, tint);
  const { Icon: ChipIcon, label: chipLabel } = threadChip(node.ref_type);

  // DEPTH + OUTPUT for the chip. A title alone shows that a question was asked but not how far it
  // reached or what came of it — which is most of what makes the map a *journey* rather than an
  // index. `metadata` rides in the snapshot's Artifact envelope (backend: canvas_populate).
  const meta = (node.snapshot as { metadata?: ThreadMeta } | undefined)?.metadata;
  const depthBits: string[] = [];
  if (meta?.sources) depthBits.push(`${meta.sources} source${meta.sources === 1 ? '' : 's'}`);
  if (meta && meta.answered === false) depthBits.push('unanswered');

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
          {/* OPEN — play the podcast, read the document, refresh on the quiz. The chip shows a
              summary by design; this is the way down to the real thing. Playable media get a
              play glyph so the affordance reads as "listen/watch" rather than "expand".
              Double-clicking the node body does the same (onNodeDoubleClick). */}
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              // Pass the click point: media opens in a floating player anchored here.
              onOpen?.(node, { x: e.clientX, y: e.clientY });
            }}
            className="rounded p-0.5 text-gray-400 hover:bg-violet-50 hover:text-violet-600 dark:hover:bg-violet-900/30 dark:hover:text-violet-300"
            title={PLAYABLE_REFS.has(node.ref_type) ? 'Play' : 'Open for a closer look'}
            aria-label={PLAYABLE_REFS.has(node.ref_type) ? 'Play' : 'Open'}
          >
            {PLAYABLE_REFS.has(node.ref_type)
              ? <PlayCircle className="h-3 w-3" />
              : <Maximize2 className="h-3 w-3" />}
          </button>
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
          {/* Sequence — where this step sits in the card's story. Tabular numerals so the
              chips line up down a column instead of jittering. */}
          {rank && (
            <span
              className="ml-auto flex-shrink-0 text-[9.5px] font-semibold tabular-nums text-gray-300 dark:text-gray-600"
              title={`Step ${rank.index} of ${rank.total} in this topic`}
            >
              {rank.index}/{rank.total}
            </span>
          )}
        </div>
        <p className="line-clamp-2 text-[12px] font-medium leading-snug text-gray-800 dark:text-gray-100">
          {node.title || 'Untitled'}
        </p>
        {/* Depth — what the question reached for. */}
        {depthBits.length > 0 && (
          <div className="flex items-center gap-1 text-[10px] text-gray-400 dark:text-gray-500">
            <Layers3 className="h-2.5 w-2.5 flex-shrink-0" />
            <span className="truncate">{depthBits.join(' · ')}</span>
          </div>
        )}
        {/* OPEN LOOP — a question the notebook never really answered. These were only ever
            visible in a side panel; on the node they read as part of the journey ("this
            thread is still hanging") rather than as a separate to-do list. */}
        {openLoop && (
          <div
            className="flex items-center gap-1 text-[10px] font-medium text-amber-600 dark:text-amber-400"
            title={`Open loop — ${openLoop}`}
          >
            <CircleDashed className="h-2.5 w-2.5 flex-shrink-0" />
            <span className="truncate">open loop</span>
          </div>
        )}
        {/* Output — the answer the question produced. */}
        {meta?.preview && (
          <p
            className="line-clamp-2 text-[10.5px] leading-snug text-gray-500 dark:text-gray-400"
            title={meta.preview}
          >
            {meta.preview}
          </p>
        )}
        {/* P4 — a stashed idle-research finding for this (formerly orphan) thread. */}
        {researchInsight && (
          <p
            className="mt-auto line-clamp-2 rounded bg-rose-50 px-1.5 py-1 text-[10px] italic leading-snug text-rose-700 dark:bg-rose-900/25 dark:text-rose-300"
            title={researchInsight}
          >
            💡 {researchInsight}
          </p>
        )}
      </div>

      {/* Orphan affordance (P4) — a thread that hasn't landed in a topic card yet. Click to answer
          "what were you exploring here?" → the intent re-assigns it + kicks off idle research. */}
      {isOrphan && (
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onElicit?.(node);
          }}
          className="nodrag nopan absolute bottom-1 right-1 z-10 flex items-center gap-0.5 rounded-full bg-violet-100/90 px-1.5 py-0.5 text-[9px] font-semibold text-violet-600 transition-colors hover:bg-violet-200 dark:bg-violet-900/40 dark:text-violet-300 dark:hover:bg-violet-800/60"
          title="Not yet part of a topic — tell me what you were exploring"
          aria-label="What were you exploring here?"
        >
          <Compass className="h-2.5 w-2.5" />
          explore
        </button>
      )}
    </div>
  );
}

// ─── Topic GROUP card (P3) ────────────────────────────────────────────────────
// The container react-flow node: child threads position INSIDE it. Renders a
// header (title + one-line synthesis + thread-count chip + collapse chevron);
// the body area below is intentionally transparent so children sit "inside".
function TopicCardNode({ id, data }: NodeProps<TopicFlowNode>) {
  const { node, collapsed, onToggle, composition } = data;
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
            {/* WHAT'S INSIDE — the same chip icons the children use, so a collapsed topic
                still says "2 questions, a podcast and a document" at a glance. */}
            {!!composition?.length && (
              <div className="flex flex-shrink-0 items-center gap-1.5">
                {composition.map(({ refType, count: n }) => {
                  const { Icon, label } = threadChip(refType);
                  return (
                    <span
                      key={refType}
                      className="flex items-center gap-0.5 text-violet-400 dark:text-violet-300/80"
                      title={`${n} ${label}${n === 1 ? '' : 's'}`}
                    >
                      <Icon className="h-3 w-3" />
                      {n > 1 && <span className="text-[9px] font-semibold tabular-nums">{n}</span>}
                    </span>
                  );
                })}
              </div>
            )}
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

export const nodeTypes: NodeTypes = { artifact: ArtifactNode, topicCard: TopicCardNode };