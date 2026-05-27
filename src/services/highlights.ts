import { Highlight, HighlightCreate } from '../types';
import api from './api';

// All calls go through the project's `api` axios instance (not bare axios)
// so the request interceptor attaches X-LocalBook-Token. Bare axios would
// 401 against the hardened backend (P0.1f enforce mode) because no token
// header is set — that's the bug that surfaced as "missing or invalid app
// token" errors in the source viewer after uploads (2026-05-27).
export const highlightService = {
  async create(highlight: HighlightCreate): Promise<Highlight> {
    const response = await api.post(`/source-viewer/highlights`, highlight);
    return response.data;
  },

  async list(notebookId: string, sourceId: string): Promise<Highlight[]> {
    const response = await api.get(`/source-viewer/highlights/${notebookId}/${sourceId}`);
    return response.data;
  },

  async updateAnnotation(highlightId: string, annotation: string): Promise<Highlight> {
    const response = await api.patch(`/source-viewer/highlights/${highlightId}`, {
      annotation,
    });
    return response.data;
  },

  async delete(highlightId: string): Promise<void> {
    await api.delete(`/source-viewer/highlights/${highlightId}`);
  },
};
