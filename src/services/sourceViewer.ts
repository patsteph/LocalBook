// Source Viewer & Notes API service
import api from './api';

export interface SourceContent {
  id: string;
  filename: string;
  content: string;
  format: string;
}

export const sourceViewerService = {
  async getContent(notebookId: string, sourceId: string): Promise<SourceContent> {
    const response = await api.get(`/source-viewer/content/${notebookId}/${sourceId}`);
    return response.data;
  },

  async getNotes(notebookId: string, sourceId: string): Promise<string> {
    const response = await api.get(`/source-viewer/notes/${notebookId}/${sourceId}`);
    return response.data.notes;
  },

  async saveNotes(notebookId: string, sourceId: string, notes: string): Promise<void> {
    await api.post('/source-viewer/notes', {
      notebook_id: notebookId,
      source_id: sourceId,
      notes
    });
  },
};
