/**
 * infographic service — 2.2.0 infographic lane (L1 annotated chart /
 * L2 structured diagram).
 *
 * Thin client wrapping POST /visual/infographic. Returns the full response so
 * the Studio tombstone can surface both the `json:infographic` Artifact
 * envelope and the routing decision (auto vs lane override).
 */
import { api } from './api';
import type { Artifact } from '../types/artifact';
import type { InfographicLane } from '../components/visual/InfographicLanePicker';

export interface InfographicResponse {
  artifact: Artifact;
  lane: string;
  routing?: { mode?: string; confidence?: number; stage?: string; lane?: string };
}

export const infographicService = {
  async generate(
    notebookId: string,
    topic: string,
    lane: InfographicLane = 'auto',
  ): Promise<InfographicResponse> {
    const { data } = await api.post<InfographicResponse>('/visual/infographic', {
      notebook_id: notebookId,
      topic,
      lane,
      include_sources: true,
    });
    return data;
  },
};
