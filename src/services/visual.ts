/**
 * Visual Summary Service - API calls for Mermaid diagrams and visual summaries
 */

import { API_BASE_URL } from './api';

const API_BASE = API_BASE_URL;

export interface Diagram {
  diagram_type?: string;
  code?: string;  // Mermaid code (legacy)
  svg?: string;   // SVG code (new)
  render_type?: 'svg' | 'mermaid';
  title: string;
  description: string;
  template_id?: string;
  template_name?: string;
  tagline?: string;  // Editable summary line shown below visual
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
    focusTopic?: string,
    colorTheme?: string
  ): Promise<VisualSummary> {
    const response = await fetch(`${API_BASE}/visual/summary`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        notebook_id: notebookId,
        diagram_types: diagramTypes,
        focus_topic: focusTopic || undefined,
        color_theme: colorTheme || 'auto',
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
    topic: string,
    colorTheme?: string
  ): Promise<VisualSummary> {
    const response = await fetch(`${API_BASE}/visual/smart`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        notebook_id: notebookId,
        topic: topic,
        color_theme: colorTheme || 'auto',
      }),
    });
    if (!response.ok) throw new Error('Failed to generate smart visual');
    return response.json();
  },

  // Streaming visual generation - primary first, alternatives follow
  async generateSmartStream(
    notebookId: string,
    topic: string,
    colorTheme: string,
    onPrimary: (diagram: Diagram) => void,
    onAlternative: (diagram: Diagram) => void,
    onDone: () => void,
    onError: (error: string) => void,
    templateId?: string,  // Optional: force specific visual type
    guidance?: string     // Optional: user refinement guidance
  ): Promise<void> {
    const response = await fetch(`${API_BASE}/visual/smart/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        notebook_id: notebookId,
        topic: topic,
        color_theme: colorTheme || 'auto',
        template_id: templateId,
        guidance: guidance,
      }),
    });

    if (!response.ok) {
      onError('Failed to start visual stream');
      return;
    }

    const reader = response.body?.getReader();
    if (!reader) {
      onError('No response body');
      return;
    }

    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      let eventType = '';
      for (const line of lines) {
        if (line.startsWith('event: ')) {
          eventType = line.slice(7).trim();
        } else if (line.startsWith('data: ')) {
          const data = JSON.parse(line.slice(6));
          if (eventType === 'primary') {
            onPrimary(data as Diagram);
          } else if (eventType === 'alternative') {
            onAlternative(data as Diagram);
          } else if (eventType === 'done') {
            onDone();
          } else if (eventType === 'error') {
            onError(data.error);
          }
        }
      }
    }
  },

  // Phase 4: Visual refinement chat
  async refineVisual(
    notebookId: string,
    currentCode: string,
    refinement: string,
    colorTheme?: string
  ): Promise<{ success: boolean; code: string; changes_made: string }> {
    const response = await fetch(`${API_BASE}/visual/refine`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        notebook_id: notebookId,
        current_code: currentCode,
        refinement: refinement,
        color_theme: colorTheme || 'auto',
      }),
    });
    if (!response.ok) throw new Error('Failed to refine visual');
    return response.json();
  },

  async checkCacheStatus(notebookId: string): Promise<{ ready: boolean; theme_count?: number; age_seconds?: number; reason?: string }> {
    const response = await fetch(`${API_BASE}/visual/cache/status/${notebookId}`);
    if (!response.ok) return { ready: false, reason: 'fetch_error' };
    return response.json();
  },

  async waitForCache(notebookId: string, maxWaitMs: number = 8000, pollIntervalMs: number = 500): Promise<boolean> {
    const startTime = Date.now();
    while (Date.now() - startTime < maxWaitMs) {
      const status = await this.checkCacheStatus(notebookId);
      if (status.ready) {
        console.log(`[Visual] Cache ready in ${Date.now() - startTime}ms, ${status.theme_count} themes`);
        return true;
      }
      await new Promise(resolve => setTimeout(resolve, pollIntervalMs));
    }
    console.log(`[Visual] Cache not ready after ${maxWaitMs}ms, proceeding anyway`);
    return false;
  },
};
