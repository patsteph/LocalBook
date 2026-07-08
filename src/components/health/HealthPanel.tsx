import { useCallback, useEffect, useState } from 'react';
import { openUrl } from '@tauri-apps/plugin-opener';
import {
  healthApi, HealthFull, LogEntry, HealthIssue, CheckStatus, Overall,
  fmtCount, sectionMeta,
} from './healthApi';

// In-app System Health panel (rendered in the App's <Modal>). React port of the
// static health_portal.html — same /health/* endpoints, Tailwind light/dark, and
// a "Open in browser" escape to the standalone page (the degraded-mode lifeboat).

const OVERALL_META: Record<Overall, { dot: string; ring: string; text: (n: number) => string }> = {
  healthy:  { dot: 'bg-emerald-500', ring: 'border-emerald-300 dark:border-emerald-700 bg-emerald-50 dark:bg-emerald-900/20', text: () => 'All Systems Operational' },
  degraded: { dot: 'bg-amber-500',   ring: 'border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/20',       text: (n) => `${n} Issue${n !== 1 ? 's' : ''} Detected` },
  critical: { dot: 'bg-red-500',     ring: 'border-red-300 dark:border-red-700 bg-red-50 dark:bg-red-900/20',               text: () => 'Critical Issues Detected' },
};
const STATUS_DOT: Record<CheckStatus, string> = { pass: 'bg-emerald-500', warn: 'bg-amber-500', fail: 'bg-red-500' };
const SEVERITY_BORDER: Record<string, string> = {
  critical: 'border-l-red-500', high: 'border-l-orange-500', medium: 'border-l-amber-500', low: 'border-l-blue-500',
};
const LOG_COLOR: Record<string, string> = {
  ERROR: 'text-red-400', WARN: 'text-amber-400', INFO: 'text-blue-400', DEBUG: 'text-purple-400',
};

function Stat({ label, value, bar }: { label: string; value: string; bar?: { pct: number; tone: 'good' | 'warn' | 'danger' } }) {
  const toneCls = { good: 'bg-emerald-500', warn: 'bg-amber-500', danger: 'bg-red-500' };
  return (
    <div className="text-center">
      <div className="text-2xl font-semibold text-gray-900 dark:text-gray-100">{value}</div>
      <div className="text-[11px] uppercase text-gray-500 dark:text-gray-400">{label}</div>
      {bar && (
        <div className="mt-1 h-2 rounded-full bg-gray-200 dark:bg-gray-700 overflow-hidden">
          <div className={`h-full ${toneCls[bar.tone]}`} style={{ width: `${Math.max(0, Math.min(100, bar.pct))}%` }} />
        </div>
      )}
    </div>
  );
}

export function HealthPanel() {
  const [data, setData] = useState<HealthFull | null>(null);
  const [checking, setChecking] = useState(false);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [logFilter, setLogFilter] = useState<'all' | 'ERROR' | 'WARN' | 'INFO'>('all');
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [repairMsg, setRepairMsg] = useState<string | null>(null);
  const [lastChecked, setLastChecked] = useState<string | null>(null);

  const refreshLogs = useCallback(async () => {
    try { setLogs((await healthApi.logs(200)).logs || []); } catch { /* ignore */ }
  }, []);

  const runCheck = useCallback(async () => {
    setChecking(true);
    try {
      const d = await healthApi.full();
      setData(d);
      setLastChecked(new Date().toLocaleTimeString());
      // Auto-expand any section that isn't all-pass.
      const open = new Set<string>();
      Object.entries(d.sections || {}).forEach(([k, s]) => { if (s.status !== 'pass') open.add(k); });
      setExpanded(open);
    } catch {
      setData({ overall: 'critical', sections: {}, issues: [{ severity: 'critical', title: 'Backend unreachable', message: 'Could not reach the backend health endpoint.' }] });
    }
    setChecking(false);
    refreshLogs();
  }, [refreshLogs]);

  // Auto-run on open (parity with the static page).
  useEffect(() => { runCheck(); refreshLogs(); }, [runCheck, refreshLogs]);

  const toggle = (k: string) => setExpanded((prev) => {
    const next = new Set(prev);
    next.has(k) ? next.delete(k) : next.add(k);
    return next;
  });

  const doRepair = async (issue: HealthIssue) => {
    if (!issue.repair) return;
    setRepairMsg('Running repair…');
    try {
      const r = await healthApi.repair(issue.repair, issue.repair_params || {});
      setRepairMsg(r.message || 'Repair completed — re-checking…');
      setTimeout(() => { setRepairMsg(null); runCheck(); }, 2500);
    } catch (e) {
      setRepairMsg(`Repair failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const filteredLogs = logFilter === 'all' ? logs : logs.filter((l) => l.level === logFilter);
  const sys = data?.system;
  const tok = data?.token_stats;
  const sections = data?.sections ? Object.entries(data.sections) : [];
  const issues = data?.issues || [];
  const overallMeta = data ? OVERALL_META[data.overall] : null;

  return (
    <div className="p-4 space-y-4">
      {/* Status banner */}
      <div className={`rounded-xl border-2 px-5 py-4 flex items-center justify-between gap-3 flex-wrap ${overallMeta?.ring || 'border-gray-200 dark:border-gray-700'}`}>
        <div className="flex items-center gap-3">
          <span className={`w-4 h-4 rounded-full ${checking ? 'bg-gray-400 animate-pulse' : (overallMeta?.dot || 'bg-gray-400')}`} />
          <span className="text-base font-medium text-gray-900 dark:text-gray-100">
            {checking ? 'Running health checks…' : data ? overallMeta!.text(issues.length) : 'Loading…'}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={runCheck} disabled={checking}
            className="px-3 py-1.5 text-sm rounded-lg bg-blue-600 hover:bg-blue-700 text-white disabled:bg-gray-300 dark:disabled:bg-gray-700 disabled:text-gray-500 disabled:cursor-not-allowed">
            {checking ? 'Running…' : '🔍 Run Health Check'}
          </button>
          <button onClick={() => openUrl(healthApi.exportUrl)}
            className="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800">
            📋 Export
          </button>
          <button onClick={() => openUrl(healthApi.portalUrl)} title="Open the standalone page in your browser (works even if the app is unresponsive)"
            className="px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800">
            ↗ Browser
          </button>
        </div>
      </div>

      {repairMsg && (
        <div className="rounded-lg border border-blue-300 dark:border-blue-700 bg-blue-50 dark:bg-blue-900/20 px-4 py-2 text-sm text-blue-700 dark:text-blue-300">
          {repairMsg}
        </div>
      )}

      {/* System resources */}
      <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-4">
        <h3 className="text-sm font-semibold text-gray-900 dark:text-white mb-3">System Resources</h3>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <Stat label="Memory Available" value={sys?.memory_available_gb != null ? `${sys.memory_available_gb.toFixed(1)} GB` : '--'}
            bar={{ pct: 100 - (sys?.memory_percent_used || 0), tone: (sys?.memory_percent_used || 0) > 90 ? 'danger' : (sys?.memory_percent_used || 0) > 80 ? 'warn' : 'good' }} />
          <Stat label="Disk Free" value={sys?.disk_free_gb != null ? `${sys.disk_free_gb.toFixed(0)} GB` : '--'}
            bar={{ pct: 100 - (sys?.disk_percent_used || 0), tone: (sys?.disk_percent_used || 0) > 90 ? 'danger' : (sys?.disk_percent_used || 0) > 80 ? 'warn' : 'good' }} />
          <Stat label="Queries (24h)" value={data?.metrics?.queries_24h != null ? String(data.metrics.queries_24h) : '--'} />
          <Stat label="Avg Latency" value={data?.metrics?.avg_latency_ms ? `${(data.metrics.avg_latency_ms / 1000).toFixed(1)}s` : '--'} />
        </div>
      </div>

      {/* Token economy */}
      <div className="rounded-lg border border-gray-200 dark:border-gray-700 border-l-4 border-l-blue-500 bg-white dark:bg-gray-800 p-4">
        <h3 className="text-sm font-semibold text-gray-900 dark:text-white mb-3">🪙 Token Economy <span className="text-xs font-normal text-gray-500 dark:text-gray-400">(all-time, processed locally)</span></h3>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          <Stat label="Tokens In" value={fmtCount(tok?.prompt_tokens)} />
          <Stat label="Tokens Out" value={fmtCount(tok?.completion_tokens)} />
          <Stat label="Avg Tokens/sec" value={tok?.avg_tokens_per_sec != null ? tok.avg_tokens_per_sec.toFixed(1) : '--'} />
          <Stat label="Scraped Tokens" value={fmtCount(tok?.scraped_tokens)} />
        </div>
      </div>

      {/* Sections */}
      {sections.length > 0 && (
        <div className="space-y-2">
          {sections.map(([key, section]) => {
            const meta = sectionMeta(key);
            const passing = section.checks.filter((c) => c.status === 'pass').length;
            const isOpen = expanded.has(key);
            return (
              <div key={key} className="rounded-lg border border-gray-200 dark:border-gray-700 overflow-hidden">
                <button onClick={() => toggle(key)}
                  className="w-full flex items-center gap-3 px-4 py-3 bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700/50 text-left">
                  <span className={`text-xs text-gray-400 transition-transform ${isOpen ? 'rotate-90' : ''}`}>▶</span>
                  <span>{meta.icon}</span>
                  <span className="flex-1 font-medium text-gray-900 dark:text-gray-100">{meta.label}</span>
                  <span className="text-xs text-gray-500 dark:text-gray-400">{passing}/{section.checks.length} passing</span>
                  <span className={`w-3 h-3 rounded-full ${STATUS_DOT[section.status] || 'bg-gray-400'}`} />
                </button>
                {isOpen && (
                  <div className="px-4 py-3 bg-gray-50 dark:bg-gray-900/30 grid grid-cols-1 sm:grid-cols-2 gap-2">
                    {section.checks.map((c, i) => (
                      <div key={i} className="flex items-center justify-between rounded-lg bg-white dark:bg-gray-800 px-3 py-2" title={c.error || 'OK'}>
                        <span className="text-sm text-gray-700 dark:text-gray-300">{c.display}</span>
                        <span className={`w-2.5 h-2.5 rounded-full ${STATUS_DOT[c.status] || 'bg-gray-400'}`} />
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Issues */}
      {issues.length > 0 && (
        <div>
          <h3 className="text-sm font-semibold text-gray-900 dark:text-white mb-2">⚠️ Issues Detected</h3>
          <div className="space-y-2">
            {issues.map((issue, i) => (
              <div key={i} className={`rounded-lg border-l-4 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 px-4 py-3 flex items-start justify-between gap-3 ${SEVERITY_BORDER[issue.severity] || 'border-l-gray-400'}`}>
                <div className="min-w-0">
                  <h4 className="text-sm font-medium text-gray-900 dark:text-gray-100">{issue.title}</h4>
                  <p className="text-xs text-gray-500 dark:text-gray-400">{issue.message}</p>
                </div>
                {issue.repair && (
                  <button onClick={() => doRepair(issue)}
                    className="flex-shrink-0 px-3 py-1.5 text-xs rounded-lg bg-blue-600 hover:bg-blue-700 text-white">
                    🔧 Repair
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Console */}
      <div>
        <div className="flex items-center justify-between mb-2 flex-wrap gap-2">
          <h3 className="text-sm font-semibold text-gray-900 dark:text-white">📟 Console</h3>
          <div className="flex items-center gap-1">
            {(['all', 'ERROR', 'WARN', 'INFO'] as const).map((lvl) => (
              <button key={lvl} onClick={() => setLogFilter(lvl)}
                className={`px-2.5 py-1 text-xs rounded border ${
                  logFilter === lvl
                    ? 'bg-blue-600 border-blue-600 text-white'
                    : 'border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'
                }`}>{lvl === 'all' ? 'All' : lvl}</button>
            ))}
            <button onClick={refreshLogs} className="px-2 py-1 text-xs text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200">🔄</button>
            <button onClick={async () => { await healthApi.clearLogs(); setLogs([]); }} className="px-2 py-1 text-xs text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200">🗑️</button>
          </div>
        </div>
        <div className="h-52 overflow-y-auto rounded-lg bg-gray-900 dark:bg-black/40 border border-gray-200 dark:border-gray-700 p-3 font-mono text-xs">
          {filteredLogs.length === 0 ? (
            <div className="text-gray-500 text-center py-10">No logs to display</div>
          ) : (
            [...filteredLogs].reverse().map((log, i) => (
              <div key={i} className="flex gap-3 py-0.5 border-b border-gray-800">
                <span className="text-gray-500 flex-shrink-0">{new Date(log.timestamp).toLocaleTimeString()}</span>
                <span className={`flex-shrink-0 w-12 font-semibold ${LOG_COLOR[log.level] || 'text-gray-300'}`}>{log.level}</span>
                <span className="text-gray-200 break-words min-w-0">{log.message}</span>
                {log.source && <span className="text-gray-500 ml-auto flex-shrink-0">{log.source}</span>}
              </div>
            ))
          )}
        </div>
      </div>

      <div className="text-center text-xs text-gray-400 dark:text-gray-500">
        {lastChecked ? `Last checked: ${lastChecked}` : 'Never checked'}
      </div>
    </div>
  );
}
