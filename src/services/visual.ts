/**
 * Visual Summary Service - API calls for Mermaid diagrams and visual summaries
 */

import { API_BASE_URL } from './api';

const API_BASE = API_BASE_URL;

export interface Diagram {
  diagram_type: string;
  code: string;
  title: string;
  description: string;
}

export interface VisualSummary {
  notebook_id: string;
  diagrams: Diagram[];
  key_points: string[];
}

export interface DocumentComparison {
  notebook_id: string;
  document_1: { id: string; name: string };
  document_2: { id: string; name: string };
  comparison: {
    similarities: string[];
    differences: string[];
    unique_to_first: string[];
    unique_to_second: string[];
    synthesis: string;
  };
}

export const visualService = {
  async generateSummary(
    notebookId: string,
    diagramTypes: string[] = ['mindmap', 'flowchart'],
    focusTopic?: string
  ): Promise<VisualSummary> {
    const response = await fetch(`${API_BASE}/visual/summary`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        notebook_id: notebookId,
        diagram_types: diagramTypes,
        focus_topic: focusTopic || undefined,
      }),
    });
    if (!response.ok) throw new Error('Failed to generate visual summary');
    return response.json();
  },

  async generateMindmap(notebookId: string): Promise<any> {
    const response = await fetch(`${API_BASE}/visual/mindmap`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notebook_id: notebookId }),
    });
    if (!response.ok) throw new Error('Failed to generate mindmap');
    return response.json();
  },

  async generateFlowchart(notebookId: string, focus?: string): Promise<any> {
    const response = await fetch(`${API_BASE}/visual/flowchart`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notebook_id: notebookId, focus }),
    });
    if (!response.ok) throw new Error('Failed to generate flowchart');
    return response.json();
  },

  async compareDocuments(
    notebookId: string,
    sourceId1: string,
    sourceId2: string
  ): Promise<DocumentComparison> {
    const response = await fetch(
      `${API_BASE}/visual/compare?notebook_id=${notebookId}&source_id_1=${sourceId1}&source_id_2=${sourceId2}`,
      { method: 'POST' }
    );
    if (!response.ok) throw new Error('Failed to compare documents');
    return response.json();
  },

  async generateSmart(
    notebookId: string,
    topic: string
  ): Promise<VisualSummary> {
    const response = await fetch(`${API_BASE}/visual/smart`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        notebook_id: notebookId,
        topic: topic,
      }),
    });
    if (!response.ok) throw new Error('Failed to generate smart visual');
    return response.json();
  },
};
