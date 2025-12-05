import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

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

class TimelineService {
  async getTimeline(notebookId: string, sourceId?: string): Promise<TimelineEvent[]> {
    const params = sourceId ? { source_id: sourceId } : {};
    const response = await axios.get(`${API_BASE_URL}/timeline/${notebookId}`, { params });
    return response.data;
  }

  async extractTimeline(notebookId: string): Promise<void> {
    await axios.post(`${API_BASE_URL}/timeline/extract/${notebookId}`);
  }

  async getExtractionProgress(notebookId: string): Promise<ExtractionProgress> {
    const response = await axios.get(`${API_BASE_URL}/timeline/progress/${notebookId}`);
    return response.data;
  }

  async deleteTimeline(notebookId: string): Promise<void> {
    await axios.delete(`${API_BASE_URL}/timeline/${notebookId}`);
  }
}

export const timelineService = new TimelineService();
