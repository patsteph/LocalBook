import { useCallback, useEffect, useRef, useState } from 'react';
import { evalApi, EvalStatus, EvalResult, HardwareResponse, gradeBand, GRADE_BADGE } from './evalApi';
import { EvalResultDetail } from './EvalResultDetail';
import { SidecarStatus } from './SidecarStatus';

const POLL_MS = 2000;

function mmss(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

// Wave 9.6 — marks a role that's being tested on the Apple MLX engine, so the
// evaluator makes clear which stack produced the numbers (user #4).
function EngineTag() {
  return (
    <span
      title="This role runs on the Apple MLX engine for this evaluation."
      className="shrink-0 px-1.5 py-0.5 text-[10px] font-bold rounded bg-gradient-to-r from-amber-500 to-orange-500 text-white tracking-wide"
    >
      ⚡MLX
    </span>
  );
}

// The Evaluator tab: hardware/combo context, sidecar controls, a full-eval
// runner with live progress, and the latest result detail. Faithful React port
// of the health-portal evaluator, themed with Tailwind light/dark pairs.
export function EvaluatorPanel() {
  const [hw, setHw] = useState<HardwareResponse | null>(null);
  const [status, setStatus] = useState<EvalStatus | null>(null);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<EvalResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stopPolling = useCallback(() => {
    if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
  }, []);

  const loadLatest = useCallback(async () => {
    try {
      const { result: r } = await evalApi.getLatest();
      if (r) setResult(r);
    } catch { /* none yet */ }
  }, []);

  const poll = useCallback(async () => {
    try {
      const st = await evalApi.getStatus();
      setStatus(st);
      if (st.error) {
        setError(st.error);
        setRunning(false);
        stopPolling();
        return;
      }
      if (!st.running) {
        setRunning(false);
        stopPolling();
        await loadLatest(); // show the freshly-persisted run
      }
    } catch {
      // transient — keep polling
    }
  }, [stopPolling, loadLatest]);

  const startPolling = useCallback(() => {
    stopPolling();
    pollRef.current = setInterval(poll, POLL_MS);
    poll();
  }, [poll, stopPolling]);

  // Mount: hardware + latest result, and resume live view if a run is in flight.
  useEffect(() => {
    (async () => {
      try { setHw(await evalApi.getHardware()); } catch { /* ignore */ }
      await loadLatest();
      try {
        const st = await evalApi.getStatus();
        if (st.running) { setRunning(true); startPolling(); }
      } catch { /* ignore */ }
    })();
    return stopPolling;
  }, [loadLatest, startPolling, stopPolling]);

  const run = async () => {
    setError(null);
    setResult(null);
    setRunning(true);
    setStatus(null);
    try {
      await evalApi.run();
      startPolling();
    } catch (e) {
      setRunning(false);
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const liveScores = status?.results_so_far ? Object.entries(status.results_so_far) : [];

  return (
    <div className="p-4 space-y-4">
      {/* Hardware / combo context */}
      <div className="rounded-lg border-l-4 border-purple-500 border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-4">
        <div className="flex items-center gap-2 mb-3">
          <span className="text-sm font-semibold text-gray-900 dark:text-white">💻 Test environment</span>
          {hw?.hardware.tier && (
            <span className="text-xs uppercase px-2 py-0.5 rounded-full bg-purple-500 text-white">{hw.hardware.tier}</span>
          )}
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-center">
          <div>
            <div className="text-xs text-gray-500 dark:text-gray-400 uppercase">Processor</div>
            <div className="text-base font-semibold text-purple-600 dark:text-purple-400">{hw?.hardware.chip || '—'}</div>
          </div>
          <div>
            <div className="text-xs text-gray-500 dark:text-gray-400 uppercase">Memory</div>
            <div className="text-base font-semibold text-gray-900 dark:text-gray-100">{hw ? `${hw.hardware.memory_gb} GB` : '—'}</div>
          </div>
          <div>
            <div className="text-xs text-gray-500 dark:text-gray-400 uppercase">Main model</div>
            <div className="text-base font-semibold text-blue-600 dark:text-blue-400 truncate flex items-center justify-center gap-1">
              {hw?.combo.main_engine === 'mlx' && <EngineTag />}
              <span className="truncate">{hw?.combo.main_model || '—'}</span>
            </div>
          </div>
          <div>
            <div className="text-xs text-gray-500 dark:text-gray-400 uppercase">Fast model</div>
            <div className="text-base font-semibold text-emerald-600 dark:text-emerald-400 truncate flex items-center justify-center gap-1">
              {hw?.combo.fast_engine === 'mlx' && <EngineTag />}
              <span className="truncate">{hw?.combo.fast_model || '—'}</span>
            </div>
          </div>
        </div>
      </div>

      <SidecarStatus />

      {/* Run control */}
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <p className="text-sm text-gray-600 dark:text-gray-400">
          Exercises the full RAG, generation, and multimodal pipeline (~10-category suite).
        </p>
        <button
          onClick={run}
          disabled={running}
          className="px-4 py-2 text-sm font-medium rounded-lg bg-blue-600 hover:bg-blue-700 text-white disabled:bg-gray-300 dark:disabled:bg-gray-700 disabled:text-gray-500 disabled:cursor-not-allowed"
        >
          {running ? 'Evaluating…' : '🧪 Run full evaluation'}
        </button>
      </div>

      {error && (
        <div className="rounded-lg border border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/20 px-4 py-3 text-sm text-red-700 dark:text-red-300">
          Evaluation failed: {error}
        </div>
      )}

      {/* Live progress */}
      {running && (
        <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-4 space-y-3">
          <div className="flex items-center justify-between text-sm font-medium text-gray-900 dark:text-white">
            <span>{status ? `Phase ${status.phase}/${status.total_phases}: ${status.phase_name}` : 'Starting…'}</span>
            <span className="tabular-nums text-gray-500 dark:text-gray-400">{mmss(status?.elapsed_seconds || 0)}</span>
          </div>
          <div className="h-2 rounded-full bg-gray-200 dark:bg-gray-700 overflow-hidden">
            <div className="h-full bg-blue-500 transition-all duration-500" style={{ width: `${status?.progress_percent || 0}%` }} />
          </div>
          {status?.current_test && (
            <div className="text-xs font-mono text-gray-600 dark:text-gray-400">Current: {status.current_test}</div>
          )}
          {liveScores.length > 0 && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
              {liveScores.map(([name, res]) => (
                <div key={name} className="rounded-lg border-l-2 border-blue-500 bg-gray-50 dark:bg-gray-900/40 px-2.5 py-1.5">
                  <div className="text-[11px] uppercase text-gray-500 dark:text-gray-400 truncate">{name.replace(/_/g, ' ')}</div>
                  <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                    {Math.round(res.score)} <span className="text-xs font-normal text-gray-500">({res.grade})</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Latest / just-finished result */}
      {!running && result && (
        <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/30 p-4">
          <div className="flex items-center gap-2 mb-3">
            <span className={`inline-block px-2 py-0.5 rounded font-bold text-sm ${GRADE_BADGE[gradeBand(result.overall_score)]}`}>Latest run</span>
          </div>
          <EvalResultDetail run={result} />
        </div>
      )}

      {!running && !result && !error && (
        <div className="text-center text-sm text-gray-500 dark:text-gray-400 py-8">
          No evaluation runs yet. Run one to benchmark the current combo.
        </div>
      )}
    </div>
  );
}
