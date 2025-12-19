// Chat API service
import api, { API_BASE_URL } from './api';
import { ChatQuery, ChatResponse, Citation } from '../types';

export interface StreamCallbacks {
  onMode?: (deepThink: boolean, autoUpgraded: boolean) => void;
  onCitations?: (citations: Citation[], sources: string[], lowConfidence: boolean) => void;
  onQuickSummary?: (summary: string) => void;
  onToken?: (token: string) => void;
  onDone?: (followUpQuestions: string[]) => void;
  onError?: (error: string) => void;
}

export const chatService = {
  async query(query: ChatQuery): Promise<ChatResponse> {
    const response = await api.post('/chat/query', query);
    return response.data;
  },

  async queryStream(query: ChatQuery, callbacks: StreamCallbacks): Promise<void> {
    const response = await fetch(`${API_BASE_URL}/chat/query/stream`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(query),
    });

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`);
    }

    const reader = response.body?.getReader();
    if (!reader) {
      throw new Error('No response body');
    }

    const decoder = new TextDecoder();
    let buffer = '';

    const processLine = (line: string) => {
      if (line.startsWith('data: ')) {
        try {
          const data = JSON.parse(line.slice(6));
          
          if (data.error) {
            callbacks.onError?.(data.error);
          } else if (data.type === 'mode') {
            callbacks.onMode?.(data.deep_think, data.auto_upgraded);
          } else if (data.type === 'citations') {
            callbacks.onCitations?.(data.citations, data.sources, data.low_confidence);
          } else if (data.type === 'quick_summary') {
            callbacks.onQuickSummary?.(data.content);
          } else if (data.type === 'token') {
            callbacks.onToken?.(data.content);
          } else if (data.type === 'done') {
            callbacks.onDone?.(data.follow_up_questions || []);
          }
        } catch (e) {
          console.error('Failed to parse SSE data:', e);
        }
      }
    };

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        processLine(line);
      }
    }

    // Process any remaining data in the buffer after stream ends
    if (buffer.trim()) {
      processLine(buffer.trim());
    }
  },

  async getSuggestedQuestions(notebookId: string): Promise<string[]> {
    const response = await api.get(`/chat/suggested-questions/${notebookId}`);
    return response.data.questions;
  },
};
