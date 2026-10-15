import { useCallback, useEffect, useState } from 'react';
import { API_BASE_URL, localFetch } from '../../services/api';

/**
 * Switching the embedding (search) model — services/embedding_migration.py.
 *
 * Never a plain swap: the new model's index is built BESIDE the current one while search
 * keeps working, then the switch happens when LocalBook next starts, and the previous index
 * is kept so it can be switched back. This panel drives that: pre-checks → build (with
 * progress, stoppable, resumable) → switch → roll back / remove the old index.
 */

type Check = { ok: boolean; free_bytes?: number; needed_bytes?: number };
type Status = {
  live_model: string;
  target: string | null;
  running: boolean;
  stage: string | null;
  error: string | null;
  tables_total: number;
  tables_done: number;
  rows_done: number;
  ready: boolean;
  switch_pending: boolean;
  rollback_pending: boolean;
  previous: { model: string; bytes: number; switched_at: string } | null;
};

const short = (id?: string | null) => (id ?? '').split('/').pop() ?? '';
const mb = (b?: number) => `${Math.round((b ?? 0) / 1048576)} MB`;
const CHECK_LABEL: Record<string, string> = {
  different_model: 'A different model from the one in use',
  downloaded: 'The model is downloaded',
  disk: 'Enough free space for a second index',
  not_running: 'No other switch running',
  no_switch_pending: 'No switch already waiting for a restart',
};

async function call(path: string, method = 'GET', body?: unknown) {
  const r = await localFetch(`${API_BASE_URL}/embeddings/migration${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data?.detail ?? `Request failed (${r.status})`);
  return data;
}

export function EmbeddingSwitchPanel({ target, onClose }: { target: string | null; onClose: () => void }) {
  const [st, setSt] = useState<Status | null>(null);
  const [pre, setPre] = useState<{ ready: boolean; checks: Record<string, Check> } | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setSt(await call(''));
      if (target) setPre(await call(`/preflight?model=${encodeURIComponent(target)}`));
    } catch (e: any) {
      setMsg(e.message);
    }
  }, [target]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (!st?.running) return;
    const t = setInterval(() => void refresh(), 2000);
    return () => clearInterval(t);
  }, [st?.running, refresh]);

  const act = async (fn: () => Promise<any>) => {
    setBusy(true); setMsg(null);
    try {
      const out = await fn();
      if (out?.reason) setMsg(out.reason);
      if (out?.restart_required) setMsg('Quit and reopen LocalBook to finish — the switch happens as it starts.');
      await refresh();
    } catch (e: any) {
      setMsg(e.message);
    } finally {
      setBusy(false);
    }
  };

  const building = st?.running;
  const forTarget = st?.target && target && st.target === target;
  const pct = st && st.tables_total ? Math.round((st.tables_done / st.tables_total) * 100) : 0;
  const btn = 'rounded-lg px-3 py-1.5 text-xs font-medium disabled:opacity-40';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="w-full max-w-lg rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900 p-5 shadow-xl"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-base font-semibold text-gray-900 dark:text-gray-100">
              {target ? `Switch search to ${short(target)}` : 'Search model'}
            </h3>
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              In use now: {short(st?.live_model)}. The new index is built alongside the current one, so
              search keeps working. The switch happens when LocalBook next starts, and you can switch back.
            </p>
          </div>
          <button onClick={onClose} className="text-lg leading-none text-gray-400 hover:text-gray-600 dark:hover:text-gray-200">×</button>
        </div>

        {target && !forTarget && !building && pre && (
          <div className="mt-4 space-y-1 text-xs">
            {Object.entries(pre.checks).map(([k, c]) => (
              <div key={k} className={c.ok ? 'text-gray-600 dark:text-gray-300' : 'text-amber-600 dark:text-amber-400'}>
                {c.ok ? '✓' : '✗'} {CHECK_LABEL[k] ?? k}
                {k === 'disk' && !c.ok ? ` (needs ${mb(c.needed_bytes)}, ${mb(c.free_bytes)} free)` : ''}
              </div>
            ))}
            <button disabled={!pre.ready || busy} onClick={() => act(() => call('/start', 'POST', { model: target }))}
                    className={`${btn} mt-2 bg-blue-600 text-white hover:bg-blue-700`}>
              Build the new index
            </button>
          </div>
        )}

        {st?.target && (building || st.stage) && !st.switch_pending && (
          <div className="mt-4 text-sm">
            <div className="text-gray-700 dark:text-gray-200">
              {building ? `Building the ${short(st.target)} index…` : st.ready ? `The ${short(st.target)} index is ready.`
                : st.stage === 'failed' ? `The build stopped: ${st.error}` : `Build ${st.stage}.`}
            </div>
            <div className="mt-2 h-1.5 w-full rounded bg-gray-200 dark:bg-gray-700">
              <div className="h-1.5 rounded bg-blue-600" style={{ width: `${st.ready ? 100 : pct}%` }} />
            </div>
            <div className="mt-1 text-xs text-gray-500">
              {st.tables_done} of {st.tables_total} notebooks · {st.rows_done} passages · no AI model calls, only embedding
            </div>
            <div className="mt-3 flex gap-2">
              {building && <button disabled={busy} onClick={() => act(() => call('/stop', 'POST'))}
                                   className={`${btn} border border-gray-300 dark:border-gray-600`}>Stop (resumes later)</button>}
              {!building && !st.ready && st.target &&
                <button disabled={busy} onClick={() => act(() => call('/start', 'POST', { model: st.target }))}
                        className={`${btn} bg-blue-600 text-white`}>Resume</button>}
              {st.ready && <button disabled={busy} onClick={() => act(() => call('/switch', 'POST'))}
                                   className={`${btn} bg-emerald-600 text-white hover:bg-emerald-700`}>Switch to {short(st.target)}</button>}
            </div>
          </div>
        )}

        {(st?.switch_pending || st?.rollback_pending) && (
          <div className="mt-4 text-sm text-gray-700 dark:text-gray-200">
            {st.switch_pending ? 'Switch requested.' : 'Switch back requested.'} Quit and reopen LocalBook to finish.
            <button disabled={busy} onClick={() => act(() => call('/cancel', 'POST'))}
                    className={`${btn} ml-2 border border-gray-300 dark:border-gray-600`}>Cancel</button>
          </div>
        )}

        {st?.previous && !st.rollback_pending && (
          <div className="mt-4 rounded-lg border border-gray-200 dark:border-gray-700 p-3 text-xs text-gray-600 dark:text-gray-300">
            The previous index ({short(st.previous.model)}, {mb(st.previous.bytes)}) is kept so you can switch back.
            <div className="mt-2 flex gap-2">
              <button disabled={busy} onClick={() => act(() => call('/rollback', 'POST'))}
                      className={`${btn} border border-gray-300 dark:border-gray-600`}>Switch back</button>
              <button disabled={busy} onClick={() => act(() => call('/previous', 'DELETE'))}
                      className={`${btn} border border-red-300 text-red-600 dark:border-red-800 dark:text-red-400`}>Remove it</button>
            </div>
          </div>
        )}

        {msg && <p className="mt-3 text-sm text-gray-700 dark:text-gray-200">{msg}</p>}
      </div>
    </div>
  );
}
