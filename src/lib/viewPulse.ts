/**
 * viewPulse — gentle "something happened over there" signal for the main nav.
 *
 * 2026-06-02. Designed for restraint. The pulse must feel like the app is
 * paying attention without nagging:
 *  - one cycle (~1.8s of fade-in/out)
 *  - coalesced within a 30s window (subsequent fires for the same view
 *    are dropped so the user isn't getting strobe-flashed)
 *  - suppressed if the user is already on that view (you're already
 *    looking at it; no need to point at it)
 *
 * Surface API:
 *   pulseView('library')           // fire a pulse
 *   useViewPulse('library', cb)    // subscribe (returns the cleanup)
 *   setActiveView('chat')          // suppression hint — the nav strip
 *                                  // calls this on view change so pulses
 *                                  // for the active view get dropped
 */
import { useEffect, useRef } from 'react';
import { PanelView } from '../components/canvas/types';

type PulseKind = Extract<PanelView, 'chat' | 'library' | 'constellation' | 'timeline' | 'curator'>;

const COALESCE_WINDOW_MS = 30_000;
const PULSE_DURATION_MS = 1800;

const lastPulseAt: Partial<Record<PulseKind, number>> = {};
const listeners = new Map<PulseKind, Set<(at: number) => void>>();
let activeView: PulseKind | null = null;

export function setActiveView(view: PulseKind | string | null) {
  activeView = (view as PulseKind) || null;
}

export function pulseView(view: PulseKind) {
  // Suppress when the user is already on that view.
  if (activeView === view) return;
  // Coalesce within window.
  const now = Date.now();
  const last = lastPulseAt[view] || 0;
  if (now - last < COALESCE_WINDOW_MS) return;
  lastPulseAt[view] = now;
  const set = listeners.get(view);
  if (set) set.forEach(cb => cb(now));
}

/**
 * React hook — subscribe to pulses for a single view. Receives the
 * fire-timestamp; useful for triggering a CSS animation by changing key
 * (or any other re-render trigger).
 */
export function useViewPulse(view: PulseKind, cb: (at: number) => void) {
  const cbRef = useRef(cb);
  cbRef.current = cb;
  useEffect(() => {
    const stable = (at: number) => cbRef.current(at);
    let set = listeners.get(view);
    if (!set) { set = new Set(); listeners.set(view, set); }
    set.add(stable);
    return () => { set?.delete(stable); };
  }, [view]);
}

export const PULSE_DURATION = PULSE_DURATION_MS;
