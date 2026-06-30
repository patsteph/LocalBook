/**
 * useMorningBriefFetcher (V6, 2026-06-30)
 *
 * Extracts the morning-brief / weekly-wrap fetch logic that was duplicated in
 * App.tsx (a boot-time trigger with a models-loading retry, and a
 * visibilitychange trigger). The fetch sequence — should-show → weekly OR brief
 * → set state + mark-shown + save — now lives in one place; the two triggers are
 * preserved as two effects so behaviour is unchanged:
 *   • boot: fire once when the backend becomes ready (retries while models warm)
 *   • visibility: re-check when the app regains focus, only if no brief is shown yet
 */
import { useCallback, useEffect } from 'react';
import { localFetch, API_BASE_URL } from '../services/api';

interface UseMorningBriefFetcherOpts {
  backendReady: boolean;
  morningBrief: unknown;
  weeklyWrap: unknown;
  setMorningBrief: (brief: any) => void;
  setWeeklyWrap: (wrap: any) => void;
}

export function useMorningBriefFetcher({
  backendReady,
  morningBrief,
  weeklyWrap,
  setMorningBrief,
  setWeeklyWrap,
}: UseMorningBriefFetcherOpts): void {
  // The shared fetch sequence. Stable across renders (only depends on the
  // setters), so both effects can reuse it. `retries` drives the boot-time
  // models-loading backoff; the visibility trigger calls it with the default 0.
  const fetchBrief = useCallback((retries = 0) => {
    const localHour = new Date().getHours();
    localFetch(`${API_BASE_URL}/curator/morning-brief/should-show?local_hour=${localHour}`)
      .then(r => (r.ok ? r.json() : null))
      .then(check => {
        if (check?.reason === 'models_loading' && retries < 6) {
          // Models still warming up — retry in 10s (up to ~60s total).
          setTimeout(() => fetchBrief(retries + 1), 10000);
          return;
        }
        if (check?.should_show_weekly) {
          return localFetch(`${API_BASE_URL}/curator/weekly-wrap`)
            .then(r => (r.ok ? r.json() : null))
            .then(wrap => {
              if (wrap?.narrative) {
                setWeeklyWrap(wrap);
                localFetch(`${API_BASE_URL}/curator/morning-brief/mark-shown`, { method: 'POST' }).catch(() => {});
                localFetch(`${API_BASE_URL}/curator/weekly-wrap/save`, {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify(wrap),
                }).catch(() => {});
              }
            });
        } else if (check?.should_show) {
          const hoursAway = check.hours_away || 12;
          return localFetch(`${API_BASE_URL}/curator/morning-brief?hours_away=${hoursAway}`)
            .then(r => (r.ok ? r.json() : null))
            .then(brief => {
              if (brief && (brief.notebooks?.length > 0 || brief.cross_notebook_insight || brief.narrative)) {
                setMorningBrief(brief);
                localFetch(`${API_BASE_URL}/curator/morning-brief/mark-shown`, { method: 'POST' }).catch(() => {});
                localFetch(`${API_BASE_URL}/curator/morning-brief/save`, {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify(brief),
                }).catch(() => {});
              }
            });
        }
      })
      .catch(() => {});
  }, [setMorningBrief, setWeeklyWrap]);

  // Boot trigger — fires once when the backend transitions to ready.
  useEffect(() => {
    if (!backendReady) return;
    fetchBrief();
  }, [backendReady, fetchBrief]);

  // Visibility trigger — re-check on focus, but only if nothing is shown yet.
  useEffect(() => {
    if (!backendReady) return;
    const handleVisibilityChange = () => {
      if (document.visibilityState !== 'visible') return;
      if (morningBrief || weeklyWrap) return;
      fetchBrief();
    };
    document.addEventListener('visibilitychange', handleVisibilityChange);
    return () => document.removeEventListener('visibilitychange', handleVisibilityChange);
  }, [backendReady, morningBrief, weeklyWrap, fetchBrief]);
}
