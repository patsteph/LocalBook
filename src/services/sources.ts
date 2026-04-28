// Source document API service
import api, { API_BASE_URL } from './api';
import { Source } from '../types';
import { invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';

/**
 * Detect whether we're running inside the Tauri runtime.
 * In Tauri, the global `__TAURI_INTERNALS__` is injected on every page.
 */
export const isTauri = (): boolean => {
  return typeof (window as any).__TAURI_INTERNALS__ !== 'undefined';
};

// ── Streaming upload progress types ────────────────────────────────────────
export interface UploadProgressEvent {
  stage: string;           // e.g. "received", "extracting", "chunking", "embedding", "complete", "error"
  percent: number;         // 0..100
  message: string;         // human-readable description
  details?: Record<string, any>;
}

export interface UploadStreamResult {
  source_id?: string;
  chunks?: number;
  characters?: number;
  format?: string;
  filename?: string;
}

export const sourceService = {
  async list(notebookId: string): Promise<Source[]> {
    const response = await api.get(`/sources/${notebookId}`);
    return response.data || [];
  },

  async upload(notebookId: string, file: File): Promise<Source> {
    const formData = new FormData();
    formData.append('notebook_id', notebookId);
    formData.append('file', file);

    try {
      const response = await api.post('/sources/upload', formData);
      return response.data;
    } catch (error: any) {
      console.error('[Upload] Failed:', {
        message: error.message,
        code: error.code,
        status: error.response?.status,
        statusText: error.response?.statusText,
        data: error.response?.data,
        config: {
          url: error.config?.url,
          baseURL: error.config?.baseURL,
          method: error.config?.method,
        },
      });
      throw error;
    }
  },

  /**
   * Upload a file and stream granular ingestion progress via SSE.
   *
   * The backend emits stage-by-stage events (received, detecting, extracting,
   * chunking, summarizing, hyde_questions, embedding, indexing, tagging,
   * complete/error) so the UI can show the full RAG journey.
   *
   * onProgress is called for every event EXCEPT the terminal one. The promise
   * resolves with the `complete` event's details on success, or rejects with
   * an Error on `error` / network failure.
   */
  async uploadWithProgress(
    notebookId: string,
    file: File,
    onProgress: (evt: UploadProgressEvent) => void,
    signal?: AbortSignal,
  ): Promise<UploadStreamResult> {
    const formData = new FormData();
    formData.append('notebook_id', notebookId);
    formData.append('file', file);

    const response = await fetch(`${API_BASE_URL}/sources/upload/stream`, {
      method: 'POST',
      body: formData,
      signal,
    });

    if (!response.ok || !response.body) {
      const text = await response.text().catch(() => '');
      throw new Error(
        `Upload failed (${response.status}): ${text || response.statusText}`,
      );
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // SSE events are delimited by blank lines ("\n\n").
        let sepIdx: number;
        while ((sepIdx = buffer.indexOf('\n\n')) !== -1) {
          const rawEvent = buffer.slice(0, sepIdx);
          buffer = buffer.slice(sepIdx + 2);

          // Ignore comments (lines starting with ":") and empty frames
          const lines = rawEvent.split('\n').filter(l => !l.startsWith(':') && l.trim() !== '');
          const dataLine = lines.find(l => l.startsWith('data:'));
          if (!dataLine) continue;
          const payload = dataLine.slice(5).trim();
          if (!payload || payload === '{}') continue;

          let evt: UploadProgressEvent;
          try {
            evt = JSON.parse(payload) as UploadProgressEvent;
          } catch {
            continue; // skip malformed frames
          }

          if (evt.stage === 'complete') {
            return (evt.details || {}) as UploadStreamResult;
          }
          if (evt.stage === 'error') {
            throw new Error(evt.message || 'Upload failed');
          }
          onProgress(evt);
        }
      }
    } finally {
      try { reader.releaseLock(); } catch { /* ignore */ }
    }

    // Stream ended without a complete/error event — treat as completed silently
    return {};
  },

  /**
   * Upload a file from a local path via the Tauri-native streaming command.
   *
   * Bypasses the WebView entirely so large files (40MB+) don't trigger
   * WKWebView OOM. The Rust backend streams the file from disk, multipart-encodes
   * it without buffering, posts to /sources/upload/stream, and forwards SSE
   * progress events via window.emit on a per-call channel.
   *
   * Only works inside Tauri. Caller must check isTauri() first.
   */
  async uploadFromPath(
    notebookId: string,
    path: string,
    onProgress: (evt: UploadProgressEvent) => void,
  ): Promise<UploadStreamResult> {
    // Unique channel per upload so concurrent uploads don't cross-talk
    const channelId = `${Date.now()}-${Math.random().toString(36).substr(2, 8)}`;
    const eventTopic = `upload-progress-${channelId}`;

    const unlisten = await listen<UploadProgressEvent>(eventTopic, (event) => {
      try {
        onProgress(event.payload);
      } catch (e) {
        console.warn('[uploadFromPath] onProgress callback threw:', e);
      }
    });

    try {
      const result = await invoke<UploadStreamResult>('upload_file_streaming', {
        path,
        notebookId,
        channelId,
      });
      return result || {};
    } finally {
      try { unlisten(); } catch { /* ignore */ }
    }
  },

  async delete(notebookId: string, sourceId: string): Promise<void> {
    await api.delete(`/sources/${notebookId}/${sourceId}`);
  },

  // =========================================================================
  // Document Tagging (v0.6.0)
  // =========================================================================

  async getTags(notebookId: string, sourceId: string): Promise<string[]> {
    const response = await api.get(`/sources/${notebookId}/${sourceId}/tags`);
    return response.data?.tags || [];
  },

  async setTags(notebookId: string, sourceId: string, tags: string[]): Promise<string[]> {
    const response = await api.put(`/sources/${notebookId}/${sourceId}/tags`, { tags });
    return response.data?.tags || [];
  },

  async addTag(notebookId: string, sourceId: string, tag: string): Promise<string[]> {
    const response = await api.post(`/sources/${notebookId}/${sourceId}/tags`, { tag });
    return response.data?.tags || [];
  },

  async removeTag(notebookId: string, sourceId: string, tag: string): Promise<string[]> {
    const response = await api.delete(`/sources/${notebookId}/${sourceId}/tags/${encodeURIComponent(tag)}`);
    return response.data?.tags || [];
  },

  async getAllTags(notebookId: string): Promise<{ tag: string; count: number }[]> {
    const response = await api.get(`/sources/${notebookId}/tags/all`);
    return response.data?.tags || [];
  },

  async getSourcesByTag(notebookId: string, tag: string): Promise<Source[]> {
    const response = await api.get(`/sources/${notebookId}/tags/${encodeURIComponent(tag)}/sources`);
    return response.data || [];
  },

  async autoTagAll(notebookId: string): Promise<{ message: string; queued: number; already_tagged: number; total: number }> {
    const response = await api.post(`/sources/${notebookId}/auto-tag-all`);
    return response.data;
  },

  // =========================================================================
  // Notes as Input (v1.3)
  // =========================================================================

  async createNote(notebookId: string, title: string, content: string): Promise<Source & { source_id: string }> {
    const response = await api.post(`/sources/${notebookId}/note`, { title, content });
    return response.data;
  },

  async updateNote(notebookId: string, sourceId: string, content: string, title?: string): Promise<Source & { source_id: string }> {
    const response = await api.put(`/sources/${notebookId}/${sourceId}/note`, { content, title });
    return response.data;
  },

  async getNoteContent(notebookId: string, sourceId: string): Promise<string> {
    const response = await api.get(`/source-viewer/content/${notebookId}/${sourceId}`);
    return response.data?.content || '';
  },

  async moveSource(notebookId: string, sourceId: string, targetNotebookId: string): Promise<{ message: string; source_id: string; target_notebook_id: string }> {
    const response = await api.post(`/sources/${notebookId}/${sourceId}/move`, { target_notebook_id: targetNotebookId });
    return response.data;
  },
};
