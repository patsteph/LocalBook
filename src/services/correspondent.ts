/**
 * correspondent service — Phase 6 of v2-information-cortex.
 *
 * Thin client for the /correspondent/* endpoints (IMAP account
 * management, sync, status, approval queue).
 */
import { API_BASE_URL, localFetch } from './api';

export interface IMAPAccount {
  email: string;
  imap_host: string;
  imap_port: number;
  imap_user: string;
  use_ssl: boolean;
  enabled: boolean;
  last_uid: number;
  last_polled_at: string | null;
  created_at?: string;
  // Phase 8 — SMTP + forward routing.
  smtp_host?: string | null;
  smtp_port?: number;
  smtp_use_tls?: boolean;
  send_confirmations?: boolean;
  default_forward_notebook_id?: string | null;
  // Phase 13 — weekly auto-journal opt-out.
  weekly_journal_enabled?: boolean;
}

export interface QueueItem {
  item_id: string;
  email_account: string;
  message_uid: number;
  message_id: string;
  sender: string;
  subject: string;
  summary: string;
  topic_tags: string[];
  // Phase 8 — kind='forward' on queued forwards; absent / 'newsletter' otherwise.
  kind?: 'forward' | 'newsletter';
  top_candidate?: { notebook_id: string; notebook_name: string; confidence: number };
  alternatives?: { notebook_id: string; notebook_name: string; confidence: number }[];
  decision_reason: string;
  created_at: string;
}

export interface CorrespondentStatus {
  accounts: Record<string, {
    last_polled_at?: string;
    last_uid?: number;
    last_error?: string | null;
    last_result?: { ingested: number; queued: number; personal: number; transactional: number; errors: number };
  }>;
}

// Phase 7 — sister-newsletter subscription proposals.
// Phase 13 — entity-watch proposals (kind='entity'). Same queue, different shape.
export type SubscriptionProposal = SisterSubscriptionProposal | EntityWatchProposal;

export interface SisterSubscriptionProposal {
  id: string;
  kind: 'subscription';
  status: 'pending';
  title: string;
  url: string;
  feed_url: string;
  source_type: string;
  channel_name?: string;
  default_schedule?: string;
  kind_label: 'newsletter' | 'blog' | 'podcast';
  suggested_notebook_id: string;
  source_email: { message_id?: string; subject?: string; sender?: string };
  created_at: string;
}

export interface EntityWatchProposal {
  id: string;
  kind: 'entity';
  status: 'pending';
  title: string;
  entity_name: string;
  entity_type: 'person' | 'document' | 'paper' | 'podcast' | string;
  suggested_notebook_id: string;
  source_email: { sender?: string; summary?: string };
  created_at: string;
}

export const correspondentService = {
  async listAccounts(): Promise<IMAPAccount[]> {
    const res = await localFetch(`${API_BASE_URL}/correspondent/accounts`);
    if (!res.ok) throw new Error(`Failed to list accounts: ${res.status}`);
    const data = await res.json();
    return data.accounts || [];
  },

  async addAccount(payload: {
    email: string;
    imap_host: string;
    imap_port: number;
    imap_user?: string;
    imap_password: string;
    use_ssl: boolean;
    smtp_host?: string;
    smtp_port?: number;
    smtp_use_tls?: boolean;
    send_confirmations?: boolean;
    default_forward_notebook_id?: string | null;
  }): Promise<IMAPAccount> {
    const res = await localFetch(`${API_BASE_URL}/correspondent/accounts`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    return data.account;
  },

  async deleteAccount(email: string): Promise<void> {
    const res = await localFetch(`${API_BASE_URL}/correspondent/accounts/${encodeURIComponent(email)}`, {
      method: 'DELETE',
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  },

  async updateAccount(email: string, patch: { send_confirmations?: boolean; default_forward_notebook_id?: string | null; weekly_journal_enabled?: boolean }): Promise<void> {
    const res = await localFetch(`${API_BASE_URL}/correspondent/accounts/${encodeURIComponent(email)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  },

  async smtpHint(email: string): Promise<{ found: boolean; host?: string; port?: number; use_tls?: boolean }> {
    const res = await localFetch(`${API_BASE_URL}/correspondent/smtp-hint?email=${encodeURIComponent(email)}`);
    if (!res.ok) return { found: false };
    return res.json();
  },

  async sync(): Promise<{ summary: { totals: Record<string, number>; accounts: Record<string, unknown> } }> {
    const res = await localFetch(`${API_BASE_URL}/correspondent/sync`, { method: 'POST' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  async status(): Promise<CorrespondentStatus> {
    const res = await localFetch(`${API_BASE_URL}/correspondent/status`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },

  async listQueue(): Promise<QueueItem[]> {
    const res = await localFetch(`${API_BASE_URL}/correspondent/queue`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    return data.items || [];
  },

  async approveQueueItem(itemId: string, notebookId?: string): Promise<{ source_id: string; notebook_id: string }> {
    const res = await localFetch(`${API_BASE_URL}/correspondent/queue/${itemId}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notebook_id: notebookId || null }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },

  async dismissQueueItem(itemId: string): Promise<void> {
    const res = await localFetch(`${API_BASE_URL}/correspondent/queue/${itemId}/dismiss`, {
      method: 'POST',
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  },

  // ── Phase 7 — subscription proposals ──
  async listSubscriptionQueue(): Promise<SubscriptionProposal[]> {
    const res = await localFetch(`${API_BASE_URL}/correspondent/subscriptions`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    return data.items || [];
  },

  async approveSubscription(itemId: string, notebookId?: string): Promise<{ feed_url: string; notebook_id: string }> {
    const res = await localFetch(`${API_BASE_URL}/correspondent/subscriptions/${itemId}/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notebook_id: notebookId || null }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },

  async dismissSubscription(itemId: string): Promise<void> {
    const res = await localFetch(`${API_BASE_URL}/correspondent/subscriptions/${itemId}/dismiss`, {
      method: 'POST',
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
  },
};
