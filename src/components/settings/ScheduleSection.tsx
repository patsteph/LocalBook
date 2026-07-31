/**
 * ScheduleSection — the Unified Schedule Viewer (2.2.0).
 *
 * One surface for everything the app schedules/times: every agent + all
 * background infrastructure. Rung A/B: read everything; edit only the
 * live-bidirectional set (per-notebook Collector cadence, delegated to the
 * Collector config writer). Infrastructure schedules are read-only behind an
 * "advanced" disclosure. Code-defined schedules become editable in Rung C.
 */
import { useEffect, useState } from 'react';
import { scheduleService } from '../../services/schedule';
import type { ScheduleRow, LiveSchedule } from '../../services/schedule';

// Reuse the Collector's frequency labels so the two UIs read identically.
const FREQUENCY_LABELS: Record<string, string> = {
  hourly: 'Every Hour',
  every_2_hours: 'Every 2 Hours',
  every_4_hours: 'Every 4 Hours',
  every_8_hours: 'Every 8 Hours',
  twice_daily: 'Twice Daily',
  daily: 'Daily',
  every_3_days: 'Every 3 Days',
  weekly: 'Weekly',
  manual: 'Manual Only',
};

const AGENT_BADGE: Record<string, string> = {
  collector: 'bg-teal-50 text-teal-700 dark:bg-teal-900/30 dark:text-teal-300 border-teal-300 dark:border-teal-700',
  curator: 'bg-purple-50 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300 border-purple-300 dark:border-purple-700',
  correspondent: 'bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300 border-amber-300 dark:border-amber-700',
  research: 'bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300 border-emerald-300 dark:border-emerald-700',
  memory: 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300 border-blue-300 dark:border-blue-700',
  system: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300 border-gray-300 dark:border-gray-600',
};

const CATEGORY_ORDER: { id: string; label: string; icon: string }[] = [
  { id: 'agents', label: 'Agents', icon: '🤖' },
  { id: 'email', label: 'Email', icon: '📬' },
  { id: 'memory', label: 'Memory', icon: '🧠' },
  { id: 'infrastructure', label: 'Infrastructure', icon: '⚙️' },
];

function humanInterval(seconds?: number | null): string {
  if (seconds == null) return '—';
  if (seconds < 1) return `${seconds * 1000}ms`;
  if (seconds < 60) return `Every ${Math.round(seconds)}s`;
  if (seconds < 3600) return `Every ${Math.round(seconds / 60)} min`;
  if (seconds < 86400) {
    const h = seconds / 3600;
    return `Every ${Number.isInteger(h) ? h : h.toFixed(1)}h`;
  }
  const d = seconds / 86400;
  return d === 1 ? 'Daily' : `Every ${Number.isInteger(d) ? d : d.toFixed(1)}d`;
}

function cadenceLabel(row: ScheduleRow): string {
  if (row.cadence_kind === 'reactive') return 'Event-driven';
  if (row.cadence_kind === 'per_notebook') {
    return FREQUENCY_LABELS[row.frequency || ''] || row.frequency || '—';
  }
  return humanInterval(row.interval_seconds);
}

function timeAgo(iso?: string | null): string {
  if (!iso) return 'Never';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return 'Never';
  const diff = Date.now() - then;
  const past = diff >= 0;
  const s = Math.abs(diff) / 1000;
  let out: string;
  if (s < 60) out = `${Math.round(s)}s`;
  else if (s < 3600) out = `${Math.round(s / 60)}m`;
  else if (s < 86400) out = `${Math.round(s / 3600)}h`;
  else out = `${Math.round(s / 86400)}d`;
  return past ? `${out} ago` : `in ${out}`;
}

interface CollectorEdit {
  frequency: string;
  maxItems: number | undefined;
}

export function ScheduleSection() {
  const [rows, setRows] = useState<ScheduleRow[]>([]);
  const [live, setLive] = useState<LiveSchedule | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [edits, setEdits] = useState<Record<string, CollectorEdit>>({});
  const [savingId, setSavingId] = useState<string | null>(null);
  const [savedId, setSavedId] = useState<string | null>(null);
  const [togglingId, setTogglingId] = useState<string | null>(null);
  const [runningId, setRunningId] = useState<string | null>(null);
  const [ranId, setRanId] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await scheduleService.getSchedules();
      setRows(data.schedules || []);
      setLive(data.live || null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load schedules');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const editFor = (row: ScheduleRow): CollectorEdit =>
    edits[row.id] || {
      frequency: row.frequency || 'daily',
      maxItems: row.max_items_per_run ?? undefined,
    };

  const setEdit = (id: string, patch: Partial<CollectorEdit>) => {
    setEdits((prev) => {
      const base = prev[id] || { frequency: 'daily', maxItems: undefined };
      return { ...prev, [id]: { ...base, ...patch } };
    });
  };

  const saveCollector = async (row: ScheduleRow) => {
    const edit = editFor(row);
    setSavingId(row.id);
    setError(null);
    try {
      await scheduleService.updateSchedule(row.id, {
        frequency: edit.frequency,
        ...(edit.maxItems != null ? { max_items_per_run: edit.maxItems } : {}),
      });
      setSavedId(row.id);
      setTimeout(() => setSavedId((cur) => (cur === row.id ? null : cur)), 2000);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Update failed');
    } finally {
      setSavingId(null);
    }
  };

  const toggleEnabled = async (row: ScheduleRow) => {
    setTogglingId(row.id);
    setError(null);
    try {
      await scheduleService.setEnabled(row.id, !row.enabled);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Toggle failed');
    } finally {
      setTogglingId(null);
    }
  };

  const runNow = async (row: ScheduleRow) => {
    setRunningId(row.id);
    setError(null);
    try {
      await scheduleService.runNow(row.id);
      setRanId(row.id);
      setTimeout(() => setRanId((cur) => (cur === row.id ? null : cur)), 2500);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Run failed');
    } finally {
      setRunningId(null);
    }
  };

  const grouped = (category: string, advanced: boolean) =>
    rows.filter((r) => r.category === category && !!r.advanced === advanced);

  const renderRow = (row: ScheduleRow) => {
    const badge = AGENT_BADGE[row.agent] || AGENT_BADGE.system;
    const isCollector = row.editable && row.cadence_kind === 'per_notebook';
    const edit = editFor(row);

    return (
      <div
        key={row.id}
        className="p-3 border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-medium text-gray-900 dark:text-white">
                {row.name}
              </span>
              <span className={`px-1.5 py-0.5 text-[10px] font-semibold uppercase rounded border ${badge}`}>
                {row.agent}
              </span>
              {row.tier && (
                <span className="px-1.5 py-0.5 text-[10px] font-medium rounded bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400">
                  {row.tier}
                </span>
              )}
              {row.overridden && (
                <span className="px-1.5 py-0.5 text-[10px] font-medium rounded bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300">
                  overridden
                </span>
              )}
              {!row.enabled && (
                <span className="px-1.5 py-0.5 text-[10px] font-medium rounded bg-gray-200 text-gray-500 dark:bg-gray-600 dark:text-gray-300">
                  disabled
                </span>
              )}
            </div>
            {row.note && (
              <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">{row.note}</p>
            )}
            {(row.last_run || row.next_due) && (
              <p className="text-[11px] text-gray-400 dark:text-gray-500 mt-1">
                Last run {timeAgo(row.last_run)}
                {row.next_due ? ` · Next ${timeAgo(row.next_due)}` : ''}
                {row.overdue ? ' · overdue' : ''}
              </p>
            )}
          </div>

          <div className="flex-shrink-0 text-right">
            <div className="text-sm font-semibold text-gray-700 dark:text-gray-200">
              {cadenceLabel(row)}
            </div>
            {row.env_var && (
              <div className="text-[10px] text-gray-400 dark:text-gray-500 mt-0.5">
                via env
              </div>
            )}
            {row.managed_in && (
              <div className="text-[10px] text-gray-400 dark:text-gray-500 mt-0.5">
                edit in {row.managed_in}
              </div>
            )}
          </div>
        </div>

        {!isCollector && (row.can_disable || row.can_run_now !== undefined) && (
          <div className="mt-3 flex items-center gap-2 flex-wrap border-t border-gray-100 dark:border-gray-700 pt-2.5">
            {row.can_disable && (
              <button
                onClick={() => toggleEnabled(row)}
                disabled={togglingId === row.id}
                title={row.enabled ? 'Turn this schedule off' : 'Turn this schedule on'}
                className={`inline-flex items-center gap-1.5 px-2.5 py-1 text-xs font-medium rounded-full border transition-colors disabled:opacity-50 ${
                  row.enabled
                    ? 'bg-green-50 text-green-700 border-green-300 dark:bg-green-900/30 dark:text-green-300 dark:border-green-700'
                    : 'bg-gray-100 text-gray-500 border-gray-300 dark:bg-gray-700 dark:text-gray-400 dark:border-gray-600'
                }`}
              >
                <span
                  className={`w-2 h-2 rounded-full ${
                    row.enabled ? 'bg-green-500' : 'bg-gray-400'
                  }`}
                />
                {togglingId === row.id ? '…' : row.enabled ? 'On' : 'Off'}
              </button>
            )}
            <button
              onClick={() => runNow(row)}
              disabled={!row.can_run_now || runningId === row.id}
              title={
                row.can_run_now
                  ? 'Run this actor once, now'
                  : row.run_now_reason || 'This actor cannot be run on demand'
              }
              className="px-2.5 py-1 text-xs font-medium rounded border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
            >
              {runningId === row.id
                ? 'Running…'
                : ranId === row.id
                ? 'Triggered ✓'
                : 'Run now'}
            </button>
          </div>
        )}

        {isCollector && (
          <div className="mt-3 flex items-end gap-2 flex-wrap border-t border-gray-100 dark:border-gray-700 pt-3">
            <label className="flex flex-col text-[11px] text-gray-500 dark:text-gray-400">
              Frequency
              <select
                value={edit.frequency}
                onChange={(e) => setEdit(row.id, { frequency: e.target.value })}
                className="mt-0.5 px-2 py-1 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-100"
              >
                {(row.frequency_options || Object.keys(FREQUENCY_LABELS)).map((f) => (
                  <option key={f} value={f}>
                    {FREQUENCY_LABELS[f] || f}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col text-[11px] text-gray-500 dark:text-gray-400">
              Max items / run
              <input
                type="number"
                min={1}
                max={100}
                value={edit.maxItems ?? ''}
                onChange={(e) =>
                  setEdit(row.id, {
                    maxItems: e.target.value === '' ? undefined : Number(e.target.value),
                  })
                }
                className="mt-0.5 w-24 px-2 py-1 text-sm border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-100"
              />
            </label>
            <button
              onClick={() => saveCollector(row)}
              disabled={savingId === row.id}
              className="px-3 py-1.5 text-sm font-medium rounded bg-teal-600 hover:bg-teal-700 disabled:bg-teal-400 text-white transition-colors"
            >
              {savingId === row.id ? 'Saving…' : savedId === row.id ? 'Saved ✓' : 'Save'}
            </button>
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-base font-semibold text-gray-900 dark:text-white mb-1">
            Schedules
          </h3>
          <p className="text-sm text-gray-600 dark:text-gray-400">
            Everything the app runs on a timer — across every agent and the
            background engine. Per-notebook Collector cadence is editable here;
            most background actors can be turned on/off or run once on demand.
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="flex-shrink-0 px-3 py-1.5 text-sm font-medium rounded border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700"
        >
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      {error && (
        <div className="p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg text-sm text-red-700 dark:text-red-400">
          {error}
        </div>
      )}

      {/* Live background-engine snapshot */}
      {live && (
        <div className="p-3 border border-gray-200 dark:border-gray-700 rounded-lg bg-gray-50 dark:bg-gray-800/50 flex flex-wrap gap-x-6 gap-y-1 text-xs">
          <span className="text-gray-500 dark:text-gray-400">
            Presence tier:{' '}
            <span className="font-medium text-gray-800 dark:text-gray-200">
              {live.tier || '—'}
            </span>
          </span>
          <span className="text-gray-500 dark:text-gray-400">
            Queue depth:{' '}
            <span className="font-medium text-gray-800 dark:text-gray-200">
              {live.queue_depth ?? '—'}
            </span>
          </span>
          <span className="text-gray-500 dark:text-gray-400">
            Memory pressure:{' '}
            <span className="font-medium text-gray-800 dark:text-gray-200">
              {live.memory_pressure == null ? '—' : live.memory_pressure ? 'yes' : 'no'}
            </span>
          </span>
          {live.last_run_label && (
            <span className="text-gray-500 dark:text-gray-400">
              Last job:{' '}
              <span className="font-medium text-gray-800 dark:text-gray-200">
                {live.last_run_label} ({timeAgo(live.last_run)})
              </span>
            </span>
          )}
        </div>
      )}

      {loading && rows.length === 0 && (
        <p className="text-sm text-gray-500 dark:text-gray-400">Loading schedules…</p>
      )}

      {/* Category groups (non-advanced) */}
      {CATEGORY_ORDER.map((cat) => {
        const catRows = grouped(cat.id, false);
        if (catRows.length === 0) return null;
        return (
          <div key={cat.id} className="space-y-2">
            <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 flex items-center gap-1.5">
              <span>{cat.icon}</span> {cat.label}
            </h4>
            <div className="space-y-2">{catRows.map(renderRow)}</div>
          </div>
        );
      })}

      {/* Advanced / infrastructure disclosure — read-only */}
      {rows.some((r) => r.advanced) && (
        <div className="pt-2">
          <button
            onClick={() => setShowAdvanced((v) => !v)}
            className="text-xs font-medium text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
          >
            {showAdvanced ? '▾' : '▸'} Advanced / infrastructure schedules (read-only)
          </button>
          {showAdvanced && (
            <div className="mt-2 space-y-3">
              {CATEGORY_ORDER.map((cat) => {
                const catRows = grouped(cat.id, true);
                if (catRows.length === 0) return null;
                return (
                  <div key={cat.id} className="space-y-2">
                    <h4 className="text-xs font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
                      {cat.label}
                    </h4>
                    <div className="space-y-2">{catRows.map(renderRow)}</div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
