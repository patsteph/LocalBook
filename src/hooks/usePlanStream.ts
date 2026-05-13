/**
 * usePlanStream — SSE consumer for curator plan_* events.
 *
 * Curator Phase 2b (2026-05-13). Opens GET /curator/events/stream and
 * reconstructs the per-plan state from incremental events. Components
 * that need plan visibility (PlanCard) read from the returned `plans`
 * map keyed by plan_id.
 *
 * Singleton EventSource: a module-level provider opens one connection
 * for the whole app and pushes state updates to subscribed components
 * via React state. Auto-reconnects with exponential backoff on close.
 */
import { useEffect, useState } from 'react';
import { API_BASE_URL } from '../services/api';

export type PlanStepStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped';
export type PlanStatus = 'proposed' | 'running' | 'completed' | 'cancelled' | 'failed';

export interface PlanStep {
  seq: number;
  name: string;
  description?: string;
  status: PlanStepStatus;
  output_summary?: string;
}

export interface PlanWithSteps {
  plan_id: string;
  notebook_id?: string;
  intent?: string;
  summary?: string;
  status: PlanStatus;
  created_at: string;
  steps: PlanStep[];
}

interface CuratorSSEEvent {
  ts: string;
  actor: string;
  action: string;
  intent: string | null;
  notebook_id: string | null;
  payload: Record<string, any>;
  outcome: string | null;
}

// ── Module-level singleton state ────────────────────────────────────────

const planStore = new Map<string, PlanWithSteps>();
const listeners = new Set<() => void>();
let eventSource: EventSource | null = null;
let retryCount = 0;
let retryTimer: number | null = null;

function emitChange(): void {
  listeners.forEach((l) => l());
}

function applyEvent(evt: CuratorSSEEvent): void {
  const planId = evt.payload?.plan_id as string | undefined;
  if (!planId) return;

  const existing = planStore.get(planId);

  switch (evt.action) {
    case 'plan_created': {
      // We don't have the full plan body yet from the create event —
      // the SSE payload only carries plan_id. The first step events
      // will populate the rest. For UX, render a placeholder.
      if (!existing) {
        planStore.set(planId, {
          plan_id: planId,
          notebook_id: evt.notebook_id ?? undefined,
          intent: evt.intent ?? undefined,
          summary: undefined,
          status: 'proposed',
          created_at: evt.ts,
          steps: [],
        });
        emitChange();
      }
      break;
    }
    case 'plan_started': {
      if (existing) {
        existing.status = 'running';
        emitChange();
      }
      break;
    }
    case 'plan_step_started': {
      if (!existing) return;
      const seq = evt.payload?.seq as number;
      const step = existing.steps.find((s) => s.seq === seq);
      if (step) {
        step.status = 'running';
      } else {
        existing.steps.push({
          seq,
          name: `step_${seq}`,
          status: 'running',
        });
        existing.steps.sort((a, b) => a.seq - b.seq);
      }
      emitChange();
      break;
    }
    case 'plan_step_completed': {
      if (!existing) return;
      const seq = evt.payload?.seq as number;
      const summary = evt.payload?.output_summary as string | undefined;
      const step = existing.steps.find((s) => s.seq === seq);
      if (step) {
        step.status = 'done';
        step.output_summary = summary;
      }
      emitChange();
      break;
    }
    case 'plan_completed': {
      if (existing) {
        existing.status = 'completed';
        emitChange();
      }
      break;
    }
    case 'plan_cancelled': {
      if (existing) {
        existing.status = 'cancelled';
        emitChange();
      }
      break;
    }
    case 'plan_failed': {
      if (existing) {
        existing.status = 'failed';
        const seq = evt.payload?.seq as number | undefined;
        if (seq) {
          const step = existing.steps.find((s) => s.seq === seq);
          if (step) {
            step.status = 'failed';
            step.output_summary = evt.payload?.reason as string | undefined;
          }
        }
        emitChange();
      }
      break;
    }
    default:
      // Unknown plan_* action — ignore. Future actions land here.
      break;
  }
}

function openConnection(): void {
  if (eventSource) return;
  try {
    eventSource = new EventSource(`${API_BASE_URL}/curator/events/stream`);

    eventSource.addEventListener('curator_event', (e: MessageEvent) => {
      try {
        const evt = JSON.parse(e.data) as CuratorSSEEvent;
        applyEvent(evt);
      } catch (err) {
        // Bad frame — log and move on.
        // eslint-disable-next-line no-console
        console.warn('[usePlanStream] failed to parse event:', err);
      }
    });

    eventSource.onopen = () => {
      retryCount = 0;
    };

    eventSource.onerror = () => {
      // EventSource will auto-retry, but if the server is genuinely down
      // we close + reopen with exponential backoff to avoid hammering.
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      const delay = Math.min(1000 * Math.pow(2, retryCount), 30000);
      retryCount += 1;
      if (retryTimer !== null) {
        window.clearTimeout(retryTimer);
      }
      retryTimer = window.setTimeout(() => {
        openConnection();
      }, delay);
    };
  } catch (err) {
    // eslint-disable-next-line no-console
    console.warn('[usePlanStream] failed to open EventSource:', err);
  }
}

function ensureConnection(): void {
  if (!eventSource) openConnection();
}

// ── Public hook ─────────────────────────────────────────────────────────

interface UsePlanStreamReturn {
  /** Map of all plans we've seen this session, keyed by plan_id. */
  plans: ReadonlyMap<string, PlanWithSteps>;
  /** Convenience: the most recently-created running plan, if any. */
  activeRunningPlanId: string | null;
  /** Imperative: ask the backend to cancel a running plan. */
  cancelPlan: (planId: string) => Promise<boolean>;
  /**
   * One-shot fetch of a plan's current state via REST. Used when the
   * SSE stream hasn't delivered events for this plan_id yet (race:
   * collection finished before client EventSource opened). Idempotent.
   */
  fetchPlan: (planId: string) => Promise<void>;
}

export function usePlanStream(): UsePlanStreamReturn {
  const [, force] = useState(0);

  useEffect(() => {
    ensureConnection();
    const listener = () => force((n) => n + 1);
    listeners.add(listener);
    return () => {
      listeners.delete(listener);
    };
  }, []);

  // Compute the active running plan (most recently created with status=running).
  let activeRunningPlanId: string | null = null;
  let latestStart = '';
  for (const [id, plan] of planStore.entries()) {
    if (plan.status === 'running' && plan.created_at > latestStart) {
      latestStart = plan.created_at;
      activeRunningPlanId = id;
    }
  }

  return {
    plans: planStore,
    activeRunningPlanId,
    cancelPlan,
    fetchPlan,
  };
}

async function cancelPlan(planId: string): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE_URL}/curator/plans/${encodeURIComponent(planId)}/cancel`, {
      method: 'POST',
    });
    return res.ok;
  } catch {
    return false;
  }
}

// In-flight fetch tracking — dedupes simultaneous calls for the same id.
const inFlightFetches = new Set<string>();

async function fetchPlan(planId: string): Promise<void> {
  if (planStore.has(planId)) return;
  if (inFlightFetches.has(planId)) return;
  inFlightFetches.add(planId);
  try {
    const res = await fetch(`${API_BASE_URL}/curator/plans/${encodeURIComponent(planId)}`);
    if (!res.ok) return;
    const plan = (await res.json()) as PlanWithSteps;
    // Only insert if SSE hasn't beaten us to it. If a later SSE event
    // updates the plan, that's fine — the event handler merges in place.
    if (!planStore.has(planId)) {
      planStore.set(planId, plan);
      emitChange();
    }
  } catch {
    // Silent — PlanCard will continue showing the placeholder. The
    // user can retry by re-running the action.
  } finally {
    inFlightFetches.delete(planId);
  }
}
