/**
 * Companions — local tools LocalBook installs, connects to, and learns from.
 *
 * Everything tool-specific lives in a backend manifest, so this client is
 * generic: it renders whatever companions the backend reports. A second tool
 * is a new JSON file on the backend, not a change here.
 */
import { API_BASE_URL, localFetch } from './api';

export type CompanionState = 'not_installed' | 'installed' | 'connected' | 'recording';

export interface CompanionInstall {
  kind: string;
  command: string;
  interactive?: boolean;
  requires?: string[];
  notes?: string[];
  /** Pinned provenance — a commit, never a branch. */
  repo?: string;
  ref?: string;
  short_ref?: string;
  ref_date?: string;
  sha256?: string;
  review_url?: string;
}

export interface VerifyCheck {
  ok: boolean;
  kind: string;
  label: string;
  detail: string;
  fix: string | null;
}

export interface VerifyResult {
  checked: boolean;
  ok: boolean;
  checks: VerifyCheck[];
  failed_count?: number;
  summary?: string;
}

export interface CompanionExtra {
  id: string;
  name: string;
  tagline?: string;
  description?: string;
  notes: string[];
  review_url?: string;
  installed: boolean;
  host_installed: boolean;
  host_cask?: string;
  host_needs_admin: boolean;
  target: string;
}

export interface Companion {
  id: string;
  name: string;
  author?: string;
  tagline?: string;
  description?: string;
  homepage?: string;
  icon: string;
  state: CompanionState;
  installed: boolean;
  connected: boolean;
  running: boolean;
  output_dir: string | null;
  output_exists: boolean;
  linked_notebook_id: string | null;
  linked_notebook_title?: string | null;
  folder_link_id: string | null;
  using_model: string | null;
  install?: CompanionInstall;
  can_control: boolean;
  has_checks?: boolean;
  extras: CompanionExtra[];
}

export interface CompanionList {
  companions: Companion[];
  notebooks: Array<{ id: string; title: string }>;
}

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

export async function listCompanions(): Promise<CompanionList> {
  return jsonOrThrow(await localFetch(`${API_BASE_URL}/companions`));
}

export async function connectCompanion(
  id: string,
  body: { notebook_id?: string | null; backfill?: 'all' | 'new_only'; frequency?: string },
): Promise<{ link_error: string | null; status: Companion }> {
  return jsonOrThrow(await localFetch(`${API_BASE_URL}/companions/${id}/connect`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }));
}

export async function disconnectCompanion(
  id: string, removeLink = false,
): Promise<{ status: Companion }> {
  return jsonOrThrow(await localFetch(
    `${API_BASE_URL}/companions/${id}/disconnect?remove_link=${removeLink}`,
    { method: 'POST' }));
}

export async function controlCompanion(
  id: string, action: 'start' | 'stop',
): Promise<{ status: Companion }> {
  return jsonOrThrow(await localFetch(
    `${API_BASE_URL}/companions/${id}/control/${action}`, { method: 'POST' }));
}

export async function revokeCompanionKey(): Promise<void> {
  await jsonOrThrow(await localFetch(`${API_BASE_URL}/companions/key/revoke`, {
    method: 'POST',
  }));
}

/** The single word the card shows, and the dot colour that goes with it. */
export const STATE_LABEL: Record<CompanionState, string> = {
  not_installed: 'Not installed',
  installed: 'Installed',
  connected: 'Connected',
  recording: 'Recording',
};

export const STATE_DOT: Record<CompanionState, string> = {
  not_installed: 'bg-gray-300 dark:bg-gray-600',
  installed: 'bg-amber-400',
  connected: 'bg-green-500',
  recording: 'bg-red-500 animate-pulse',
};

/** Trim the vendor prefix so a card shows "gemma-4-e4b", not the full repo id. */
export function shortModel(id: string | null): string {
  if (!id) return '';
  const tail = id.split('/').pop() || id;
  return tail.replace(/-(4bit|8bit|bf16|it)$/i, '');
}

export async function verifyCompanion(id: string): Promise<VerifyResult> {
  return jsonOrThrow(await localFetch(`${API_BASE_URL}/companions/${id}/verify`));
}

export async function checkInstallScript(id: string): Promise<{
  source: CompanionInstall;
  verification: { ok: boolean; error?: string; mismatch?: boolean; bytes?: number };
}> {
  return jsonOrThrow(await localFetch(`${API_BASE_URL}/companions/${id}/install-script`));
}

export async function installExtra(id: string, extraId: string): Promise<{ status: Companion }> {
  return jsonOrThrow(await localFetch(`${API_BASE_URL}/companions/${id}/extras/${extraId}`, {
    method: 'POST',
  }));
}

export async function removeExtra(id: string, extraId: string): Promise<{ status: Companion }> {
  return jsonOrThrow(await localFetch(`${API_BASE_URL}/companions/${id}/extras/${extraId}`, {
    method: 'DELETE',
  }));
}
