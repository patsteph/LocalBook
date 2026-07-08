import { useCallback, useEffect, useState } from 'react';
import { evalApi, RunSummary, EvalResult, gradeBand, GRADE_BADGE } from './evalApi';
import { EvalResultDetail } from './EvalResultDetail';

// History tab: the top runs (backend returns them ranked by score) as clickable
// cards → shared detail view. Faithful port of the portal's historical-runs grid.
export function EvalHistoryPanel() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<EvalResult | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const { runs: r } = await evalApi.getResults();
      setRuns(r || []);
    } catch {
      setRuns([]);
    }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const openRun = async (runId: string) => {
    setLoadingDetail(true);
    setSelected(null);
    try {
      const { result } = await evalApi.getResult(runId);
      setSelected(result);
    } catch { /* ignore */ }
    setLoadingDetail(false);
  };

  if (selected || loadingDetail) {
    return (
      <div className="p-4 space-y-4">
        <button
          onClick={() => setSelected(null)}
          className="text-sm text-blue-600 dark:text-blue-400 hover:underline"
        >
          ← Back to history
        </button>
        {loadingDetail
          ? <div className="text-center text-sm text-gray-500 dark:text-gray-400 py-8">Loading…</div>
          : selected && <EvalResultDetail run={selected} />}
      </div>
    );
  }

  return (
    <div className="p-4 space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-600 dark:text-gray-400">Top runs, ranked by overall score.</p>
        <button onClick={load} className="text-xs text-blue-600 dark:text-blue-400 hover:underline">Refresh</button>
      </div>
      {loading ? (
        <div className="text-center text-sm text-gray-500 dark:text-gray-400 py-8">Loading…</div>
      ) : runs.length === 0 ? (
        <div className="text-center text-sm text-gray-500 dark:text-gray-400 py-8 rounded-lg border border-dashed border-gray-300 dark:border-gray-700">
          No evaluation runs yet. Run one from the Evaluator tab to start benchmarking.
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {runs.map((run) => (
            <button
              key={run.run_id}
              onClick={() => openRun(run.run_id)}
              className="text-left rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700/50 p-4 transition-colors"
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm font-medium text-gray-900 dark:text-gray-100">{new Date(run.timestamp).toLocaleString()}</span>
                <span className={`px-2 py-0.5 rounded font-bold text-sm ${GRADE_BADGE[gradeBand(run.overall_score)]}`}>
                  {Math.round(run.overall_score)}/100
                </span>
              </div>
              <div className="text-xs text-gray-500 dark:text-gray-400 space-y-0.5">
                <div>Main: <b className="text-gray-700 dark:text-gray-300">{run.main_model || '?'}</b></div>
                <div>Fast: <b className="text-gray-700 dark:text-gray-300">{run.fast_model || '?'}</b></div>
                <div>Time: {Math.round(run.total_time_seconds / 60)} min</div>
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
