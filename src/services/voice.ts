/**
 * Voice Notes Service - API calls for Whisper transcription
 */

import { API_BASE_URL } from './api';

const API_BASE = API_BASE_URL;

export interface TranscriptionResult {
  text: string;
  duration_seconds: number;
  language?: string;
  source_id?: string;
}

export interface VoiceStatus {
  available: boolean;
  model?: string;
  message: string;
}

export const voiceService = {
  async getStatus(): Promise<VoiceStatus> {
    const response = await fetch(`${API_BASE}/voice/status`);
    if (!response.ok) throw new Error('Failed to get voice status');
    return response.json();
  },

  async transcribe(
    file: File,
    notebookId: string,
    title?: string,
    addAsSource: boolean = true
  ): Promise<TranscriptionResult> {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('notebook_id', notebookId);
    if (title) formData.append('title', title);
    formData.append('add_as_source', addAsSource.toString());

    const response = await fetch(`${API_BASE}/voice/transcribe`, {
      method: 'POST',
      body: formData,
    });
    if (!response.ok) throw new Error('Failed to transcribe audio');
    return response.json();
  },

  async transcribeQuick(file: File): Promise<{ text: string; language: string }> {
    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(`${API_BASE}/voice/transcribe-quick`, {
      method: 'POST',
      body: formData,
    });
    if (!response.ok) throw new Error('Failed to transcribe audio');
    return response.json();
  },
};
