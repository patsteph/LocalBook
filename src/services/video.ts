/**
 * Video Service - API calls for video explainer generation
 */

import { API_BASE_URL } from './api';

const API_BASE = API_BASE_URL;

export interface VideoGeneration {
  video_id: string;
  notebook_id: string;
  topic: string;
  duration_minutes: number;
  visual_style: string;
  voice: string;
  format_type: string;
  video_file_path: string | null;
  duration_seconds: number | null;
  slide_count: number | null;
  status: 'pending' | 'processing' | 'completed' | 'failed';
  error_message: string | null;
  created_at: string;
}

export interface VideoGenerateRequest {
  notebook_id: string;
  topic?: string;
  duration_minutes?: number;
  visual_style?: string;
  narrator_gender?: string;  // "male" or "female"
  accent?: string;           // "us", "uk", "es", "fr", etc.
  voice?: string;            // Legacy: direct Kokoro voice ID override
  format_type?: 'explainer' | 'brief';
  chat_context?: string;     // Recent chat conversation for "From Chat" mode
}

export interface VisualStyle {
  id: string;
  name: string;
  accent_color: string;
  bg_color: string;
  is_custom?: boolean;
}

export const videoService = {
  async generate(request: VideoGenerateRequest): Promise<VideoGeneration> {
    const response = await fetch(`${API_BASE}/video/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
    });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail || 'Failed to start video generation');
    }
    return response.json();
  },

  async list(notebookId: string): Promise<VideoGeneration[]> {
    const response = await fetch(`${API_BASE}/video/${notebookId}`);
    if (!response.ok) throw new Error('Failed to list videos');
    const data = await response.json();
    return data.generations || [];
  },

  async getStatus(videoId: string): Promise<VideoGeneration> {
    const response = await fetch(`${API_BASE}/video/status/${videoId}`);
    if (!response.ok) throw new Error('Failed to get video status');
    return response.json();
  },

  getStreamUrl(videoId: string): string {
    return `${API_BASE}/video/stream/${videoId}`;
  },

  async delete(videoId: string): Promise<void> {
    const response = await fetch(`${API_BASE}/video/remove/${videoId}`, {
      method: 'DELETE',
    });
    if (!response.ok) throw new Error('Failed to delete video');
  },

  async listStyles(): Promise<VisualStyle[]> {
    const response = await fetch(`${API_BASE}/video/styles/list`);
    if (!response.ok) throw new Error('Failed to list video styles');
    const data = await response.json();
    return data.styles || [];
  },
};
