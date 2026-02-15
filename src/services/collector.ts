/**
 * Collector Service - Frontend API for per-notebook content collection
 */
import { API_BASE_URL } from './api';

export interface CollectorConfig {
  name: string;
  intent: string;
  focus_areas: string[];
  excluded_topics: string[];
  collection_mode: 'manual' | 'automatic' | 'hybrid';
  approval_mode: 'trust_me' | 'show_me' | 'mixed';
  sources: {
    rss_feeds: string[];
    web_pages: string[];
    news_keywords: string[];
  };
  schedule: {
    frequency: string;
    max_items_per_run: number;
  };
  filters: {
    max_age_days: number;
    min_relevance: number;
    language: string;
  };
  created_at: string;
  updated_at: string;
}

export interface PendingItem {
  item_id: string;
  title: string;
  preview: string;
  source: string;
  confidence: number;
  confidence_reasons: string[];
  queued_at: string;
  expires_at: string;
  days_until_expiry: number;
}

export interface SourceHealth {
  source_id: string;
  url: string;
  health: 'healthy' | 'degraded' | 'failing' | 'dead';
  failure_count: number;
  items_collected: number;
  avg_response_ms: number;
}

export interface CollectionResult {
  notebook_id: string;
  started_at: string;
  completed_at: string;
  items_found: number;
  items_queued: number;
  items_auto_approved: number;
  duplicates_skipped: number;
  errors: string[];
}

export interface SchedulerStatus {
  running: boolean;
  notebooks_tracked: number;
  last_runs: Record<string, string>;
}

class CollectorService {
  /**
   * Get Collector configuration for a notebook
   */
  async getConfig(notebookId: string): Promise<CollectorConfig> {
    const response = await fetch(`${API_BASE_URL}/collector/${notebookId}/config`);
    if (!response.ok) throw new Error('Failed to fetch collector config');
    return response.json();
  }

  /**
   * Update Collector configuration
   */
  async updateConfig(
    notebookId: string, 
    updates: Partial<CollectorConfig>
  ): Promise<{ success: boolean; config: CollectorConfig }> {
    const response = await fetch(`${API_BASE_URL}/collector/${notebookId}/config`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updates)
    });
    if (!response.ok) throw new Error('Failed to update collector config');
    return response.json();
  }

  /**
   * Run immediate first sweep for instant gratification
   */
  async runFirstSweep(notebookId: string): Promise<{
    items_found: number;
    items_queued: number;
    sources_checked: number;
    duration_ms: number;
  }> {
    const response = await fetch(`${API_BASE_URL}/collector/${notebookId}/first-sweep`, {
      method: 'POST'
    });
    if (!response.ok) throw new Error('Failed to run first sweep');
    return response.json();
  }

  /**
   * Trigger immediate collection run
   */
  async collectNow(notebookId: string): Promise<CollectionResult> {
    const response = await fetch(`${API_BASE_URL}/collector/${notebookId}/collect-now`, {
      method: 'POST'
    });
    if (!response.ok) throw new Error('Failed to trigger collection');
    return response.json();
  }

  /**
   * Get items pending approval
   */
  async getPendingApprovals(notebookId: string): Promise<{
    pending: PendingItem[];
    total: number;
    expiring_soon: number;
  }> {
    const response = await fetch(`${API_BASE_URL}/collector/${notebookId}/pending`);
    if (!response.ok) throw new Error('Failed to fetch pending approvals');
    return response.json();
  }

  /**
   * Approve a pending item
   */
  async approveItem(notebookId: string, itemId: string): Promise<{ success: boolean }> {
    const response = await fetch(`${API_BASE_URL}/collector/${notebookId}/approve/${itemId}`, {
      method: 'POST'
    });
    if (!response.ok) throw new Error('Failed to approve item');
    return response.json();
  }

  /**
   * Approve multiple items at once
   */
  async approveBatch(notebookId: string, itemIds: string[]): Promise<{
    approved: number;
    total: number;
  }> {
    const response = await fetch(`${API_BASE_URL}/collector/${notebookId}/approve-batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ item_ids: itemIds })
    });
    if (!response.ok) throw new Error('Failed to approve batch');
    return response.json();
  }

  /**
   * Approve all items from a specific source
   */
  async approveAllFromSource(notebookId: string, sourceName: string): Promise<{
    approved: number;
    source: string;
  }> {
    const response = await fetch(
      `${API_BASE_URL}/collector/${notebookId}/approve-source/${encodeURIComponent(sourceName)}`,
      { method: 'POST' }
    );
    if (!response.ok) throw new Error('Failed to approve from source');
    return response.json();
  }

  /**
   * Reject a pending item with feedback
   */
  async rejectItem(
    notebookId: string,
    itemId: string,
    reason: string,
    feedbackType?: 'wrong_topic' | 'too_old' | 'bad_source' | 'already_knew' | 'other'
  ): Promise<{ success: boolean }> {
    const response = await fetch(`${API_BASE_URL}/collector/${notebookId}/reject/${itemId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason, feedback_type: feedbackType })
    });
    if (!response.ok) throw new Error('Failed to reject item');
    return response.json();
  }

  /**
   * Get health report for all configured sources
   */
  async getSourceHealth(notebookId: string): Promise<{ sources: SourceHealth[] }> {
    const response = await fetch(`${API_BASE_URL}/collector/${notebookId}/source-health`);
    if (!response.ok) throw new Error('Failed to fetch source health');
    return response.json();
  }

  /**
   * Get collection scheduler status
   */
  async getSchedulerStatus(): Promise<SchedulerStatus> {
    const response = await fetch(`${API_BASE_URL}/collector/scheduler/status`);
    if (!response.ok) throw new Error('Failed to fetch scheduler status');
    return response.json();
  }

  /**
   * Start the collection scheduler
   */
  async startScheduler(): Promise<{ success: boolean; status: string }> {
    const response = await fetch(`${API_BASE_URL}/collector/scheduler/start`, {
      method: 'POST'
    });
    if (!response.ok) throw new Error('Failed to start scheduler');
    return response.json();
  }

  /**
   * Stop the collection scheduler
   */
  async stopScheduler(): Promise<{ success: boolean; status: string }> {
    const response = await fetch(`${API_BASE_URL}/collector/scheduler/stop`, {
      method: 'POST'
    });
    if (!response.ok) throw new Error('Failed to stop scheduler');
    return response.json();
  }

  /**
   * Get collector profile (collection history summary)
   */
  async getProfile(notebookId: string): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/collector/${notebookId}/profile`);
    if (!response.ok) throw new Error('Failed to fetch collector profile');
    return response.json();
  }

  /**
   * Get collection history
   */
  async getHistory(notebookId: string, limit = 15): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/collector/${notebookId}/history?limit=${limit}`);
    if (!response.ok) throw new Error('Failed to fetch collection history');
    return response.json();
  }

  /**
   * Toggle a source on/off
   */
  async toggleSource(notebookId: string, sourceUrl: string, enabled: boolean): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/collector/${notebookId}/source-toggle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_url: sourceUrl, enabled })
    });
    if (!response.ok) throw new Error('Failed to toggle source');
    return response.json();
  }
}

export const collectorService = new CollectorService();
