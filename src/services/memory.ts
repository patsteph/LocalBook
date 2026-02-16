/**
 * Memory Service - Core memory and memory stats API
 */
import { API_BASE_URL } from './api';

export interface CoreMemory {
  id: string;
  notebook_id: string;
  content: string;
  memory_type: string;
  created_at: string;
  updated_at: string;
}

export interface MemoryStats {
  total_core: number;
  total_archival: number;
  total_events: number;
  notebooks_with_memory: number;
}

class MemoryService {
  async getCoreMemories(): Promise<CoreMemory[]> {
    const response = await fetch(`${API_BASE_URL}/memory/core`);
    if (!response.ok) throw new Error('Failed to fetch core memories');
    return response.json();
  }

  async getStats(): Promise<MemoryStats> {
    const response = await fetch(`${API_BASE_URL}/memory/stats`);
    if (!response.ok) throw new Error('Failed to fetch memory stats');
    return response.json();
  }

  async updateCoreMemory(id: string, content: string): Promise<CoreMemory> {
    const response = await fetch(`${API_BASE_URL}/memory/core/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content }),
    });
    if (!response.ok) throw new Error('Failed to update memory');
    return response.json();
  }

  async deleteCoreMemory(id: string): Promise<void> {
    const response = await fetch(`${API_BASE_URL}/memory/core/${id}`, {
      method: 'DELETE',
    });
    if (!response.ok) throw new Error('Failed to delete memory');
  }
}

export const memoryService = new MemoryService();
