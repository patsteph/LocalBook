/**
 * comparison service — Phase 4 of v2-information-cortex.
 *
 * Thin client wrapping POST /comparison/generate. Returns an Artifact
 * envelope with type='json:comparison' and a payload shaped like:
 *   {
 *     source_a: { id, title },
 *     source_b: { id, title },
 *     similarities: string[],
 *     differences: string[],
 *     unique_to_a: string[],
 *     unique_to_b: string[],
 *     synthesis: string,
 *   }
 */
import { api } from './api';

export interface ComparisonSourceRef {
  id: string;
  title: string;
}

export interface ComparisonPayload {
  source_a: ComparisonSourceRef;
  source_b: ComparisonSourceRef;
  similarities: string[];
  differences: string[];
  unique_to_a: string[];
  unique_to_b: string[];
  synthesis: string;
}

export interface ComparisonArtifactEnvelope {
  id: string;
  type: 'json:comparison';
  payload: ComparisonPayload;
  title?: string;
  tagline?: string;
  metadata?: Record<string, unknown>;
}

export const comparisonService = {
  async generate(
    notebookId: string,
    sourceAId: string,
    sourceBId: string,
    focus?: string,
  ): Promise<ComparisonArtifactEnvelope> {
    const response = await api.post('/comparison/generate', {
      notebook_id: notebookId,
      source_a_id: sourceAId,
      source_b_id: sourceBId,
      focus,
    });
    return response.data.artifact;
  },
};
