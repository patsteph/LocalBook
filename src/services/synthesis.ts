/**
 * synthesis service — Phase 12 of v2-information-cortex.
 *
 * Thin client for /synthesis/* endpoints. v1 ships:
 *   findPerspectives(query, notebookId?, crossNotebook?)
 *
 * Returns the server-composed HTML (rendered through the strict
 * HtmlArtifactRenderer) and the structured perspectives payload for any
 * future client-side use.
 */
import { API_BASE_URL, localFetch } from './api';

export interface SourcePerspective {
  source_id: string;
  filename: string;
  notebook_id: string;
  take: string;
  claims: string[];
  snippet: string;
}

export interface ClaimMember {
  source_id: string;
  claim: string;
}

export interface ClaimCluster {
  label: 'consensus' | 'contested' | 'solo';
  representative: string;
  members: ClaimMember[];
}

export interface TopicPerspectives {
  query: string;
  scope: 'notebook' | 'cross-notebook' | 'empty';
  sources: SourcePerspective[];
  claim_clusters: ClaimCluster[];
}

export const synthesisService = {
  async findDeepDive(
    entity: string,
    notebookId: string | null,
    crossNotebook: boolean = true,
    maxSources: number = 8,
  ): Promise<{ html: string; perspectives: TopicPerspectives & { related_entities?: string[] } }> {
    const res = await localFetch(`${API_BASE_URL}/synthesis/deep-dive`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        entity,
        notebook_id: notebookId,
        cross_notebook: crossNotebook,
        max_sources: maxSources,
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },

  async getNotebookDashboard(notebookId: string): Promise<{ html: string; generated_at: string }> {
    const res = await localFetch(`${API_BASE_URL}/curator/notebook-dashboard/${notebookId}`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },

  async findPerspectives(
    query: string,
    notebookId: string | null,
    crossNotebook: boolean = false,
    maxSources: number = 8,
  ): Promise<{ html: string; perspectives: TopicPerspectives }> {
    const res = await localFetch(`${API_BASE_URL}/synthesis/perspectives`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query,
        notebook_id: notebookId,
        cross_notebook: crossNotebook,
        max_sources: maxSources,
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    return res.json();
  },
};
