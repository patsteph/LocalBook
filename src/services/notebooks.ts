// Notebook API service
import api from './api';
import { Notebook } from '../types';

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

  async create(title: string, description?: string, color?: string): Promise<Notebook> {
    const response = await api.post('/notebooks/', { title, description, color });
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
};
