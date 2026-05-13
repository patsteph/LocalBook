/**
 * useEngagement — fire-and-forget UI engagement capture.
 *
 * Curator Phase 2b (2026-05-13). POSTs to /curator/engagement so the
 * brain accumulates real signals about what users open / click / dismiss
 * / thumbs. Powers Phase 5 smart morning brief and Phase 4 calibration.
 *
 * Design:
 * - On-action capture (no polling). UI events happen at human speed
 *   so this has zero measurable performance impact.
 * - 300ms debounce by (kind, signal, subject_id) tuple defends against
 *   accidental double-clicks. Backend has no dedup — it stays dumb +
 *   fast.
 * - Silent failure (engagement is observability — never block UI on
 *   error). All errors logged at debug level, never surfaced.
 * - Respects the backend `engagement_tracking_enabled` flag implicitly
 *   via the API returning `{ok:true, suppressed:true}` — we don't
 *   special-case it client-side.
 */
import { useCallback } from 'react';
import { API_BASE_URL } from '../services/api';

export type EngagementKind =
  | 'query'
  | 'source'
  | 'curator_feature'
  | 'brief'
  | 'reflection'
  | 'connection'
  | string;

export type EngagementSignal =
  | 'asked'
  | 'asked_ui'
  | 'opened'
  | 'clicked'
  | 'story_clicked'
  | 'ignored'
  | 'dismissed'
  | 'rejected'
  | 'approved'
  | 'thumbs_up'
  | 'thumbs_down'
  | 'invoked'
  | 'viewed'
  | string;

export interface CaptureOptions {
  subject_type?: string;
  subject_id?: string;
  notebook_id?: string;
  payload?: Record<string, any>;
}

// Module-level debounce state. 300ms window per (kind, signal, subject_id) tuple.
const DEBOUNCE_MS = 300;
const recentSignals = new Map<string, number>();

function makeDebounceKey(
  kind: EngagementKind,
  signal: EngagementSignal,
  subjectId?: string,
): string {
  return `${kind}::${signal}::${subjectId ?? ''}`;
}

function pruneExpired(now: number): void {
  // Cheap cleanup — runs on every capture. Map stays tiny under
  // realistic usage; even at 100 events/min the size is bounded by
  // active tuples within the debounce window.
  for (const [key, ts] of recentSignals.entries()) {
    if (now - ts > DEBOUNCE_MS) {
      recentSignals.delete(key);
    }
  }
}

async function postEngagement(
  kind: EngagementKind,
  signal: EngagementSignal,
  opts: CaptureOptions,
): Promise<void> {
  try {
    await fetch(`${API_BASE_URL}/curator/engagement`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        kind,
        signal,
        subject_type: opts.subject_type,
        subject_id: opts.subject_id,
        notebook_id: opts.notebook_id,
        payload: opts.payload,
      }),
    });
  } catch (err) {
    // eslint-disable-next-line no-console
    console.debug('[useEngagement] capture failed (non-fatal):', err);
  }
}

interface UseEngagementReturn {
  /**
   * Capture a UI engagement event. Fire-and-forget, never blocks.
   *
   * Returns true if the event was dispatched, false if debounced
   * (within 300ms of an identical tuple).
   */
  capture: (
    kind: EngagementKind,
    signal: EngagementSignal,
    opts?: CaptureOptions,
  ) => boolean;
}

export function useEngagement(): UseEngagementReturn {
  const capture = useCallback(
    (
      kind: EngagementKind,
      signal: EngagementSignal,
      opts: CaptureOptions = {},
    ): boolean => {
      const now = Date.now();
      pruneExpired(now);
      const key = makeDebounceKey(kind, signal, opts.subject_id);
      const lastSent = recentSignals.get(key);
      if (lastSent !== undefined && now - lastSent < DEBOUNCE_MS) {
        // Suppressed by debounce — same tuple within 300ms.
        return false;
      }
      recentSignals.set(key, now);
      // Don't await — fire-and-forget.
      void postEngagement(kind, signal, opts);
      return true;
    },
    [],
  );

  return { capture };
}
