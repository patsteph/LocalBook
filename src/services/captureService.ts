/**
 * Capture Service — TypeScript API client for the QR Handoff capture system.
 *
 * Manages session lifecycle, WebSocket subscriptions, and cleanup.
 */

import { API_BASE_URL } from './api';

export interface CaptureSession {
  session_id: string;
  token: string;
  capture_url: string;    // http://<mac-ip>:8443/capture/page/{id}?t={token}
  short_url: string;      // http://<mac-ip>:8443/c/A1B2C3 (for compact QR)
  short_code: string;     // 6-char code
  ws_url: string;         // ws://localhost:8000/capture/ws/{id}
}

export interface CapturePageEvent {
  type:
    | 'page_received'
    | 'page_classifying'
    | 'page_processing'
    | 'page_complete'
    | 'page_error'
    | 'session_complete';
  page_index: number;
  content_type?: string;  // 'document' | 'whiteboard' | 'drawing' | 'photo' | 'math'
  ocr_text?: string;
  error?: string;
  // Failure category set by the backend so the UI can render targeted
  // guidance instead of a generic message. See backend/services/
  // capture_queue.py CapturePageResult for the canonical definitions.
  error_type?:
    | ''
    | 'vision_model'
    | 'cleanup_model'
    | 'timeout'
    | 'generic';
  // Name of the model that failed (only set when error_type is a model
  // category). Lets the UI say "Granite 3.3 Vision crashed" instead of
  // a generic "vision model failed".
  error_model?: string;
  file_name?: string;
  stats?: {
    pages_received: number;
    pages_processed: number;
    total_chars: number;
    errors: number;
    pending: number;
  };
}

export const captureService = {
  /**
   * Create a new capture session. Returns session info including
   * the QR code URL for the iPhone.
   */
  async createSession(): Promise<CaptureSession> {
    const resp = await fetch(`${API_BASE_URL}/capture/session`, {
      method: 'POST',
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(err.detail || `Failed to create session (${resp.status})`);
    }
    return resp.json();
  },

  /**
   * Close a capture session and clean up server-side resources.
   */
  async closeSession(sessionId: string): Promise<void> {
    try {
      await fetch(`${API_BASE_URL}/capture/session/${sessionId}`, {
        method: 'DELETE',
      });
    } catch (err) {
      console.warn('[captureService] Failed to close session:', err);
    }
  },

  /**
   * Open a WebSocket connection to receive real-time OCR results.
   * Returns a cleanup function that closes the connection.
   */
  connectWebSocket(
    session: CaptureSession,
    onEvent: (event: CapturePageEvent) => void,
  ): () => void {
    const wsUrl = `${API_BASE_URL.replace('http', 'ws')}/capture/ws/${session.session_id}?t=${session.token}`;

    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    const connect = () => {
      if (closed) return;
      ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        console.debug('[captureService] WebSocket connected');
      };

      ws.onmessage = (event) => {
        try {
          const data: CapturePageEvent = JSON.parse(event.data);
          onEvent(data);
        } catch (err) {
          console.warn('[captureService] Failed to parse WS message:', err);
        }
      };

      ws.onclose = () => {
        if (!closed) {
          // Reconnect after 2 seconds
          reconnectTimer = setTimeout(connect, 2000);
        }
      };

      ws.onerror = (err) => {
        console.warn('[captureService] WebSocket error:', err);
      };
    };

    connect();

    // Return cleanup function
    return () => {
      closed = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      if (ws) {
        ws.onclose = null; // Prevent reconnect
        ws.close();
        ws = null;
      }
    };
  },
};
