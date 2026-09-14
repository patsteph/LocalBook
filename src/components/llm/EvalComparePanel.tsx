/**
 * EvalComparePanel — side-by-side comparison of two evaluation runs.
 *
 * `POST /evaluator/results/compare` has existed since the Evaluator shipped with NO frontend
 * caller — the fifth instance of this repo's backend-complete/no-caller pattern. It is also the
 * surface the MLX-vs-Ollama cutover decision gets judged on, so it needs one.
 *
 * The design point that matters: **an invalid comparison must refuse to render as a verdict.**
 * Two runs can be numerically comparable and still meaningless — if one fell back to Ollama
 * mid-run, or sampled one query, or swapped. Those are surfaced as a blocking banner rather
 * than a footnote, because a plausible-looking delta table is worse than no table.
 */
import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, ArrowRight, RefreshCw } from 'lucide-react';

import { evalApi, type CompareResponse, type MetricDelta, type RunSummary } from './evalApi';

/** Metrics where LOWER is better — the arrow colour has to know. */
const LOWER_IS_BETTER = new Set([
  'avg_ttft_ms', 'ttft_p50', 'ttft_p95', 'total_run_time_seconds',
  'peak_rss_gb', 'peak_system_used_gb', 'mlx_peak_gb', 'mlx_active_end_gb', 'swap_out_delta',
]);

const LABELS: Record<string, string> = {
  avg_tokens_per_sec: 'Throughput (mean tok/s)',
  tps_p50: 'Throughput p50',
  tps_p05: 'Throughput p05 (slow tail)',
  avg_ttft_ms: 'Time to first token (mean)',
  ttft_p50: 'TTFT p50',
  ttft_p95: 'TTFT p95',
  total_run_time_seconds: 'Total run time (s)',
  perf_samples: 'Perf samples',
  peak_rss_gb: 'Peak RSS (GB)',
  peak_system_used_gb: 'Peak system used (GB)',
  min_system_available_gb: 'Min available (GB)',
  mlx_peak_gb: 'MLX peak (GB)',
  mlx_active_end_gb: 'MLX still resident at end (GB)',
  swap_out_delta: 'Swap-out during run',
};

function DeltaCell({ metric, d }: { metric: string; d: MetricDelta }) {
  if (d?.delta === null || d?.delta === undefined) {
    return <span className="text-gray-400">—</span>;
  }
  // `perf_samples` is a count, not a performance metric: more is better but it is not a
  // regression signal, so it stays neutral.
  const neutral = metric === 'perf_samples';
  const better = LOWER_IS_BETTER.has(metric) ? d.delta < 0 : d.delta > 0;
  const cls = neutral ? 'text-gray-500'
    : d.delta === 0 ? 'text-gray-500'
      : better ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400';
  return (
    <span className={`tabular-nums font-medium ${cls}`}>
      {d.delta > 0 ? '+' : ''}{d.delta}
      {d.pct !== null && d.pct !== undefined && ` (${d.pct > 0 ? '+' : ''}${d.pct}%)`}
    </span>
  );
}

function MetricTable({ title, deltas, sideA, sideB }: {
  title: string;
  deltas: Record<string, MetricDelta>;
  sideA?: Record<string, number | null>;
  sideB?: Record<string, number | null>;
}) {
  const rows = Object.keys(deltas).filter(
    (k) => deltas[k]?.a !== null || deltas[k]?.b !== null,
  );
  if (!rows.length) {
    return (
      <div className="mt-4">
        <h4 className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">{title}</h4>
        <p className="mt-1 text-[12px] text-gray-500">
          Not recorded for these runs — runs from before 2026-08-19 carry no memory or
          distribution data.
        </p>
      </div>
    );
  }
  return (
    <div className="mt-4">
      <h4 className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">{title}</h4>
      <div className="mt-1 overflow-x-auto">
        <table className="w-full text-[12px]">
          <tbody>
            {rows.map((k) => (
              <tr key={k} className="border-b border-gray-100 last:border-0 dark:border-gray-700">
                <td className="py-1 pr-3 text-gray-600 dark:text-gray-300">{LABELS[k] ?? k}</td>
                <td className="py-1 pr-3 text-right tabular-nums text-gray-500">{deltas[k]?.a ?? (sideA?.[k] ?? '—')}</td>
                <td className="py-1 pr-3 text-right tabular-nums text-gray-500">{deltas[k]?.b ?? (sideB?.[k] ?? '—')}</td>
                <td className="py-1 text-right"><DeltaCell metric={k} d={deltas[k]} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function EngineChips({ engines }: { engines?: Record<string, string> }) {
  if (!engines || !Object.keys(engines).length) {
    return <span className="text-[10px] text-gray-400">engine unrecorded</span>;
  }
  return (
    <div className="flex flex-wrap gap-1">
      {Object.entries(engines).map(([role, eng]) => (
        <span
          key={role}
          className={`rounded px-1.5 py-0.5 text-[9.5px] font-semibold ${
            eng === 'mlx'
              ? 'bg-violet-100 text-violet-700 dark:bg-violet-900/40 dark:text-violet-300'
              : 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300'
          }`}
        >
          {role}:{eng}
        </span>
      ))}
    </div>
  );
}

export function EvalComparePanel() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [a, setA] = useState('');
  const [b, setB] = useState('');
  const [data, setData] = useState<CompareResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    evalApi.getResults()
      .then(({ runs: r }) => {
        setRuns(r);
        // Default to the two most recent — the common case is "did my last change regress?"
        if (r.length >= 2) { setA(r[1].run_id); setB(r[0].run_id); }
      })
      .catch((e) => setError(String(e)));
  }, []);

  const compare = async () => {
    if (!a || !b || a === b) return;
    setLoading(true); setError(null); setData(null);
    try {
      setData(await evalApi.compare(a, b));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  };

  const scoreDelta = useMemo(
    () => (data ? Math.round((data.run_b.overall_score - data.run_a.overall_score) * 10) / 10 : 0),
    [data],
  );

  const label = (r: RunSummary) =>
    `${r.main_model} · ${new Date(r.timestamp).toLocaleString()} · ${Math.round(r.overall_score)}`;

  return (
    <div className="p-4">
      <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-100">Compare runs</h3>
      <p className="mt-0.5 text-[12px] text-gray-500 dark:text-gray-400">
        Two runs side by side — quality, speed and memory. Used to judge an engine change before
        committing to it.
      </p>

      {runs.length < 2 ? (
        <p className="mt-4 text-[12px] text-gray-500">
          Two completed runs are needed to compare. Run the Evaluator twice.
        </p>
      ) : (
        <>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <select
              value={a} onChange={(e) => setA(e.target.value)}
              className="min-w-0 flex-1 rounded border border-gray-300 bg-white px-2 py-1 text-[12px] dark:border-gray-600 dark:bg-gray-800"
            >
              {runs.map((r) => <option key={r.run_id} value={r.run_id}>{label(r)}</option>)}
            </select>
            <ArrowRight className="h-4 w-4 flex-shrink-0 text-gray-400" />
            <select
              value={b} onChange={(e) => setB(e.target.value)}
              className="min-w-0 flex-1 rounded border border-gray-300 bg-white px-2 py-1 text-[12px] dark:border-gray-600 dark:bg-gray-800"
            >
              {runs.map((r) => <option key={r.run_id} value={r.run_id}>{label(r)}</option>)}
            </select>
            <button
              type="button" onClick={compare} disabled={loading || !a || !b || a === b}
              className="flex-shrink-0 rounded bg-violet-600 px-3 py-1 text-[12px] font-semibold text-white hover:bg-violet-700 disabled:opacity-50"
            >
              {loading ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : 'Compare'}
            </button>
          </div>

          {error && (
            <div className="mt-3 rounded border border-red-200 bg-red-50 px-3 py-2 text-[12px] text-red-700 dark:border-red-900 dark:bg-red-950/50 dark:text-red-300">
              {error}
            </div>
          )}

          {data && (
            <>
              {/* A comparison that cannot be trusted must say so BEFORE the numbers, not after. */}
              {!data.validity.comparable && (
                <div className="mt-3 rounded border border-amber-300 bg-amber-50 px-3 py-2 dark:border-amber-800 dark:bg-amber-950/50">
                  <div className="flex items-center gap-1.5 text-[12px] font-semibold text-amber-800 dark:text-amber-300">
                    <AlertTriangle className="h-3.5 w-3.5" /> These runs are not safely comparable
                  </div>
                  <ul className="mt-1 list-disc pl-5 text-[11.5px] text-amber-800 dark:text-amber-300">
                    {data.validity.problems.map((p) => <li key={p}>{p}</li>)}
                  </ul>
                </div>
              )}
              {!data.validity.same_hardware && (
                <p className="mt-2 text-[11.5px] text-amber-700 dark:text-amber-400">
                  Different hardware — speed and memory deltas reflect the machines, not the change.
                </p>
              )}

              <div className="mt-3 grid grid-cols-2 gap-3">
                {[data.run_a, data.run_b].map((side, i) => (
                  <div key={side.run_id} className="rounded-lg border border-gray-200 p-2.5 dark:border-gray-700">
                    <p className="text-[10px] uppercase tracking-wide text-gray-400">{i === 0 ? 'A' : 'B'}</p>
                    <p className="text-lg font-bold tabular-nums text-gray-800 dark:text-gray-100">
                      {Math.round(side.overall_score)} <span className="text-[12px] font-normal text-gray-400">{side.overall_grade}</span>
                    </p>
                    <div className="mt-1"><EngineChips engines={side.engines} /></div>
                    {!!side.engine_fallbacks && (
                      <p className="mt-1 text-[10.5px] font-medium text-amber-600 dark:text-amber-400">
                        {side.engine_fallbacks} engine fallback(s)
                      </p>
                    )}
                  </div>
                ))}
              </div>

              <p className="mt-2 text-[12px] text-gray-600 dark:text-gray-300">
                Overall:{' '}
                <span className={`font-semibold tabular-nums ${
                  scoreDelta > 0 ? 'text-emerald-600 dark:text-emerald-400'
                    : scoreDelta < 0 ? 'text-red-600 dark:text-red-400' : 'text-gray-500'}`}>
                  {scoreDelta > 0 ? '+' : ''}{scoreDelta}
                </span>{' '}points (B vs A)
              </p>

              <MetricTable title="Performance" deltas={data.perf_deltas}
                           sideA={data.run_a.perf} sideB={data.run_b.perf} />
              <MetricTable title="Memory" deltas={data.memory_deltas}
                           sideA={data.run_a.memory} sideB={data.run_b.memory} />
            </>
          )}
        </>
      )}
    </div>
  );
}
