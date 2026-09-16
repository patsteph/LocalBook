/**
 * Standing routing rules, in plain English.
 *
 * A rule is a promise the user made to themselves — "recordings with Sarah go
 * to her 1:1 notebook". They must be able to read it back, see whether it has
 * actually been kept, and revoke it. A rule that files things invisibly is
 * indistinguishable from the app guessing, which is the thing this design
 * exists to avoid.
 */
import { useCallback, useEffect, useState } from 'react';
import {
  deleteRule, describeRule, listRules, setRuleEnabled, type RoutingRule,
} from '../../services/folders';

export function RoutingRulesList({ onChanged }: { onChanged?: () => void }) {
  const [rules, setRules] = useState<RoutingRule[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      setRules((await listRules()).rules);
      setError(null);
    } catch (e: any) {
      setError(e?.message || 'Could not load rules.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const mutate = async (fn: () => Promise<unknown>) => {
    try { await fn(); await load(); onChanged?.(); }
    catch (e: any) { setError(e?.message || 'Failed'); }
  };

  if (loading) return <p className="text-sm text-gray-500">Loading…</p>;

  return (
    <div className="space-y-2">
      <p className="text-xs text-gray-500 dark:text-gray-400">
        These are the only things that file a recording without asking you first.
        Everything else waits for review.
      </p>

      {error && (
        <div className="rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 px-3 py-2 text-sm text-red-700 dark:text-red-300">
          {error}
        </div>
      )}

      {rules.length === 0 && (
        <div className="rounded-lg border border-dashed dark:border-gray-700 px-4 py-6 text-center">
          <p className="text-sm text-gray-600 dark:text-gray-300">No routing rules yet.</p>
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400 max-w-sm mx-auto">
            When you approve a recording you can choose “always route these”, which
            creates a rule here. Until then, every recording is reviewed by you.
          </p>
        </div>
      )}

      {rules.map((r) => (
        <div key={r.id}
             className={`rounded-lg border px-3 py-2.5 ${
               r.enabled
                 ? 'border-gray-200 dark:border-gray-700'
                 : 'border-gray-200 dark:border-gray-800 opacity-60'
             }`}>
          <div className="flex items-start gap-2">
            <span className="text-sm leading-none mt-0.5">{r.enabled ? '⚡' : '⏸'}</span>
            <div className="min-w-0 flex-1">
              <p className="text-sm text-gray-900 dark:text-gray-100">
                {describeRule(r)}
              </p>
              <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                {r.hit_count === 0
                  ? 'Has not routed anything yet'
                  : `Routed ${r.hit_count} recording${r.hit_count === 1 ? '' : 's'}`}
                {!r.enabled && ' · paused'}
              </p>
            </div>
            <button
              onClick={() => mutate(() => setRuleEnabled(r.id, !r.enabled))}
              className="px-2 py-1 text-xs rounded text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800"
            >
              {r.enabled ? 'Pause' : 'Resume'}
            </button>
            <button
              onClick={() => mutate(() => deleteRule(r.id))}
              className="px-2 py-1 text-xs rounded text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20"
              title="Revoke this rule. Recordings it would have filed go back to review."
            >
              Revoke
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}
