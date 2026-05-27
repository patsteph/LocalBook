import api from './api';

export interface TimelineEvent {
  event_id: string;
  notebook_id: string;
  source_id: string;
  date_timestamp: number;
  date_string: string;
  date_type: string;
  event_text: string;
  context: string;
  page_number?: number;
  char_offset?: number;
  confidence: number;
  filename?: string;
}

export interface ExtractionProgress {
  status: string;
  current: number;
  total: number;
  message: string;
}

// All calls go through the project's `api` axios instance (not bare axios)
// so the request interceptor attaches X-LocalBook-Token. Bare axios would
// 401 against the hardened backend (P0.1f enforce mode). Same class of bug
// as src/services/highlights.ts (fixed 2026-05-27).
class TimelineService {
  async getTimeline(notebookId: string, sourceId?: string): Promise<TimelineEvent[]> {
    const params = sourceId ? { source_id: sourceId } : {};
    const response = await api.get(`/timeline/${notebookId}`, { params });
    return response.data;
  }

  async extractTimeline(notebookId: string): Promise<void> {
    await api.post(`/timeline/extract/${notebookId}`);
  }

  async getExtractionProgress(notebookId: string): Promise<ExtractionProgress> {
    const response = await api.get(`/timeline/progress/${notebookId}`);
    return response.data;
  }

  async deleteTimeline(notebookId: string): Promise<void> {
    await api.delete(`/timeline/${notebookId}`);
  }
}

export const timelineService = new TimelineService();
