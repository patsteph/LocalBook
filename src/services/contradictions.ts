/**
 * Contradiction Detection Service
 * 
 * Frontend service for detecting and managing contradictions in notebook sources.
 */

import { API_BASE_URL } from './api';

const API_BASE = API_BASE_URL;

export interface Claim {
  id: string;
  text: string;
  source_id: string;
  source_name: string;
  chunk_text: string;
  claim_type: string;
}

export interface Contradiction {
  id: string;
  claim_a: Claim;
  claim_b: Claim;
  contradiction_type: string;
  severity: 'low' | 'medium' | 'high';
  explanation: string;
  resolution_hint?: string;
  detected_at: string;
  dismissed: boolean;
  resolved: boolean;
}

export interface ContradictionReport {
  notebook_id: string;
  generated_at: string;
  contradictions: Contradiction[];
  claims_analyzed: number;
  sources_analyzed: number;
}

export interface ContradictionCount {
  count: number;
  total: number;
  has_scanned: boolean;
  scanned_at?: string;
}

export const contradictionService = {
  /**
   * Scan a notebook for contradictions
   */
  async scan(notebookId: string, forceRescan = false): Promise<ContradictionReport> {
    const response = await fetch(`${API_BASE}/contradictions/scan/${notebookId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ force_rescan: forceRescan }),
    });
    
    if (!response.ok) throw new Error('Failed to scan for contradictions');
    return response.json();
  },

  /**
   * Get cached contradiction report
   */
  async getReport(notebookId: string): Promise<ContradictionReport> {
    const response = await fetch(`${API_BASE}/contradictions/${notebookId}`);
    if (!response.ok) throw new Error('Failed to get contradictions');
    return response.json();
  },

  /**
   * Get quick count of contradictions
   */
  async getCount(notebookId: string): Promise<ContradictionCount> {
    const response = await fetch(`${API_BASE}/contradictions/${notebookId}/count`);
    if (!response.ok) throw new Error('Failed to get contradiction count');
    return response.json();
  },

  /**
   * Start background scan
   */
  async scanBackground(notebookId: string): Promise<void> {
    await fetch(`${API_BASE}/contradictions/${notebookId}/scan-background`, {
      method: 'POST',
    });
  },

  /**
   * Get scan status
   */
  async getScanStatus(notebookId: string): Promise<{ status: string; progress: number }> {
    const response = await fetch(`${API_BASE}/contradictions/${notebookId}/scan-status`);
    if (!response.ok) throw new Error('Failed to get scan status');
    return response.json();
  },

  /**
   * Dismiss a contradiction
   */
  async dismiss(notebookId: string, contradictionId: string, reason?: string): Promise<void> {
    await fetch(`${API_BASE}/contradictions/${notebookId}/dismiss/${contradictionId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ reason }),
    });
  },

  /**
   * Clear cache to force fresh scan
   */
  async clearCache(notebookId: string): Promise<void> {
    await fetch(`${API_BASE}/contradictions/${notebookId}/cache`, {
      method: 'DELETE',
    });
  },
};
