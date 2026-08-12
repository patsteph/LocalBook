/**
 * Client-side Quality Signals reporter.
 *
 * Quality Signals only ever observed the BACKEND. That is how three chat charts stayed broken for
 * months (found 2026-08-12): they emitted a well-formed `json-chart` fence whose payload the
 * renderer could not read, so the UI drew "Unsupported chart type: undefined" and nothing anywhere
 * recorded it. A render that silently degrades is exactly the near-miss the ledger exists to
 * catch — and only the client can see it.
 *
 * Rules this file exists to enforce:
 *  - **Never throw, never block.** Reporting is observability; it must not affect what the user sees.
 *  - **Dedupe per session.** A broken renderer inside a list would otherwise fire on every item and
 *    every re-render, drowning the ledger it is meant to inform (and skewing the recurrence counts
 *    that promote signals into Evaluator regression cases).
 */
import { API_BASE_URL, localFetch } from '../services/api';

/** Types the backend allowlists for client reports (`api/signals.py::_CLIENT_TYPES`). */
export type ClientSignalType = 'render_failed' | 'degraded' | 'empty' | 'fallback';

export interface ClientSignal {
  type: ClientSignalType;
  component: string;
  detail: string;
  /** Recurrence key — repeats of the same key group together in the Rough Edges panel. */
  key?: string;
  severity?: 'info' | 'notable' | 'warn';
  notebookId?: string;
}

const seen = new Set<string>();

export function reportSignal(sig: ClientSignal): void {
  try {
    const dedupeKey = `${sig.type}|${sig.component}|${sig.key || sig.detail}`;
    if (seen.has(dedupeKey)) return;
    seen.add(dedupeKey);

    void localFetch(`${API_BASE_URL}/signals/record`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        type: sig.type,
        component: sig.component,
        detail: sig.detail.slice(0, 300),
        key: sig.key || '',
        severity: sig.severity || 'warn',
        notebook_id: sig.notebookId || '',
      }),
    }).catch(() => {
      // Backend down / offline is not worth surfacing — the render already happened.
      seen.delete(dedupeKey);   // allow a later retry
    });
  } catch {
    /* observability must never break a render */
  }
}
