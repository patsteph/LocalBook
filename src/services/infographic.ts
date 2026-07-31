/**
 * infographic service — 2.2.0 infographic lane (L1 annotated chart /
 * L2 structured diagram).
 *
 * Thin client wrapping POST /visual/infographic. Returns the full response so
 * the Studio tombstone can surface both the `json:infographic` Artifact
 * envelope and the routing decision (auto vs lane override).
 */
import { api, API_BASE_URL, localFetch } from './api';
import type { Artifact } from '../types/artifact';
import type { InfographicLane } from '../components/visual/InfographicLanePicker';

export interface InfographicResponse {
  artifact: Artifact;
  lane: string;
  routing?: { mode?: string; confidence?: number; stage?: string; lane?: string };
  infographic_id?: string | null;
}

// A persisted infographic row (GET /visual/infographic/list) — the payload
// dict rides along in `payload_json` so the Library open handler can rehydrate
// the `json:infographic` Artifact without a second fetch.
export interface InfographicRecord {
  infographic_id: string;
  notebook_id: string;
  topic?: string;
  title?: string;
  lane?: string;
  archetype?: string;
  payload_json?: string;
  degraded?: number;
  created_at?: string;
}

export const infographicService = {
  async generate(
    notebookId: string,
    topic: string,
    lane: InfographicLane = 'auto',
    archetype?: string,
    // Build B (Quality Signals): when regenerating with an explicit lane to
    // correct a prior AUTO route, pass the auto lane being overridden (+ its
    // confidence) so the backend records the router misroute.
    correctedFrom?: string,
    correctedFromConfidence?: number,
  ): Promise<InfographicResponse> {
    const { data } = await api.post<InfographicResponse>('/visual/infographic', {
      notebook_id: notebookId,
      topic,
      lane,
      include_sources: true,
      ...(archetype ? { archetype } : {}),
      ...(correctedFrom ? { corrected_from: correctedFrom } : {}),
      ...(typeof correctedFromConfidence === 'number'
        ? { corrected_from_confidence: correctedFromConfidence }
        : {}),
    });
    return data;
  },

  // Library: list persisted infographics for a notebook, newest first (2.2.0).
  async list(notebookId: string): Promise<InfographicRecord[]> {
    const response = await localFetch(`${API_BASE_URL}/visual/infographic/list/${notebookId}`);
    if (!response.ok) throw new Error('Failed to list infographics');
    return response.json();
  },

  // Library: fetch one persisted infographic as a `json:infographic` Artifact
  // envelope (ready for <ArtifactRender> + /export/artifact).
  async get(infographicId: string): Promise<Artifact> {
    const response = await localFetch(`${API_BASE_URL}/visual/infographic/item/${infographicId}`);
    if (!response.ok) throw new Error('Failed to fetch infographic');
    return response.json();
  },

  // Library: delete a persisted infographic.
  async deleteItem(infographicId: string): Promise<void> {
    const response = await localFetch(`${API_BASE_URL}/visual/infographic/item/${infographicId}`, { method: 'DELETE' });
    if (!response.ok) throw new Error('Failed to delete infographic');
  },
};
