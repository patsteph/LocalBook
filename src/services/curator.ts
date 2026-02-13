/**
 * Curator Service - Frontend API for cross-notebook synthesis and oversight
 */
import { API_BASE_URL } from './api';

export interface CuratorConfig {
  name: string;
  personality: string;
  oversight: {
    auto_approve_threshold: number;
    require_approval_for: string[];
  };
  synthesis: {
    proactive_insights: boolean;
    insight_frequency: string;
  };
  voice: {
    style: string;
    verbosity: string;
  };
}

export interface NotebookSummary {
  notebook_id: string;
  name: string;
  items_added: number;
  flagged_important: number;
  pending_approval: number;
  top_finding: string | null;
}

export interface MorningBrief {
  away_duration: string;
  notebooks: NotebookSummary[];
  cross_notebook_insight: string | null;
  generated_at: string;
}

export interface SynthesisResult {
  synthesis: string;
  sources: { notebook_id: string; score: number }[];
  notebooks_searched: string[];
}

export interface JudgmentResult {
  item_id: string;
  decision: 'approve' | 'reject' | 'modify' | 'defer_to_user';
  reason: string;
  confidence: number;
  modifications: string[] | null;
}

export interface ProactiveInsight {
  type: string;
  entity: string | null;
  notebooks: string[];
  summary: string;
  confidence: number;
}

export interface CounterargumentResult {
  inferred_thesis: string;
  counterpoints: { query: string; content: string; score: number }[];
  confidence: number;
}

class CuratorService {
  /**
   * Get current Curator configuration
   */
  async getConfig(): Promise<CuratorConfig> {
    const response = await fetch(`${API_BASE_URL}/curator/config`);
    if (!response.ok) throw new Error('Failed to fetch curator config');
    return response.json();
  }

  /**
   * Update Curator configuration (name, personality, etc.)
   */
  async updateConfig(updates: Partial<CuratorConfig>): Promise<{ success: boolean; config: CuratorConfig }> {
    const response = await fetch(`${API_BASE_URL}/curator/config`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updates)
    });
    if (!response.ok) throw new Error('Failed to update curator config');
    return response.json();
  }

  /**
   * Get morning brief based on time away
   */
  async getMorningBrief(hoursAway: number = 8): Promise<MorningBrief> {
    const response = await fetch(`${API_BASE_URL}/curator/morning-brief?hours_away=${hoursAway}`);
    if (!response.ok) throw new Error('Failed to fetch morning brief');
    return response.json();
  }

  /**
   * Synthesize information across multiple notebooks
   */
  async synthesize(query: string, notebookIds?: string[]): Promise<SynthesisResult> {
    const response = await fetch(`${API_BASE_URL}/curator/synthesize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, notebook_ids: notebookIds })
    });
    if (!response.ok) throw new Error('Failed to synthesize');
    return response.json();
  }

  /**
   * Have Curator judge items proposed by a Collector
   */
  async judgeItems(
    collectorId: string,
    notebookIntent: string,
    items: any[]
  ): Promise<{ judgments: JudgmentResult[] }> {
    const response = await fetch(`${API_BASE_URL}/curator/judge`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        collector_id: collectorId,
        notebook_intent: notebookIntent,
        items
      })
    });
    if (!response.ok) throw new Error('Failed to judge items');
    return response.json();
  }

  /**
   * Run cross-notebook pattern discovery
   */
  async discoverPatterns(): Promise<{ insights: ProactiveInsight[] }> {
    const response = await fetch(`${API_BASE_URL}/curator/discover-patterns`, {
      method: 'POST'
    });
    if (!response.ok) throw new Error('Failed to discover patterns');
    return response.json();
  }

  /**
   * Find counterarguments (Devil's Advocate mode)
   */
  async findCounterarguments(notebookId: string, thesis?: string): Promise<CounterargumentResult> {
    const response = await fetch(`${API_BASE_URL}/curator/devils-advocate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notebook_id: notebookId, thesis })
    });
    if (!response.ok) throw new Error('Failed to find counterarguments');
    return response.json();
  }

  /**
   * Check if there's a relevant proactive insight for a query
   */
  async getInsightForQuery(query: string): Promise<{ insight: string | null }> {
    const response = await fetch(
      `${API_BASE_URL}/curator/insight-for-query?query=${encodeURIComponent(query)}`
    );
    if (!response.ok) throw new Error('Failed to get insight');
    return response.json();
  }
}

export const curatorService = new CuratorService();
