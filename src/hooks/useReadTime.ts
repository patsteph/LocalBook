/**
 * useReadTime — IntersectionObserver-based dwell-time tracker.
 *
 * 2026-05-23. Attach a ref to any element representing content the user
 * is meant to read; the hook accumulates the time the element was actually
 * visible (>=50% in viewport) and fires a single engagement event on
 * unmount with the total dwell. Used for Fix #4 (brief read-time
 * telemetry) but is fully generic.
 *
 * Usage:
 *   const ref = useReadTime({
 *     kind: 'brief',
 *     signal: 'read_time',
 *     subjectType: 'morning_brief',
 *     subjectId: briefId,
 *     notebookId,
 *     payload: { voice: currentVoice },
 *   });
 *   return <div ref={ref}>...</div>;
 *
 * Notes:
 * - We don't fire if visible <1 second (avoids noise from quick scrolls).
 * - We don't fire if browser tab is hidden (pageVisibilityAPI gate).
 * - Fires once at unmount or when the consumer remounts with a new key.
 */
import { useEffect, useRef } from 'react';
import { useEngagement, EngagementKind, EngagementSignal, CaptureOptions } from './useEngagement';

export interface UseReadTimeOptions {
  kind: EngagementKind;
  signal: EngagementSignal;
  subjectType: string;
  subjectId: string;
  notebookId?: string | null;
  payload?: Record<string, any>;
  /** Min visible ms before we count the read (default 1000). */
  minVisibleMs?: number;
  /** Visibility threshold (default 0.5 = ≥50% visible). */
  visibilityThreshold?: number;
}

export function useReadTime(opts: UseReadTimeOptions): (el: HTMLDivElement | null) => void {
  const { capture } = useEngagement();

  // Use refs to keep latest values without re-binding the observer.
  const cumulativeMsRef = useRef(0);
  const visibleSinceRef = useRef<number | null>(null);
  const lastFiredRef = useRef<string | null>(null);
  const elRef = useRef<HTMLElement | null>(null);

  // Latest opts via ref so observer callback stays stable.
  const optsRef = useRef(opts);
  optsRef.current = opts;

  const flush = () => {
    // Stop the timer if it's running.
    if (visibleSinceRef.current !== null) {
      cumulativeMsRef.current += performance.now() - visibleSinceRef.current;
      visibleSinceRef.current = null;
    }
    const ms = Math.round(cumulativeMsRef.current);
    const o = optsRef.current;
    const fireKey = `${o.kind}::${o.signal}::${o.subjectId}`;
    if (ms < (o.minVisibleMs ?? 1000)) return;
    // Debounce double-fires for the same subject in this hook instance.
    if (lastFiredRef.current === fireKey) return;
    lastFiredRef.current = fireKey;
    const capOpts: CaptureOptions = {
      subject_type: o.subjectType,
      subject_id: o.subjectId,
      notebook_id: o.notebookId || undefined,
      payload: { ...(o.payload || {}), read_ms: ms },
    };
    capture(o.kind, o.signal, capOpts);
    cumulativeMsRef.current = 0;
  };

  // Stable callback ref so children can attach via `ref={refCallback}`.
  const refCallback = (el: HTMLDivElement | null) => {
    elRef.current = el;
  };

  useEffect(() => {
    const el = elRef.current;
    if (!el) return;

    const onVisible = () => {
      if (visibleSinceRef.current === null && document.visibilityState === 'visible') {
        visibleSinceRef.current = performance.now();
      }
    };
    const onHidden = () => {
      if (visibleSinceRef.current !== null) {
        cumulativeMsRef.current += performance.now() - visibleSinceRef.current;
        visibleSinceRef.current = null;
      }
    };

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.intersectionRatio >= (optsRef.current.visibilityThreshold ?? 0.5)) {
          onVisible();
        } else {
          onHidden();
        }
      },
      { threshold: [0, optsRef.current.visibilityThreshold ?? 0.5, 1] },
    );
    observer.observe(el);

    const onVisChange = () => {
      if (document.visibilityState === 'hidden') onHidden();
    };
    document.addEventListener('visibilitychange', onVisChange);

    return () => {
      observer.disconnect();
      document.removeEventListener('visibilitychange', onVisChange);
      flush();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opts.subjectId]);

  // Also flush when subjectId changes mid-mount (different brief surfaced).
  useEffect(() => {
    return () => flush();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [opts.subjectId]);

  return refCallback;
}
