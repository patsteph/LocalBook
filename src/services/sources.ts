// Source document API service
import api from './api';
import { Source } from '../types';

export const sourceService = {
  async list(notebookId: string): Promise<Source[]> {
    const response = await api.get(`/sources/${notebookId}`);
    return response.data || [];
  },

  async upload(notebookId: string, file: File): Promise<Source> {
    const formData = new FormData();
    formData.append('notebook_id', notebookId);
    formData.append('file', file);

    const response = await api.post('/sources/upload', formData, {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    });
    return response.data;
  },

  async delete(notebookId: string, sourceId: string): Promise<void> {
    await api.delete(`/sources/${notebookId}/${sourceId}`);
  },
};
