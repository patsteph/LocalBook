/**
 * Writing Assistant Service - API calls for AI writing assistance
 */

import { API_BASE_URL } from './api';

const API_BASE = API_BASE_URL;

export interface FormatOption {
  value: string;
  label: string;
  description: string;
}

export interface WritingResult {
  content: string;
  format_used: string;
  word_count: number;
  suggestions: string[];
}

export const writingService = {
  async getFormats(): Promise<FormatOption[]> {
    const response = await fetch(`${API_BASE}/writing/formats`);
    if (!response.ok) throw new Error('Failed to get formats');
    return response.json();
  },

  async getTasks(): Promise<{ value: string; label: string; description: string }[]> {
    const response = await fetch(`${API_BASE}/writing/tasks`);
    if (!response.ok) throw new Error('Failed to get tasks');
    return response.json();
  },

  async assist(
    content: string,
    task: string = 'improve',
    formatStyle: string = 'professional'
  ): Promise<WritingResult> {
    const response = await fetch(`${API_BASE}/writing/assist`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        content,
        task,
        format_style: formatStyle,
      }),
    });
    if (!response.ok) throw new Error('Failed to assist writing');
    return response.json();
  },

  async writeFromSources(
    notebookId: string,
    task: string = 'summarize',
    formatStyle: string = 'professional',
    focusTopic?: string,
    maxWords: number = 500
  ): Promise<WritingResult> {
    const response = await fetch(`${API_BASE}/writing/from-sources`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        notebook_id: notebookId,
        task,
        format_style: formatStyle,
        focus_topic: focusTopic,
        max_words: maxWords,
      }),
    });
    if (!response.ok) throw new Error('Failed to write from sources');
    return response.json();
  },

  async transformText(
    text: string,
    task: string = 'improve',
    formatStyle: string = 'professional',
    maxWords: number = 500
  ): Promise<WritingResult> {
    const response = await fetch(`${API_BASE}/writing/transform`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        text,
        task,
        format_style: formatStyle,
        max_words: maxWords,
      }),
    });
    if (!response.ok) throw new Error('Failed to transform text');
    return response.json();
  },
};
