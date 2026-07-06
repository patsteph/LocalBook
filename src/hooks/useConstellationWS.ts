import { useEffect, useRef } from 'react';
import { WS_BASE_URL } from '../services/api';

type Handler = (message: any) => void;

/**
 * ONE shared, reconnecting WebSocket to `/constellation/ws` for the whole app
 * (S3/C5, 2026-07-06). Six components used to each open their OWN socket to this
 * endpoint (App, SourcesList via the reconnecting hook; ThemesPanel,
 * WebSearchResults, SiteSearch, Constellation3D hand-rolling raw sockets with
 * duplicated reconnect logic) — 6 connections per session where 1 suffices.
 *
 * This module-level singleton opens the socket on the first subscriber and closes
 * it when the last one unsubscribes; every subscriber gets every message and does
 * its own `message.type` routing exactly as before. Reconnect uses exponential backoff with a 30s cap.
 */
class ConstellationWSClient {
  private ws: WebSocket | null = null;
  private handlers = new Set<Handler>();
  private retryCount = 0;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private closing = false;

  private readonly url = `${WS_BASE_URL}/constellation/ws`;
  private readonly maxRetries = 10;
  private readonly baseDelay = 1000;

  subscribe(handler: Handler): () => void {
    this.handlers.add(handler);
    if (!this.ws) this.connect();
    return () => this.unsubscribe(handler);
  }

  private unsubscribe(handler: Handler): void {
    this.handlers.delete(handler);
    if (this.handlers.size === 0) this.disconnect();
  }

  private connect(): void {
    this.closing = false;
    try {
      const ws = new WebSocket(this.url);
      this.ws = ws;

      ws.onopen = () => {
        this.retryCount = 0;
      };

      ws.onmessage = (event) => {
        let message: any;
        try {
          message = JSON.parse(event.data);
        } catch {
          return; // ignore malformed frames
        }
        // Snapshot the set so a handler that unsubscribes mid-dispatch is safe,
        // and one throwing handler can't starve the others.
        for (const handler of Array.from(this.handlers)) {
          try {
            handler(message);
          } catch (err) {
            console.error('[constellation-ws] handler error:', err);
          }
        }
      };

      ws.onerror = () => {
        // onclose drives the reconnect; nothing to do here.
      };

      ws.onclose = () => {
        this.ws = null;
        if (this.closing || this.handlers.size === 0) return;
        if (this.retryCount < this.maxRetries) {
          const delay = Math.min(this.baseDelay * Math.pow(2, this.retryCount), 30000);
          this.retryCount++;
          this.retryTimer = setTimeout(() => this.connect(), delay);
        }
      };
    } catch {
      // WebSocket constructor throws on an invalid URL — stay silent, like the
      // hand-rolled sockets did.
    }
  }

  private disconnect(): void {
    this.closing = true;
    if (this.retryTimer) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
    if (this.ws) {
      this.ws.onclose = null; // prevent reconnect on intentional close
      this.ws.close();
      this.ws = null;
    }
    this.retryCount = 0;
  }
}

const client = new ConstellationWSClient();

/**
 * Subscribe a component to the shared constellation socket.
 *
 * `onMessage` may close over changing state freely — the latest closure is always
 * invoked via a ref, so the subscription itself never needs to tear down and
 * reopen (unlike the old raw sockets that re-connected whenever a dep changed).
 * Pass `enabled: false` to opt out (e.g. no notebook selected yet).
 */
export function useConstellationWS(onMessage: Handler, enabled: boolean = true): void {
  const handlerRef = useRef(onMessage);
  handlerRef.current = onMessage;

  useEffect(() => {
    if (!enabled) return;
    const wrapper: Handler = (message) => handlerRef.current(message);
    return client.subscribe(wrapper);
  }, [enabled]);
}
