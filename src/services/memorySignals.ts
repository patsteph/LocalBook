/**
 * Memory Signals Service - Frontend API for user behavior tracking and memory management
 */
import { API_BASE_URL } from './api';

export interface UserSignal {
  id: string;
  notebook_id: string;
  signal_type: 'view' | 'click' | 'ignore' | 'search_miss' | 'manual_add';
  item_id: string | null;
  query: string | null;
  timestamp: string;
  metadata: Record<string, any> | null;
}

export interface ConsolidationStatus {
  last_consolidation: string | null;
  next_due: boolean;
  interval_hours: number;
  scheduler_running: boolean;
}

export interface MemoryStats {
  core_memory: {
    entries: number;
    tokens: number;
    max_tokens: number;
    usage_percent: number;
  };
  recall_memory: {
    entries: number;
  };
  archival_memory: {
    total_entries: number;
    by_namespace: {
      system: number;
      curator: number;
      collector: number;
    };
  };
}

export interface NotebookMemoryStats {
  notebook_id: string;
  collector_memories: number;
  recent_signals: number;
  ignored_items: number;
  system_memories: number;
}

class MemorySignalsService {
  /**
   * Record a user signal for learning
   */
  async recordSignal(
    notebookId: string,
    signalType: 'view' | 'click' | 'ignore' | 'search_miss' | 'manual_add',
    itemId?: string,
    query?: string,
    metadata?: Record<string, any>
  ): Promise<{ success: boolean }> {
    const response = await fetch(`${API_BASE_URL}/memory/signals`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        notebook_id: notebookId,
        signal_type: signalType,
        item_id: itemId,
        query,
        metadata
      })
    });
    if (!response.ok) throw new Error('Failed to record signal');
    return response.json();
  }

  /**
   * Get user signals for a notebook
   */
  async getSignals(
    notebookId: string,
    signalType?: string,
    sinceDays: number = 30,
    limit: number = 100
  ): Promise<{ signals: UserSignal[] }> {
    let url = `${API_BASE_URL}/memory/signals/${notebookId}?since_days=${sinceDays}&limit=${limit}`;
    if (signalType) url += `&signal_type=${signalType}`;
    
    const response = await fetch(url);
    if (!response.ok) throw new Error('Failed to fetch signals');
    return response.json();
  }

  /**
   * Get items that were shown but never clicked (negative signal)
   */
  async getIgnoredItems(
    notebookId: string,
    daysThreshold: number = 7
  ): Promise<{ ignored_items: string[]; count: number }> {
    const response = await fetch(
      `${API_BASE_URL}/memory/signals/${notebookId}/ignored?days_threshold=${daysThreshold}`
    );
    if (!response.ok) throw new Error('Failed to fetch ignored items');
    return response.json();
  }

  /**
   * Get queries where user searched but Collector had no results
   */
  async getSearchMisses(
    notebookId: string,
    sinceDays: number = 30
  ): Promise<{ search_misses: string[]; count: number }> {
    const response = await fetch(
      `${API_BASE_URL}/memory/signals/${notebookId}/search-misses?since_days=${sinceDays}`
    );
    if (!response.ok) throw new Error('Failed to fetch search misses');
    return response.json();
  }

  /**
   * Get memory statistics for a specific notebook
   */
  async getNotebookStats(notebookId: string): Promise<NotebookMemoryStats> {
    const response = await fetch(`${API_BASE_URL}/memory/namespaces/${notebookId}`);
    if (!response.ok) throw new Error('Failed to fetch notebook stats');
    return response.json();
  }

  /**
   * Get overall memory statistics
   */
  async getMemoryStats(): Promise<MemoryStats> {
    const response = await fetch(`${API_BASE_URL}/memory/stats`);
    if (!response.ok) throw new Error('Failed to fetch memory stats');
    return response.json();
  }

  /**
   * Trigger memory consolidation manually
   */
  async triggerConsolidation(): Promise<{
    status: string;
    recall_compressed: number;
    archival_pruned: number;
    core_demoted: number;
    insights_generated: number;
    signals_processed: number;
  }> {
    const response = await fetch(`${API_BASE_URL}/memory/consolidate`, {
      method: 'POST'
    });
    if (!response.ok) throw new Error('Failed to trigger consolidation');
    return response.json();
  }

  /**
   * Get consolidation scheduler status
   */
  async getConsolidationStatus(): Promise<ConsolidationStatus> {
    const response = await fetch(`${API_BASE_URL}/memory/consolidation/status`);
    if (!response.ok) throw new Error('Failed to fetch consolidation status');
    return response.json();
  }

  /**
   * Track that a Collector item was viewed (starts ignore timer)
   */
  trackItemView(notebookId: string, itemId: string): void {
    this.recordSignal(notebookId, 'view', itemId).catch(err => {
      console.warn('Failed to track item view:', err);
    });
  }

  /**
   * Track that a Collector item was clicked (positive signal)
   */
  trackItemClick(notebookId: string, itemId: string): void {
    this.recordSignal(notebookId, 'click', itemId).catch(err => {
      console.warn('Failed to track item click:', err);
    });
  }

  /**
   * Track a search with no results (coverage gap)
   */
  trackSearchMiss(notebookId: string, query: string): void {
    this.recordSignal(notebookId, 'search_miss', undefined, query).catch(err => {
      console.warn('Failed to track search miss:', err);
    });
  }

  /**
   * Track manual content addition (Collector missed this)
   */
  trackManualAdd(notebookId: string, metadata?: Record<string, any>): void {
    this.recordSignal(notebookId, 'manual_add', undefined, undefined, metadata).catch(err => {
      console.warn('Failed to track manual add:', err);
    });
  }
}

export const memorySignalsService = new MemorySignalsService();
