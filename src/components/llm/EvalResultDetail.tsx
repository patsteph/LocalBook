import { EvalResult, FeatureParity, gradeBand, GRADE_BADGE } from './evalApi';
import { friendlyModelName } from '../../lib/friendlyModel';

// Shared detail view for a single evaluation run. Rendered by the Evaluator tab
// (auto-shown after a run) and the History tab (on card click). Faithful React
// port of the health-portal's showRunDetails render, themed with Tailwind
// light/dark pairs (no CSS variables).

const READINESS: Record<string, { label: string; icon: string; cls: string }> = {
  ready:  { label: 'Production ready', icon: '✅', cls: 'bg-emerald-50 dark:bg-emerald-900/20 border-emerald-300 dark:border-emerald-700 text-emerald-800 dark:text-emerald-200' },
  viable: { label: 'Viable',           icon: '👍', cls: 'bg-amber-50 dark:bg-amber-900/20 border-amber-300 dark:border-amber-700 text-amber-800 dark:text-amber-200' },
  risky:  { label: 'Risky',            icon: '⚠️', cls: 'bg-red-50 dark:bg-red-900/20 border-red-300 dark:border-red-700 text-red-800 dark:text-red-200' },
};

const VERDICT_BADGE: Record<string, string> = {
  pass:           'bg-emerald-100 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300',
  degraded:       'bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300',
  fail:           'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-300',
  not_applicable: 'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400',
};

const STATUS_DOT: Record<string, string> = {
  pass: 'bg-emerald-500', warn: 'bg-amber-500', fail: 'bg-red-500',
};

function GradeBadge({ score, grade }: { score: number; grade?: string }) {
  return (
    <span className={`inline-block px-2 py-0.5 rounded font-bold text-sm ${GRADE_BADGE[gradeBand(score)]}`}>
      {grade ? `${grade} · ` : ''}{Math.round(score)}/100
    </span>
  );
}

export function EvalResultDetail({ run }: { run: EvalResult }) {
  const pr = run.production_readiness;
  const readiness = pr ? (READINESS[pr.headline] || READINESS.viable) : null;
  const providers = run.providers_used ? Object.entries(run.providers_used) : [];
  const parity: FeatureParity[] = run.feature_parity || [];
  const categories = run.categories ? Object.entries(run.categories) : [];

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
            Overall <GradeBadge score={run.overall_score} grade={run.overall_grade} />
          </div>
          <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">
            {run.run_id} · {new Date(run.timestamp).toLocaleString()}
          </div>
        </div>
        <div className="text-right text-sm text-gray-700 dark:text-gray-300">
          <div>{run.avg_tokens_per_sec != null ? run.avg_tokens_per_sec.toFixed(1) : '—'} tok/s</div>
          <div>{run.avg_ttft_ms != null ? Math.round(run.avg_ttft_ms) : '—'} ms TTFT</div>
        </div>
      </div>

      {/* Readiness banner */}
      {readiness && pr && (
        <div className={`rounded-lg border px-4 py-3 ${readiness.cls}`}>
          <div className="font-semibold flex items-center gap-2">
            <span>{readiness.icon}</span> {readiness.label}
          </div>
          <div className="text-xs mt-1 opacity-90">
            {pr.counts.pass} pass · {pr.counts.degraded} degraded · {pr.counts.fail} fail · {pr.counts.not_applicable} n/a
          </div>
        </div>
      )}

      {/* Providers used */}
      {providers.length > 0 && (
        <div>
          <h4 className="text-sm font-semibold text-gray-900 dark:text-white mb-2">Providers used</h4>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {providers.map(([role, info]) => (
              <div key={role} className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-3 py-2">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-gray-600 dark:text-gray-400 uppercase">{role.replace(/_/g, ' ')}</span>
                  <span className={`px-1.5 py-0.5 text-xs rounded ${
                    info.provider === 'llama_server'
                      ? 'bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300'
                      : info.provider === 'mlx'
                      ? 'bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300'
                      : 'bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300'
                  }`}>{info.provider === 'mlx' ? '⚡ mlx' : info.provider}</span>
                </div>
                <div className="text-sm text-gray-900 dark:text-gray-100 mt-0.5 truncate">{info.model_display || friendlyModelName(info.model)}</div>
                {info.backend_url && <div className="text-xs text-gray-400 dark:text-gray-500 truncate">{info.backend_url}</div>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Feature parity */}
      {parity.length > 0 && (
        <div>
          <h4 className="text-sm font-semibold text-gray-900 dark:text-white mb-2">Feature parity</h4>
          <div className="space-y-1.5">
            {parity.map((f, i) => (
              <div key={i} className="flex items-center justify-between rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-3 py-2">
                <div className="flex items-center gap-2 min-w-0">
                  <span>{f.icon || '•'}</span>
                  <div className="min-w-0">
                    <div className="text-sm text-gray-900 dark:text-gray-100 truncate">{f.feature}</div>
                    {f.note && <div className="text-xs text-gray-400 dark:text-gray-500 truncate">{f.note}</div>}
                  </div>
                </div>
                <div className="flex items-center gap-2 flex-shrink-0">
                  {f.score != null && <span className="text-xs text-gray-500 dark:text-gray-400">{Math.round(f.score)}</span>}
                  <span className={`px-1.5 py-0.5 text-xs rounded ${VERDICT_BADGE[f.verdict] || VERDICT_BADGE.not_applicable}`}>
                    {f.verdict.replace(/_/g, ' ')}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Preflight (collapsible) */}
      {run.preflight?.checks?.length ? (
        <details className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
          <summary className="cursor-pointer px-3 py-2 text-sm font-semibold text-gray-900 dark:text-white select-none">
            Preflight ({run.preflight.checks.length} checks)
          </summary>
          <div className="px-3 pb-3 space-y-1.5">
            {run.preflight.checks.map((c, i) => (
              <div key={i} className="flex items-center gap-2 text-sm">
                <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${STATUS_DOT[c.status] || 'bg-gray-400'}`} />
                <span className="text-gray-700 dark:text-gray-300">{c.name}</span>
                {c.message && <span className="text-gray-400 dark:text-gray-500 truncate">— {c.message}</span>}
              </div>
            ))}
          </div>
        </details>
      ) : null}

      {/* Category breakdown (collapsible) */}
      {categories.length > 0 && (
        <details className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
          <summary className="cursor-pointer px-3 py-2 text-sm font-semibold text-gray-900 dark:text-white select-none">
            Category breakdown ({categories.length})
          </summary>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left border-b border-gray-200 dark:border-gray-700 text-gray-500 dark:text-gray-400">
                  <th className="px-3 py-2 font-medium">Category</th>
                  <th className="px-3 py-2 font-medium">Score</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {categories.map(([key, cat]) => (
                  <tr key={key} className="border-b border-gray-100 dark:border-gray-700/50">
                    <td className="px-3 py-2 text-gray-900 dark:text-gray-100">{cat.display_name || key}</td>
                    <td className="px-3 py-2">
                      {cat.skipped ? <span className="text-gray-400">—</span> : <GradeBadge score={cat.score} grade={cat.grade} />}
                    </td>
                    <td className="px-3 py-2 text-gray-600 dark:text-gray-400">
                      {cat.skipped ? `⊘ Skipped${cat.skip_reason ? ` (${cat.skip_reason})` : ''}` : cat.passed ? '✅ Pass' : '❌ Fail'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}

      {/* Warnings */}
      {run.warnings && run.warnings.length > 0 && (
        <div className="rounded-lg border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/20 px-3 py-2 text-sm text-amber-800 dark:text-amber-200">
          {run.warnings.map((w, i) => <div key={i}>⚠️ {w}</div>)}
        </div>
      )}
    </div>
  );
}
