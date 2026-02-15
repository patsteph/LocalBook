/**
 * Graph Service - Knowledge graph and constellation API
 */
import { API_BASE_URL } from './api';

export interface GraphStats {
  entities: number;
  relationships: number;
  communities: number;
  notebooks_indexed: number;
}

class GraphService {
  async getStats(notebookId?: string): Promise<GraphStats> {
    const params = notebookId ? `?notebook_id=${notebookId}` : '';
    const response = await fetch(`${API_BASE_URL}/graph/stats${params}`);
    if (!response.ok) throw new Error('Failed to fetch graph stats');
    return response.json();
  }

  async getEntities(notebookId: string, endpoint: string): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/graph/${endpoint}?notebook_id=${notebookId}`);
    if (!response.ok) throw new Error('Failed to fetch graph entities');
    return response.json();
  }

  async buildGraph(notebookId: string): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/graph/build/${notebookId}`, {
      method: 'POST',
    });
    if (!response.ok) throw new Error('Failed to build graph');
    return response.json();
  }

  async clusterGraph(): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/graph/cluster`, {
      method: 'POST',
    });
    if (!response.ok) throw new Error('Failed to cluster graph');
    return response.json();
  }

  async resetGraph(notebookId: string): Promise<void> {
    const response = await fetch(`${API_BASE_URL}/graph/reset/${notebookId}`, {
      method: 'DELETE',
    });
    if (!response.ok) throw new Error('Failed to reset graph');
  }

  async scanContradictions(notebookId: string, forceRescan = false): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/contradictions/scan/${notebookId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ force_rescan: forceRescan }),
    });
    if (!response.ok) throw new Error('Failed to scan contradictions');
    return response.json();
  }
}

export const graphService = new GraphService();
