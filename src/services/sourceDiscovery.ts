/**
 * Source Discovery Service - Discover and approve new sources for collector
 */
import { API_BASE_URL } from './api';

class SourceDiscoveryService {
  async discover(notebookId: string, config: any): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/source-discovery/discover`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notebook_id: notebookId, ...config }),
    });
    if (!response.ok) throw new Error('Failed to discover sources');
    return response.json();
  }

  async approve(notebookId: string, sources: any[]): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/source-discovery/approve`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notebook_id: notebookId, sources }),
    });
    if (!response.ok) throw new Error('Failed to approve sources');
    return response.json();
  }
}

export const sourceDiscoveryService = new SourceDiscoveryService();
