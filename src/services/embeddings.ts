import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export interface EmbeddingModelResponse {
  model_name: string;
  dimensions: number;
  needs_reembedding: boolean;
}

export interface ReembedProgressResponse {
  current: number;
  total: number;
  status: string;
}

class EmbeddingService {
  async getCurrentModel(notebookId: string): Promise<EmbeddingModelResponse> {
    const response = await axios.get(`${API_BASE_URL}/embeddings/model/${notebookId}`);
    return response.data;
  }

  async changeModel(notebookId: string, modelName: string): Promise<void> {
    await axios.post(`${API_BASE_URL}/embeddings/model/${notebookId}`, {
      model_name: modelName,
    });
  }

  async reembedNotebook(notebookId: string): Promise<void> {
    await axios.post(`${API_BASE_URL}/embeddings/reembed/${notebookId}`);
  }

  async getReembedProgress(notebookId: string): Promise<ReembedProgressResponse> {
    const response = await axios.get(`${API_BASE_URL}/embeddings/progress/${notebookId}`);
    return response.data;
  }
}

export const embeddingService = new EmbeddingService();
