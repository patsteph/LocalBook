import { useCallback, useEffect, useState } from 'react';
import { evalApi, SidecarStatus as SidecarState } from './evalApi';

// Compact llama-server sidecar status + start/stop. The sidecar serves
// GGUF/MLX models for evaluation runs; controls belong with LLM management.
export function SidecarStatus() {
  const [s, setS] = useState<SidecarState | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setS(await evalApi.getSidecar());
    } catch {
      setS(null);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const start = async () => {
    setBusy(true); setErr(null);
    try { await evalApi.startSidecar(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    setBusy(false); refresh();
  };
  const stop = async () => {
    if (!window.confirm('Stop the llama-server sidecar? Any running evaluation using a sidecar model will fail.')) return;
    setBusy(true); setErr(null);
    try { await evalApi.stopSidecar(); } catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    setBusy(false); refresh();
  };

  const dot = !s ? 'bg-gray-400'
    : s.running && s.healthy ? 'bg-emerald-500'
    : s.running ? 'bg-amber-500'
    : 'bg-gray-400';

  const model = (s?.model_path || '').split('/').pop() || 'no model configured';
  const label = !s ? 'Sidecar status unknown'
    : s.running && s.healthy ? `Running · ${model}${s.uptime_seconds ? ` · ${Math.round(s.uptime_seconds)}s` : ''}${s.owned ? '' : ' · external'}`
    : s.running ? `Starting… ${s.pid ? `pid ${s.pid}` : ''}`
    : s.model_exists ? `Stopped · ${model} available` : `Stopped · model missing`;

  return (
    <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-3 py-2 flex items-center gap-3 flex-wrap">
      <span className={`w-2.5 h-2.5 rounded-full flex-shrink-0 ${dot}`} />
      <span className="text-sm text-gray-700 dark:text-gray-300 flex-1 min-w-0 truncate" title={err || label}>
        <span className="font-medium">llama-server</span> · {err ? <span className="text-red-600 dark:text-red-400">{err}</span> : label}
      </span>
      <div className="flex items-center gap-2 flex-shrink-0">
        <button
          onClick={start}
          disabled={busy || (!!s && s.running && s.healthy)}
          className="px-2.5 py-1 text-xs rounded-lg bg-blue-600 hover:bg-blue-700 text-white disabled:bg-gray-200 dark:disabled:bg-gray-700 disabled:text-gray-400 disabled:cursor-not-allowed"
        >
          {busy ? '…' : 'Start'}
        </button>
        <button
          onClick={stop}
          disabled={busy || !s || !s.running || !s.owned}
          title={s && s.running && !s.owned ? 'Cannot stop an externally-launched sidecar' : ''}
          className="px-2.5 py-1 text-xs rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Stop
        </button>
      </div>
    </div>
  );
}
