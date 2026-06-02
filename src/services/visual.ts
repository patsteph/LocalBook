/**
 * Visual Summary Service - API calls for Mermaid diagrams and visual summaries
 */

import { API_BASE_URL, localFetch } from './api';

const API_BASE = API_BASE_URL;

export interface Diagram {
  diagram_type?: string;
  code?: string;  // Mermaid code (legacy)
  svg?: string;   // SVG code (new)
  render_type?: 'svg' | 'mermaid' | 'chart';
  chart_config?: any;  // JSON config for Recharts-based data charts
  title: string;
  subtitle?: string;  // Second line shown in the hero overlay (Klein full-bleed)
  description: string;
  template_id?: string;
  template_name?: string;
  tagline?: string;  // Editable summary line shown below visual
  // v2 extras (populated only when the visual was generated through the
  // visual_composer pipeline). Frontend uses these for the critic-score
  // badge + provenance display on the canvas item.
  v2_path?: string;
  v2_setup?: string;
  v2_critic_score?: {
    overall: number;
    legibility: number;
    hierarchy: number;
    balance: number;
    color_harmony: number;
    message_clarity: number;
    strengths?: string[];
    weaknesses?: string[];
    suggestions?: string[];
  } | null;
  v2_generation_ms?: number;
  // Smart overlay placement: backend image-analysis pick of the
  // least-detailed zone, used as the overlay's default position when the
  // user hasn't chosen one yet. One of the 5 overlay positions or absent.
  suggested_overlay_position?: 'top-left' | 'top-right' | 'bottom-left' | 'bottom-right' | 'center';
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
    const response = await localFetch(`${API_BASE}/visual/summary`, {
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
    const response = await localFetch(`${API_BASE}/visual/mindmap`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ notebook_id: notebookId }),
    });
    if (!response.ok) throw new Error('Failed to generate mindmap');
    return response.json();
  },

  async generateFlowchart(notebookId: string, focus?: string): Promise<any> {
    const response = await localFetch(`${API_BASE}/visual/flowchart`, {
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
    const response = await localFetch(
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
    const response = await localFetch(`${API_BASE}/visual/smart`, {
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
    const response = await localFetch(`${API_BASE}/visual/smart/stream`, {
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
    // SSE state persists ACROSS reader.read() chunks. The previous version
    // declared eventType inside the while loop, which meant large payloads
    // (Klein full-bleed visuals embed a ~1MB base64 PNG → response is
    // chunked) lost the eventType between the `event:` line in chunk N
    // and the `data:` line in chunk N+1 — and onPrimary never fired,
    // leaving the canvas tombstone stuck at "Generating visual" forever.
    let eventType = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          eventType = line.slice(7).trim();
        } else if (line.startsWith('data: ')) {
          let data: any;
          try {
            data = JSON.parse(line.slice(6));
          } catch (e) {
            // Malformed JSON shouldn't kill the whole stream — skip and continue
            console.error('[visual SSE] failed to parse data line:', e);
            continue;
          }
          if (eventType === 'primary') {
            onPrimary(data as Diagram);
          } else if (eventType === 'alternative') {
            onAlternative(data as Diagram);
          } else if (eventType === 'done') {
            onDone();
          } else if (eventType === 'error') {
            onError(data.error);
          }
        } else if (line === '') {
          // Blank line terminates an SSE event. Reset eventType so a
          // subsequent `data:` line without its own `event:` defaults to
          // the SSE-standard "message" type rather than inheriting.
          eventType = '';
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
    const response = await localFetch(`${API_BASE}/visual/refine`, {
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

  // Library: list persisted visuals for a notebook, newest first (Tier 5).
  async list(notebookId: string): Promise<any[]> {
    const response = await localFetch(`${API_BASE}/visual/list/${notebookId}`);
    if (!response.ok) throw new Error('Failed to list visuals');
    return response.json();
  },

  // Library: fetch one persisted visual (used to re-hydrate canvas item).
  async getItem(visualId: string): Promise<any> {
    const response = await localFetch(`${API_BASE}/visual/item/${visualId}`);
    if (!response.ok) throw new Error('Failed to fetch visual');
    return response.json();
  },

  // Library: delete a persisted visual.
  async deleteItem(visualId: string): Promise<void> {
    const response = await localFetch(`${API_BASE}/visual/item/${visualId}`, { method: 'DELETE' });
    if (!response.ok) throw new Error('Failed to delete visual');
  },

  // Library: download a visual. SVG visuals export as .svg; Mermaid falls
  // back to markdown since the backend can't render without a renderer.
  async download(visualId: string, format: 'svg' | 'png' = 'svg'): Promise<void> {
    const response = await localFetch(`${API_BASE}/visual/item/${visualId}/download?format=${format}`);
    if (!response.ok) throw new Error('Failed to download visual');
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const ext = format === 'png' ? 'png' : (blob.type.includes('markdown') ? 'md' : 'svg');
    a.download = `visual-${visualId}.${ext}`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  },

  async checkCacheStatus(notebookId: string): Promise<{ ready: boolean; theme_count?: number; age_seconds?: number; reason?: string }> {
    const response = await localFetch(`${API_BASE}/visual/cache/status/${notebookId}`);
    if (!response.ok) return { ready: false, reason: 'fetch_error' };
    return response.json();
  },

  async waitForCache(notebookId: string, maxWaitMs: number = 8000, pollIntervalMs: number = 500): Promise<boolean> {
    const startTime = Date.now();
    while (Date.now() - startTime < maxWaitMs) {
      const status = await this.checkCacheStatus(notebookId);
      if (status.ready) {
        return true;
      }
      await new Promise(resolve => setTimeout(resolve, pollIntervalMs));
    }
    return false;
  },

  // ────────────────────────────────────────────────────────────────
  // Visual System v2 — composer-routed endpoints
  // ────────────────────────────────────────────────────────────────

  /** Report current machine's visual-generation capability. */
  async v2GetCapability(): Promise<V2Capability> {
    const response = await localFetch(`${API_BASE}/visual/v2/capability`);
    if (!response.ok) throw new Error('Failed to fetch v2 capability');
    return response.json();
  },

  /** Non-streaming compose via the v2 composer. */
  async v2Compose(
    notebookId: string,
    topic: string,
    templateId?: string,
  ): Promise<V2ComposedVisual> {
    const response = await localFetch(`${API_BASE}/visual/v2/compose`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        notebook_id: notebookId,
        topic,
        template_id: templateId,
      }),
    });
    if (!response.ok) throw new Error('Failed to compose v2 visual');
    return response.json();
  },

  /** Streaming compose. Calls back as SSE events arrive. */
  async v2ComposeStream(
    notebookId: string,
    topic: string,
    onTier: (info: V2TierEvent) => void,
    onCritic: (score: V2CriticScore) => void,
    onResult: (visual: V2ComposedVisual) => void,
    onDone: () => void,
    onError: (message: string) => void,
    templateId?: string,
  ): Promise<void> {
    const response = await localFetch(`${API_BASE}/visual/v2/compose/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        notebook_id: notebookId,
        topic,
        template_id: templateId,
      }),
    });
    if (!response.ok) {
      onError('Failed to start v2 visual stream');
      return;
    }
    const reader = response.body?.getReader();
    if (!reader) {
      onError('No response body for v2 stream');
      return;
    }
    const decoder = new TextDecoder();
    let buffer = '';
    // SSE state persists ACROSS reader.read() chunks — see the matching
    // comment in generateSmartStream above for the chunking bug this fixes.
    let eventType = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          eventType = line.slice(7).trim();
        } else if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6));
            if (eventType === 'tier') onTier(data as V2TierEvent);
            else if (eventType === 'critic') onCritic(data.score as V2CriticScore);
            else if (eventType === 'result') onResult(data.visual as V2ComposedVisual);
            else if (eventType === 'done') onDone();
            else if (eventType === 'error') onError(data.message);
          } catch {
            // Ignore malformed lines
          }
        } else if (line === '') {
          // Blank line terminates an SSE event — reset to the SSE-default
          // "message" type for any subsequent data: line.
          eventType = '';
        }
      }
    }
  },
};

// ──────────────────────────────────────────────────────────────────
// v2 types
// ──────────────────────────────────────────────────────────────────
export interface V2Capability {
  setup: 'setup_a' | 'setup_b' | 'unknown';
  concurrency_mode: 'concurrent' | 'swap' | 'swap_strict';
  total_ram_gb: number;
  warn_user: boolean;
  models: {
    gemma: string | null;
    klein: string | null;
    olmo: string | null;
    vision: string | null;
  };
  can_freeform_gemma: boolean;
  can_freeform_olmo: boolean;
  can_critic_gemma_vision: boolean;
  can_critic_separate_vision: boolean;
  can_diffusion_klein: boolean;
  summary: string;
}

export interface V2TierEvent {
  setup: string;
  path: string;
  concurrency: string;
}

export interface V2CriticScore {
  legibility: number;
  hierarchy: number;
  balance: number;
  color_harmony: number;
  message_clarity: number;
  overall: number;
  strengths: string[];
  weaknesses: string[];
  suggestions: string[];
}

export interface V2ComposedVisual {
  success: boolean;
  path: string;
  setup: string;
  output_format: 'svg' | 'mermaid';
  svg_markup: string | null;
  mermaid_code: string | null;
  title: string;
  description: string;
  key_points: string[];
  alternatives: { id: string; name: string; reason: string }[];
  critic_score: V2CriticScore | null;
  retry_count: number;
  generation_ms: number;
  error: string | null;
  model_used: string | null;
  template_id: string | null;
  template_name: string | null;
}
