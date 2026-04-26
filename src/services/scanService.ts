// Scan batch API service — Sprint 8 (multi-page scanning session)
//
// Mirrors the shape of sourceService.uploadWithProgress() so UI code that
// consumes SSE progress events is uniform across upload and batch-scan.
import { API_BASE_URL } from './api';

// Re-export the same event shape the upload flow uses — same backend contract.
export interface ScanProgressEvent {
  stage: string;
  percent: number;
  message: string;
  details?: Record<string, any>;
}

export interface ScanBatchResult {
  note_id?: string;
  total_pages?: number;
  chars?: number;
  title?: string;
}

export const scanService = {
  /**
   * Submit a batch of image paths for sequential OCR + merged-note creation.
   *
   * Streams per-page progress via SSE. onProgress is called for every event
   * except the terminal one. The promise resolves with the `complete` event's
   * details, or rejects on `error` / network failure.
   */
  async processBatchWithProgress(
    filePaths: string[],
    opts: {
      notebookId?: string | null;
      mode?: 'document' | 'photo';
      onProgress: (evt: ScanProgressEvent) => void;
      signal?: AbortSignal;
    },
  ): Promise<ScanBatchResult> {
    const { notebookId, mode = 'document', onProgress, signal } = opts;

    const response = await fetch(`${API_BASE_URL}/scan/process-batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        file_paths: filePaths,
        notebook_id: notebookId || null,
        mode,
      }),
      signal,
    });

    if (!response.ok || !response.body) {
      const text = await response.text().catch(() => '');
      throw new Error(
        `Batch scan failed (${response.status}): ${text || response.statusText}`,
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

        let sepIdx: number;
        while ((sepIdx = buffer.indexOf('\n\n')) !== -1) {
          const rawEvent = buffer.slice(0, sepIdx);
          buffer = buffer.slice(sepIdx + 2);

          const lines = rawEvent
            .split('\n')
            .filter(l => !l.startsWith(':') && l.trim() !== '');
          const dataLine = lines.find(l => l.startsWith('data:'));
          if (!dataLine) continue;
          const payload = dataLine.slice(5).trim();
          if (!payload || payload === '{}') continue;

          let evt: ScanProgressEvent;
          try {
            evt = JSON.parse(payload) as ScanProgressEvent;
          } catch {
            continue;
          }

          if (evt.stage === 'complete') {
            return (evt.details || {}) as ScanBatchResult;
          }
          if (evt.stage === 'error') {
            throw new Error(evt.message || 'Batch scan failed');
          }
          onProgress(evt);
        }
      }
    } finally {
      try { reader.releaseLock(); } catch { /* ignore */ }
    }

    return {};
  },
};
