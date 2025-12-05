// Audio Studio API service
import api from './api';
import { AudioGeneration, AudioGenerateRequest } from '../types';

export const audioService = {
  async generate(request: AudioGenerateRequest): Promise<AudioGeneration> {
    const response = await api.post('/audio/generate', request);
    return response.data;
  },

  async list(notebookId: string): Promise<AudioGeneration[]> {
    const response = await api.get(`/audio/${notebookId}`);
    return response.data;
  },

  async get(notebookId: string, audioId: string): Promise<AudioGeneration> {
    const response = await api.get(`/audio/${notebookId}/${audioId}`);
    return response.data;
  },

  getDownloadUrl(audioId: string): string {
    return `${api.defaults.baseURL}/audio/download/${audioId}`;
  },
};
