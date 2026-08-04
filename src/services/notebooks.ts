// Notebook API service
import api from './api';
import { Notebook, NotebookSection } from '../types';

export interface CursorTableInfo {
  table_name: string;
  row_count: number;
  columns: string[];
}
export interface CursorConnectResult {
  ok: boolean;
  error?: string;
  db_filename?: string;
  tables?: CursorTableInfo[];
  table_count?: number;
  row_total?: number;
  governance_files?: string[];
  schema_changed?: boolean;
}
export interface CursorDataStatus {
  connected: boolean;
  type: string;
  folder_path?: string;
  db_filename?: string;
  tables?: CursorTableInfo[];
  governance_files?: string[];
  refreshed_at?: string;
  data_status?: string;
  ready?: boolean;
  table_count?: number;
  row_total?: number;
  views?: number;
}

// Default color palette for notebooks
export const NOTEBOOK_COLORS = [
  '#3B82F6', // Blue
  '#10B981', // Emerald
  '#8B5CF6', // Violet
  '#F59E0B', // Amber
  '#EF4444', // Red
  '#EC4899', // Pink
  '#06B6D4', // Cyan
  '#84CC16', // Lime
  '#F97316', // Orange
  '#6366F1', // Indigo
];

export const notebookService = {
  async list(): Promise<Notebook[]> {
    const response = await api.get('/notebooks/');
    return response.data.notebooks;
  },

  async create(
    title: string,
    description?: string,
    color?: string,
    opts?: { type?: 'standard' | 'cursor'; folder_path?: string },
  ): Promise<Notebook> {
    const response = await api.post('/notebooks/', {
      title, description, color, type: opts?.type, folder_path: opts?.folder_path,
    });
    return response.data;
  },

  // Cursor Style: connect / refresh a folder (the .db + AGENTS.md docs), read connection status.
  async connectFolder(id: string, folderPath: string): Promise<CursorConnectResult> {
    const response = await api.post(`/notebooks/${id}/connect`, { folder_path: folderPath });
    return response.data;
  },

  async refreshData(id: string): Promise<CursorConnectResult> {
    const response = await api.post(`/notebooks/${id}/refresh`);
    return response.data;
  },

  async dataStatus(id: string): Promise<CursorDataStatus> {
    const response = await api.get(`/notebooks/${id}/data-status`);
    return response.data;
  },

  async get(id: string): Promise<Notebook> {
    const response = await api.get(`/notebooks/${id}`);
    return response.data;
  },

  async delete(id: string): Promise<void> {
    await api.delete(`/notebooks/${id}`);
  },

  async updateColor(id: string, color: string): Promise<Notebook> {
    const response = await api.put(`/notebooks/${id}/color`, { color });
    return response.data;
  },

  async rename(id: string, title: string): Promise<Notebook> {
    const response = await api.put(`/notebooks/${id}/rename`, { title });
    return response.data;
  },

  async move(id: string, sectionId: string | null, sortOrder?: number): Promise<Notebook> {
    const response = await api.put(`/notebooks/${id}/move`, { section_id: sectionId, sort_order: sortOrder });
    return response.data;
  },

  async listSections(): Promise<NotebookSection[]> {
    const response = await api.get('/notebooks/sections/list');
    return response.data.sections;
  },

  async createSection(name: string): Promise<NotebookSection> {
    const response = await api.post('/notebooks/sections/', { name });
    return response.data;
  },

  async updateSection(id: string, updates: { name?: string; collapsed?: boolean }): Promise<NotebookSection> {
    const response = await api.put(`/notebooks/sections/${id}`, updates);
    return response.data;
  },

  async deleteSection(id: string): Promise<void> {
    await api.delete(`/notebooks/sections/${id}`);
  },

  async reorderSections(sectionIds: string[]): Promise<void> {
    await api.put('/notebooks/sections/reorder', { section_ids: sectionIds });
  },
};
