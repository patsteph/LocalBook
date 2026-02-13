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

    console.log('[Upload] Starting upload:', {
      filename: file.name,
      size: file.size,
      type: file.type,
      notebookId,
    });

    try {
      const response = await api.post('/sources/upload', formData);
      console.log('[Upload] Success:', response.data);
      return response.data;
    } catch (error: any) {
      console.error('[Upload] Failed:', {
        message: error.message,
        code: error.code,
        status: error.response?.status,
        statusText: error.response?.statusText,
        data: error.response?.data,
        config: {
          url: error.config?.url,
          baseURL: error.config?.baseURL,
          method: error.config?.method,
        },
      });
      throw error;
    }
  },

  async delete(notebookId: string, sourceId: string): Promise<void> {
    await api.delete(`/sources/${notebookId}/${sourceId}`);
  },

  // =========================================================================
  // Document Tagging (v0.6.0)
  // =========================================================================

  async getTags(notebookId: string, sourceId: string): Promise<string[]> {
    const response = await api.get(`/sources/${notebookId}/${sourceId}/tags`);
    return response.data?.tags || [];
  },

  async setTags(notebookId: string, sourceId: string, tags: string[]): Promise<string[]> {
    const response = await api.put(`/sources/${notebookId}/${sourceId}/tags`, { tags });
    return response.data?.tags || [];
  },

  async addTag(notebookId: string, sourceId: string, tag: string): Promise<string[]> {
    const response = await api.post(`/sources/${notebookId}/${sourceId}/tags`, { tag });
    return response.data?.tags || [];
  },

  async removeTag(notebookId: string, sourceId: string, tag: string): Promise<string[]> {
    const response = await api.delete(`/sources/${notebookId}/${sourceId}/tags/${encodeURIComponent(tag)}`);
    return response.data?.tags || [];
  },

  async getAllTags(notebookId: string): Promise<{ tag: string; count: number }[]> {
    const response = await api.get(`/sources/${notebookId}/tags/all`);
    return response.data?.tags || [];
  },

  async getSourcesByTag(notebookId: string, tag: string): Promise<Source[]> {
    const response = await api.get(`/sources/${notebookId}/tags/${encodeURIComponent(tag)}/sources`);
    return response.data || [];
  },

  async autoTagAll(notebookId: string): Promise<{ message: string; queued: number; already_tagged: number; total: number }> {
    const response = await api.post(`/sources/${notebookId}/auto-tag-all`);
    return response.data;
  },
};
