// Typed client for the health/smoke endpoints (backend/api/health_portal.py).
// Backs the in-app HealthPanel — the React home for what used to be the
// browser-only health portal. The static /health/portal HTML stays as a
// degraded-mode lifeboat (reachable if the React app won't load).
import { API_BASE_URL, localFetch } from '../../services/api';

export type Overall = 'healthy' | 'degraded' | 'critical';
export type CheckStatus = 'pass' | 'warn' | 'fail';

export interface HealthCheck {
  display: string;
  status: CheckStatus;
  error?: string;
}
export interface HealthSection {
  status: CheckStatus;
  checks: HealthCheck[];
}
export interface HealthIssue {
  severity: 'critical' | 'high' | 'medium' | 'low' | string;
  title: string;
  message: string;
  repair?: string;
  repair_params?: Record<string, unknown>;
}
export interface HealthFull {
  overall: Overall;
  system?: {
    memory_available_gb?: number;
    disk_free_gb?: number;
    memory_percent_used?: number;
    disk_percent_used?: number;
    error?: string;
  };
  metrics?: { queries_24h?: number; avg_latency_ms?: number };
  token_stats?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    avg_tokens_per_sec?: number;
    scraped_tokens?: number;
  };
  sections?: Record<string, HealthSection>;
  issues?: HealthIssue[];
}
export interface LogEntry {
  timestamp: string;
  level: 'ERROR' | 'WARN' | 'INFO' | 'DEBUG' | string;
  message: string;
  source?: string;
}

// ── Quality signals ("Rough edges") — recurrence-ranked silent near-misses ──
export interface SignalGroup {
  type: string;        // misroute | fallback | empty | recovered | degraded
  component: string;
  key: string;
  count: number;
  severity: 'info' | 'notable' | 'warn' | string;
  first_seen: string;
  last_seen: string;
  detail: string;
  samples: string[];
}
export interface SignalsResponse {
  days: number;
  total: number;
  groups: SignalGroup[];
}

async function getJSON<T>(path: string): Promise<T> {
  const res = await localFetch(`${API_BASE_URL}${path}`);
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json() as Promise<T>;
}

export const healthApi = {
  full: () => getJSON<HealthFull>('/health/full'),
  signals: (days = 7) => getJSON<SignalsResponse>(`/signals/recent?days=${days}`),
  logs: (limit = 200) => getJSON<{ logs: LogEntry[] }>(`/health/logs?limit=${limit}`),
  clearLogs: async () => {
    await localFetch(`${API_BASE_URL}/health/logs`, { method: 'DELETE' });
  },
  repair: async (action: string, params: Record<string, unknown>) => {
    const res = await localFetch(`${API_BASE_URL}/health/repair`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action, params }),
    });
    return res.json() as Promise<{ message?: string }>;
  },
  exportUrl: `${API_BASE_URL}/health/export`,
  portalUrl: `${API_BASE_URL}/health/portal`,
};

// Byte/count formatting shared with the panel.
export function fmtCount(n?: number): string {
  if (n == null) return '--';
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

// Section key → icon + label (the static page hardcoded these per section).
export const SECTION_META: Record<string, { icon: string; label: string }> = {
  core_services:    { icon: '🔌', label: 'Core Services' },
  ai_models:        { icon: '🧠', label: 'AI & Models' },
  data_integrity:   { icon: '📊', label: 'Data Integrity' },
  configuration:    { icon: '⚙️', label: 'Configuration' },
  functional_tests: { icon: '🧪', label: 'Functional Tests' },
};
export function sectionMeta(key: string): { icon: string; label: string } {
  return SECTION_META[key] || { icon: '📋', label: key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase()) };
}
