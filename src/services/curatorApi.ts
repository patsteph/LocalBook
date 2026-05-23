/**
 * Curator Service - Curator config, chat, and intelligence API
 */
import { API_BASE_URL, localFetch } from './api';

export interface CuratorConfig {
  name?: string;
  personality?: string;
  focus_areas?: string[];
  proactive?: boolean;
  // Phase 6a: user-selectable narrative voice for written curator output.
  narrative_voice?: 'smart_colleague' | 'executive_brief' | 'conversational_analyst';
  oversight?: {
    overwatch_enabled?: boolean;
    excluded_notebook_ids?: string[];
  };
}

export interface BrainStatus {
  stats: Record<string, any>;
  digests: Array<{
    notebook_id: string;
    name: string;
    dirty: boolean;
    source_count: number;
    last_updated: string | null;
    has_summary: boolean;
    key_themes: string;
  }>;
  connections: Array<{
    id: number;
    notebooks: [string, string];
    description: string;
    strength: number;
    tier: 'strong' | 'medium' | 'weak';
    status: string;
    discovered_at: string;
  }>;
}

export interface AnticipatoryDraftStatus {
  has_draft: boolean;
  draft: null | {
    id: number;
    kind: string;
    preview: string;
    source_signal: string | null;
    created_at: string;
  };
}

// Phase 7.2 readiness diagnostic — brief engagement by voice.
export interface VoiceScoreboard {
  voices: Record<string, { opens: number; thumbs_up: number; thumbs_down: number }>;
  lookback_days: number;
  total_events: number;
}

// Phase 7.5 readiness diagnostic — Studio output engagement by kind.
export interface StudioScoreboard {
  kinds: Record<string, {
    skills: Record<string, { invoked: number; thumbs_up: number; thumbs_down: number }>;
    thumbs_up: number;
    thumbs_down: number;
    invoked: number;
  }>;
  lookback_days: number;
}

// Phase 7.6 readiness diagnostic — per-source rolling acceptance rates.
export interface SourceReputationRow {
  notebook_id: string;
  source_id: string;
  source_label: string;
  total_events: number;
  approved_count: number;
  rejected_count: number;
  added_count: number;
  rolling_30d_events: number;
  rolling_30d_approved: number;
  rolling_30d_rejected: number;
  lifetime_acceptance_rate: number;
  rolling_acceptance_rate: number;
  first_seen_at: string | null;
  last_event_at: string | null;
}

class CuratorService {
  async getConfig(): Promise<CuratorConfig> {
    const response = await localFetch(`${API_BASE_URL}/curator/config`);
    if (!response.ok) throw new Error('Failed to fetch curator config');
    return response.json();
  }

  async updateConfig(updates: Partial<CuratorConfig>): Promise<any> {
    const response = await localFetch(`${API_BASE_URL}/curator/config`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updates),
    });
    if (!response.ok) throw new Error('Failed to update curator config');
    return response.json();
  }

  async chat(message: string, notebookId?: string): Promise<any> {
    const response = await localFetch(`${API_BASE_URL}/curator/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, notebook_id: notebookId }),
    });
    if (!response.ok) throw new Error('Failed to send curator chat');
    return response.json();
  }

  async getSetupFollowup(notebookId: string): Promise<any> {
    const response = await localFetch(`${API_BASE_URL}/curator/setup-followup/${notebookId}`);
    if (!response.ok) throw new Error('Failed to fetch setup followup');
    return response.json();
  }

  async getMorningBrief(hoursAway: number): Promise<any> {
    const response = await localFetch(`${API_BASE_URL}/curator/morning-brief?hours_away=${hoursAway}`);
    if (!response.ok) throw new Error('Failed to fetch morning brief');
    return response.json();
  }

  async inferConfig(files: File[]): Promise<any> {
    const formData = new FormData();
    files.forEach(f => formData.append('files', f));
    const response = await localFetch(`${API_BASE_URL}/curator/infer-config`, {
      method: 'POST',
      body: formData,
    });
    if (!response.ok) throw new Error('Failed to infer config');
    return response.json();
  }

  async overwatch(notebookId: string, query: string, answer: string): Promise<any> {
    const response = await localFetch(`${API_BASE_URL}/curator/overwatch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notebook_id: notebookId, query, answer }),
    });
    if (!response.ok) return null;
    return response.json();
  }

  // Fix #4 (2026-05-23): brain status mini-panel data.
  async getBrainStatus(): Promise<BrainStatus | null> {
    const response = await localFetch(`${API_BASE_URL}/curator/brain-status`);
    if (!response.ok) return null;
    return response.json();
  }

  // Fix #3 (2026-05-23): check if there's a queued anticipatory draft.
  async getAnticipatoryDraft(notebookId: string): Promise<AnticipatoryDraftStatus> {
    const response = await localFetch(`${API_BASE_URL}/curator/notebooks/${encodeURIComponent(notebookId)}/anticipatory-draft`);
    if (!response.ok) return { has_draft: false, draft: null };
    return response.json();
  }

  // Fix #5 (2026-05-23): record thumbs feedback on an overwatch aside.
  async recordAsideThumbs(nagId: number, response: 'up' | 'down' | 'dismissed'): Promise<boolean> {
    const r = await localFetch(`${API_BASE_URL}/curator/asides/${nagId}/thumbs`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ response }),
    });
    return r.ok;
  }

  // Phase 7 readiness diagnostics (2026-05-23).
  async getVoiceScoreboard(lookbackDays = 30): Promise<VoiceScoreboard> {
    const r = await localFetch(`${API_BASE_URL}/curator/voice-scoreboard?lookback_days=${lookbackDays}`);
    if (!r.ok) return { voices: {}, lookback_days: lookbackDays, total_events: 0 };
    return r.json();
  }
  async getStudioScoreboard(lookbackDays = 30): Promise<StudioScoreboard> {
    const r = await localFetch(`${API_BASE_URL}/curator/studio-scoreboard?lookback_days=${lookbackDays}`);
    if (!r.ok) return { kinds: {}, lookback_days: lookbackDays };
    return r.json();
  }
  async getSourceReputation(notebookId: string, limit = 50): Promise<{ sources: SourceReputationRow[] }> {
    const r = await localFetch(`${API_BASE_URL}/curator/notebooks/${encodeURIComponent(notebookId)}/source-reputation?limit=${limit}`);
    if (!r.ok) return { sources: [] };
    return r.json();
  }
  async getNotebooks(): Promise<{ id: string; title: string; source_count: number }[]> {
    const response = await localFetch(`${API_BASE_URL}/notebooks`);
    if (!response.ok) throw new Error('Failed to fetch notebooks');
    const data = await response.json();
    // Backend returns {notebooks: [...], primary_notebook_id: ...}
    const notebooks = data.notebooks || data;
    return Array.isArray(notebooks) ? notebooks : [];
  }
}

export const curatorService = new CuratorService();
