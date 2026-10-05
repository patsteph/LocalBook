/**
 * What a realtime message invalidates.
 *
 * 2026-09-25: source-count badges in the notebook list went stale as sources arrived —
 * only a UI reload or an app relaunch brought them up to date. Every `source_updated`
 * consumer had scoped itself to the SELECTED notebook:
 *
 *   - `App.tsx`'s handler ran its body only when `data.notebook_id === selectedNotebookId`
 *   - `SourcesList`'s handler filtered the same way, AND it is the thing that bumps the
 *     notebook-list counter (via `onSourcesChange`) — while living inside a collapsible
 *     drawer that UNMOUNTS its children when closed (`{isOpen && ...}`)
 *
 * So a source landing in another notebook, or in the current one with the Sources drawer
 * collapsed, refreshed nothing. But the notebook list shows a count for EVERY notebook,
 * so its invalidation scope is not "the selected notebook" — it is "any source, anywhere".
 *
 * Keeping that distinction here, as a pure function, is deliberate: the fault was a wrong
 * scope decision, and the repo's frontend tests are pure-function tests (no DOM harness),
 * so this is the form in which the rule can actually be pinned.
 */

import type { LBEventName } from '../lib/events';

export interface RefreshScope {
  /** The notebook LIST — and therefore every notebook's source-count badge. */
  notebooks: boolean;
  /** Panels bound to the notebook currently on screen (its source list, canvas, toasts). */
  selectedNotebook: boolean;
  /** Library refresh pulses to emit (sync only: another Mac changed these kinds). */
  pulses?: LBEventName[];
}

/**
 * 2026-10-01: after the first mini ⇄ MBP sync the screens kept showing the old notebooks
 * and sources until a manual reload. Sync changes rows directly in the database, so none
 * of the per-feature events fired; the backend now sends one `sync_applied` naming the
 * tables that changed, and this maps them onto what the screens already listen for.
 */
const SYNC_PULSES: Record<string, LBEventName> = {
  sources: 'sourcesUpdated',
  highlights: 'sourcesUpdated',
  canvas_notes: 'notesUpdated',
  content_generations: 'contentUpdated',
  audio_generations: 'audioUpdated',
  video_generations: 'videoUpdated',
  visual_generations: 'visualsUpdated',
  infographic_generations: 'infographicsUpdated',
  quiz_generations: 'quizzesUpdated',
};

export const NO_REFRESH: RefreshScope = { notebooks: false, selectedNotebook: false };

/** Decide what a constellation-socket message invalidates. */
export function refreshScopeFor(
  message: { type?: string; data?: { notebook_id?: string | null } } | null | undefined,
  selectedNotebookId: string | null,
): RefreshScope {
  if (message?.type === 'sync_applied') {
    const tables: string[] = (message.data as { tables?: string[] } | undefined)?.tables ?? [];
    const pulses = [...new Set(tables.map((t) => SYNC_PULSES[t]).filter(Boolean))] as LBEventName[];
    // Any synced change can touch any notebook, the selected one included.
    return { notebooks: true, selectedNotebook: !!selectedNotebookId, pulses };
  }
  if (message?.type !== 'source_updated') return NO_REFRESH;

  const notebookId = message?.data?.notebook_id ?? null;
  return {
    // Unconditional: counts are displayed for notebooks the user is NOT looking at, and
    // an event carrying no notebook_id still means the corpus changed.
    notebooks: true,
    selectedNotebook: !!notebookId && notebookId === selectedNotebookId,
  };
}
