/**
 * Curator Service - Curator config, chat, and intelligence API
 */
import { API_BASE_URL } from './api';

export interface CuratorConfig {
  notebook_id: string;
  personality: string;
  focus_areas: string[];
  proactive: boolean;
}

class CuratorService {
  async getConfig(): Promise<CuratorConfig> {
    const response = await fetch(`${API_BASE_URL}/curator/config`);
    if (!response.ok) throw new Error('Failed to fetch curator config');
    return response.json();
  }

  async updateConfig(updates: Partial<CuratorConfig>): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/curator/config`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(updates),
    });
    if (!response.ok) throw new Error('Failed to update curator config');
    return response.json();
  }

  async chat(message: string, notebookId?: string): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/curator/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, notebook_id: notebookId }),
    });
    if (!response.ok) throw new Error('Failed to send curator chat');
    return response.json();
  }

  async getSetupFollowup(notebookId: string): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/curator/setup-followup/${notebookId}`);
    if (!response.ok) throw new Error('Failed to fetch setup followup');
    return response.json();
  }

  async getMorningBrief(hoursAway: number): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/curator/morning-brief?hours_away=${hoursAway}`);
    if (!response.ok) throw new Error('Failed to fetch morning brief');
    return response.json();
  }

  async inferConfig(files: File[]): Promise<any> {
    const formData = new FormData();
    files.forEach(f => formData.append('files', f));
    const response = await fetch(`${API_BASE_URL}/curator/infer-config`, {
      method: 'POST',
      body: formData,
    });
    if (!response.ok) throw new Error('Failed to infer config');
    return response.json();
  }

  async overwatch(notebookId: string, query: string, answer: string): Promise<any> {
    const response = await fetch(`${API_BASE_URL}/curator/overwatch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notebook_id: notebookId, query, answer }),
    });
    if (!response.ok) return null;
    return response.json();
  }
}

export const curatorService = new CuratorService();
