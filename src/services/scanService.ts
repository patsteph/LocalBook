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
  confidence?: number;
}

export interface ScanOcrBatchResult {
  merged_text: string;
  page_texts?: string[];
  total_pages?: number;
  chars?: number;
  confidence?: number;
}

// Capture modes the backend understands. The first 12 are auto-classifiable
// by the vision-model classifier; the remainder are user-pick-only and live
// in MODE_PROMPTS so the UI can offer them as explicit choices.
export type ScanMode =
  | 'document' | 'handwriting' | 'mixed' | 'math' | 'whiteboard'
  | 'drawing' | 'diagram' | 'photo'
  | 'receipt' | 'business_card' | 'code' | 'slide'
  | 'recipe' | 'resume' | 'glossary' | 'title_page'
  | 'calendar' | 'form' | 'map' | 'index_page' | 'collage';

/**
 * Read a streaming SSE response and dispatch events to onProgress.
 * Resolves with the terminal `complete` event's details, or rejects on
 * `error` events / network failures. Used by both /scan/process-batch and
 * /scan/ocr-batch — same SSE contract, different terminal payload shape.
 */
async function readScanSseStream<T>(
  response: Response,
  onProgress: (evt: ScanProgressEvent) => void,
): Promise<T> {
  if (!response.ok || !response.body) {
    const text = await response.text().catch(() => '');
    throw new Error(
      `Scan request failed (${response.status}): ${text || response.statusText}`,
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
          return (evt.details || {}) as T;
        }
        if (evt.stage === 'error') {
          throw new Error(evt.message || 'Scan failed');
        }
        onProgress(evt);
      }
    }
  } finally {
    try { reader.releaseLock(); } catch { /* ignore */ }
  }

  return {} as T;
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
      mode?: ScanMode;
      onProgress: (evt: ScanProgressEvent) => void;
      signal?: AbortSignal;
      // Optional post-OCR translation. Set to a language name (e.g. 'Spanish')
      // to get a Translation section appended.
      targetLanguage?: string;
      // Optional: append the OCR result to an existing note instead of
      // creating a new one. Falls back to create-new if the note doesn't exist.
      appendTo?: string;
    },
  ): Promise<ScanBatchResult> {
    const { notebookId, mode = 'document', onProgress, signal, targetLanguage, appendTo } = opts;

    const response = await fetch(`${API_BASE_URL}/scan/process-batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        file_paths: filePaths,
        notebook_id: notebookId || null,
        mode,
        target_language: targetLanguage ?? null,
        append_to: appendTo ?? null,
      }),
      signal,
    });

    return readScanSseStream<ScanBatchResult>(response, onProgress);
  },

  /**
   * Inline OCR — same per-page progress as processBatchWithProgress, but
   * returns the merged markdown WITHOUT creating a new note. Used when the
   * user is editing a note and wants the scan content inserted at the
   * cursor (Sprint 9 — append-to-open-note flow).
   */
  async ocrBatchWithProgress(
    filePaths: string[],
    opts: {
      mode?: ScanMode;
      onProgress: (evt: ScanProgressEvent) => void;
      signal?: AbortSignal;
      targetLanguage?: string;
    },
  ): Promise<ScanOcrBatchResult> {
    const { mode = 'document', onProgress, signal, targetLanguage } = opts;

    const response = await fetch(`${API_BASE_URL}/scan/ocr-batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        file_paths: filePaths,
        mode,
        target_language: targetLanguage ?? null,
      }),
      signal,
    });

    return readScanSseStream<ScanOcrBatchResult>(response, onProgress);
  },
};
