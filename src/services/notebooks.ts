// Notebook API service
import api from './api';
import { Notebook } from '../types';

export const notebookService = {
  async list(): Promise<Notebook[]> {
    const response = await api.get('/notebooks/');
    return response.data.notebooks;
  },

  async create(title: string, description?: string): Promise<Notebook> {
    const response = await api.post('/notebooks/', { title, description });
    return response.data;
  },

  async get(id: string): Promise<Notebook> {
    const response = await api.get(`/notebooks/${id}`);
    return response.data;
  },

  async delete(id: string): Promise<void> {
    await api.delete(`/notebooks/${id}`);
  },
};
