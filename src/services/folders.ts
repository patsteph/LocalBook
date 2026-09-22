/**
 * Linked Folders Service — watch a folder on disk, ingest what lands in it.
 *
 * A folder link with `notebook_id` feeds one notebook. A link without one is a
 * Smart Folder: scanned identically, but the destination is decided per file
 * (Part 2). `is_smart` is the flag the UI branches on.
 *
 * `pickFolder` is the only place the native directory picker is opened. It
 * degrades to a typed path outside Tauri so the feature is still testable in a
 * browser, rather than throwing.
 */
import { API_BASE_URL, localFetch } from './api';

export interface FolderLinkStats {
  ingested: number;
  failed: number;
  skipped: number;
}

export interface FolderLink {
  id: string;
  notebook_id: string | null;
  notebook_title: string | null;
  path: string;
  display_path: string;
  patterns: string[];
  /** Filenames never ingested from this folder, whatever the patterns allow. */
  exclude: string[];
  frequency: string;
  enabled: boolean;
  recursive: boolean;
  is_smart: boolean;
  exists: boolean;
  created_at: string;
  last_scan_at: string | null;
  last_error: string | null;
  files_ingested: number;
  stats: FolderLinkStats;
}

export interface FolderLinkList {
  links: FolderLink[];
  totals: { folders: number; ingested: number; failed: number; smart: number };
  frequencies: string[];
  default_patterns: string[];
}

export interface ScanReport {
  link_id: string;
  path: string;
  scanned: number;
  ingested: number;
  skipped: number;
  failed: number;
  pending: number;
  error: string | null;
  files: Array<{ name: string; action: string; reason?: string; source_id?: string; chunks?: number }>;
}

export interface PathPreview {
  path: string;
  display_path: string;
  readable: boolean;
  matching_files: number;
  sample: string[];
}

export interface LedgerEntry {
  name: string;
  path: string;
  status: string;
  error: string | null;
  source_id: string | null;
  ingested_at: string | null;
}

/** Human labels for the cadence vocabulary shared with the Collector. */
export const FREQUENCY_LABELS: Record<string, string> = {
  hourly: 'Every hour',
  every_2_hours: 'Every 2 hours',
  every_4_hours: 'Every 4 hours',
  every_8_hours: 'Every 8 hours',
  twice_daily: 'Twice a day',
  daily: 'Once a day',
  every_3_days: 'Every 3 days',
  weekly: 'Weekly',
  manual: 'Only when I ask',
};

async function jsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch { /* non-JSON error body */ }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export async function listFolderLinks(notebookId?: string): Promise<FolderLinkList> {
  const qs = notebookId ? `?notebook_id=${encodeURIComponent(notebookId)}` : '';
  return jsonOrThrow(await localFetch(`${API_BASE_URL}/folders/links${qs}`));
}

export async function createFolderLink(body: {
  path: string;
  notebook_id?: string | null;
  patterns?: string[];
  frequency?: string;
  recursive?: boolean;
  enabled?: boolean;
  /** "all" ingests what's already there; "new_only" baselines it and waits. */
  backfill?: 'all' | 'new_only';
}): Promise<FolderLink> {
  return jsonOrThrow(await localFetch(`${API_BASE_URL}/folders/links`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }));
}

export async function updateFolderLink(
  id: string,
  body: Partial<Pick<FolderLink, 'patterns' | 'exclude' | 'frequency' | 'recursive' | 'enabled' | 'notebook_id'>>,
): Promise<FolderLink> {
  return jsonOrThrow(await localFetch(`${API_BASE_URL}/folders/links/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }));
}

export async function deleteFolderLink(id: string): Promise<void> {
  await jsonOrThrow(await localFetch(`${API_BASE_URL}/folders/links/${id}`, { method: 'DELETE' }));
}

export async function scanFolderLink(id: string): Promise<ScanReport> {
  return jsonOrThrow(await localFetch(`${API_BASE_URL}/folders/links/${id}/scan`, { method: 'POST' }));
}

export async function previewFolderLink(id: string): Promise<{
  new_files: number; other: number;
  files: Array<{ name: string; size: number; action: string; reason: string }>;
}> {
  return jsonOrThrow(await localFetch(`${API_BASE_URL}/folders/links/${id}/preview`));
}

/** Look at a folder BEFORE linking it — readable? how many files? which ones? */
export async function previewPath(
  path: string, patterns?: string[], recursive = false,
): Promise<PathPreview> {
  return jsonOrThrow(await localFetch(`${API_BASE_URL}/folders/preview-path`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path, patterns, recursive }),
  }));
}

export async function folderLedger(id: string): Promise<{ entries: LedgerEntry[] }> {
  return jsonOrThrow(await localFetch(`${API_BASE_URL}/folders/ledger/${id}`));
}

/**
 * Open the native directory picker. Returns null if the user cancels, or if
 * we're not running inside Tauri (the caller falls back to a text field).
 */
export async function pickFolder(): Promise<string | null> {
  const inTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window;
  if (!inTauri) return null;
  const { open } = await import('@tauri-apps/plugin-dialog');
  const selected = await open({ directory: true, multiple: false });
  if (!selected) return null;
  return Array.isArray(selected) ? selected[0] : (selected as string);
}

// ── Smart Folders ──────────────────────────────────────────────────────────

export interface PendingItem {
  id: string;
  link_id: string;
  abs_path: string;
  filename: string;
  participants: string[];
  topics: string[];
  summary: string;
  suggested_id: string | null;
  suggested_name: string | null;
  confidence: number;
  alternatives: Array<{ notebook_id: string; notebook_name: string; confidence: number }>;
  status: string;
  created_at: string;
}

export interface PendingList {
  items: PendingItem[];
  count: number;
  total_pending: number;
  notebooks: Array<{ id: string; title: string }>;
  suggested_notebooks: Array<{ participant: string; count: number }>;
}

export interface RoutingRule {
  id: string;
  scope_participants: string[];
  scope_topics: string[];
  notebook_id: string;
  notebook_title?: string;
  hit_count: number;
  last_hit_at: string | null;
  enabled: boolean;
  created_at: string;
}

/** The scope the user chose when granting standing permission. */
export type RuleScope = 'participants' | 'topics' | 'both';

export async function listPending(notebookId?: string): Promise<PendingList> {
  const qs = notebookId ? `?notebook_id=${encodeURIComponent(notebookId)}` : '';
  return jsonOrThrow(await localFetch(`${API_BASE_URL}/folders/pending${qs}`));
}

export async function approvePending(
  id: string, body: { notebook_id?: string | null; rule_scope?: RuleScope | null },
): Promise<{ notebook_id: string; corrected: boolean; rule: RoutingRule | null }> {
  return jsonOrThrow(await localFetch(`${API_BASE_URL}/folders/pending/${id}/approve`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }));
}

export async function dismissPending(id: string): Promise<void> {
  await jsonOrThrow(await localFetch(`${API_BASE_URL}/folders/pending/${id}/dismiss`, {
    method: 'POST',
  }));
}

export async function listRules(): Promise<{ rules: RoutingRule[] }> {
  return jsonOrThrow(await localFetch(`${API_BASE_URL}/folders/rules`));
}

export async function setRuleEnabled(id: string, enabled: boolean): Promise<RoutingRule> {
  return jsonOrThrow(await localFetch(
    `${API_BASE_URL}/folders/rules/${id}?enabled=${enabled}`, { method: 'PATCH' }));
}

export async function deleteRule(id: string): Promise<void> {
  await jsonOrThrow(await localFetch(`${API_BASE_URL}/folders/rules/${id}`, {
    method: 'DELETE',
  }));
}

/** Plain-English description of a rule — what it will actually do. */
export function describeRule(r: RoutingRule): string {
  const who = r.scope_participants.length
    ? `with ${r.scope_participants.map(titleCase).join(' and ')}`
    : '';
  const what = r.scope_topics.length
    ? `about ${r.scope_topics.slice(0, 3).join(', ')}`
    : '';
  const scope = [who, what].filter(Boolean).join(' ');
  return `Recordings ${scope} → ${r.notebook_title || 'notebook'}`;
}

function titleCase(s: string): string {
  return s.replace(/\b\w/g, (c) => c.toUpperCase());
}
