/**
 * Findings Service - API calls for bookmarks, saved visuals, and highlights
 */

import { API_BASE_URL } from './api';

const API_BASE = API_BASE_URL;

export interface Finding {
  id: string;
  notebook_id: string;
  type: 'visual' | 'answer' | 'highlight' | 'source' | 'note';
  title: string;
  created_at: string;
  updated_at: string;
  content: Record<string, unknown>;
  tags: string[];
  starred: boolean;
}

export interface FindingsStats {
  total: number;
  by_type: Record<string, number>;
  starred: number;
}

export const findingsService = {
  async createFinding(
    notebookId: string,
    type: Finding['type'],
    title: string,
    content: Record<string, unknown>,
    tags?: string[],
    starred?: boolean
  ): Promise<Finding> {
    const response = await fetch(`${API_BASE}/findings`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        notebook_id: notebookId,
        type,
        title,
        content,
        tags: tags || [],
        starred: starred || false,
      }),
    });
    if (!response.ok) throw new Error('Failed to create finding');
    return response.json();
  },

  async getFindings(
    notebookId: string,
    options?: {
      type?: Finding['type'];
      starred?: boolean;
      tag?: string;
      limit?: number;
      offset?: number;
    }
  ): Promise<{ findings: Finding[]; count: number }> {
    const params = new URLSearchParams();
    if (options?.type) params.append('type', options.type);
    if (options?.starred) params.append('starred', 'true');
    if (options?.tag) params.append('tag', options.tag);
    if (options?.limit) params.append('limit', options.limit.toString());
    if (options?.offset) params.append('offset', options.offset.toString());

    const response = await fetch(
      `${API_BASE}/findings/${notebookId}?${params.toString()}`
    );
    if (!response.ok) throw new Error('Failed to fetch findings');
    return response.json();
  },

  async getFinding(notebookId: string, findingId: string): Promise<Finding> {
    const response = await fetch(
      `${API_BASE}/findings/${notebookId}/${findingId}`
    );
    if (!response.ok) throw new Error('Finding not found');
    return response.json();
  },

  async updateFinding(
    notebookId: string,
    findingId: string,
    updates: {
      title?: string;
      tags?: string[];
      starred?: boolean;
      content?: Record<string, unknown>;
    }
  ): Promise<Finding> {
    const response = await fetch(
      `${API_BASE}/findings/${notebookId}/${findingId}`,
      {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates),
      }
    );
    if (!response.ok) throw new Error('Failed to update finding');
    return response.json();
  },

  async deleteFinding(notebookId: string, findingId: string): Promise<void> {
    const response = await fetch(
      `${API_BASE}/findings/${notebookId}/${findingId}`,
      { method: 'DELETE' }
    );
    if (!response.ok) throw new Error('Failed to delete finding');
  },

  async getStats(notebookId: string): Promise<FindingsStats> {
    const response = await fetch(
      `${API_BASE}/findings/${notebookId}/stats/summary`
    );
    if (!response.ok) throw new Error('Failed to fetch findings stats');
    return response.json();
  },

  // Helper: Save a visual as a finding
  async saveVisual(
    notebookId: string,
    title: string,
    visualData: {
      type: 'svg' | 'mermaid';
      code: string;
      template_id?: string;
      source_content?: string;
    },
    tags?: string[]
  ): Promise<Finding> {
    return this.createFinding(notebookId, 'visual', title, visualData, tags);
  },

  // Helper: Save a chat answer as a finding
  async saveAnswer(
    notebookId: string,
    title: string,
    answerData: {
      question: string;
      answer: string;
      citations?: unknown[];
    },
    tags?: string[]
  ): Promise<Finding> {
    return this.createFinding(notebookId, 'answer', title, answerData, tags);
  },

  // Helper: Save a highlight/quote as a finding
  async saveHighlight(
    notebookId: string,
    title: string,
    highlightData: {
      text: string;
      source_id: string;
      source_name: string;
      page?: number;
    },
    tags?: string[]
  ): Promise<Finding> {
    return this.createFinding(notebookId, 'highlight', title, highlightData, tags);
  },
};
