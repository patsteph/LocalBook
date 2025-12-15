import axios from 'axios';
import { Highlight, HighlightCreate } from '../types';
import { API_BASE_URL } from './api';

export const highlightService = {
  async create(highlight: HighlightCreate): Promise<Highlight> {
    const response = await axios.post(`${API_BASE_URL}/source-viewer/highlights`, highlight);
    return response.data;
  },

  async list(notebookId: string, sourceId: string): Promise<Highlight[]> {
    const response = await axios.get(`${API_BASE_URL}/source-viewer/highlights/${notebookId}/${sourceId}`);
    return response.data;
  },

  async updateAnnotation(highlightId: string, annotation: string): Promise<Highlight> {
    const response = await axios.patch(`${API_BASE_URL}/source-viewer/highlights/${highlightId}`, {
      annotation
    });
    return response.data;
  },

  async delete(highlightId: string): Promise<void> {
    await axios.delete(`${API_BASE_URL}/source-viewer/highlights/${highlightId}`);
  }
};
