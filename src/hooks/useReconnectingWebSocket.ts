import { useEffect, useRef } from 'react';

interface UseReconnectingWebSocketOptions {
  url: string;
  enabled: boolean;
  onMessage: (data: any) => void;
  onError?: (event: Event) => void;
  maxRetries?: number;
  baseDelay?: number;
}

/**
 * A hook that opens a WebSocket and automatically reconnects
 * with exponential backoff on close or error.
 */
export function useReconnectingWebSocket({
  url,
  enabled,
  onMessage,
  onError,
  maxRetries = 10,
  baseDelay = 1000,
}: UseReconnectingWebSocketOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const retryCountRef = useRef(0);
  const retryTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const unmountedRef = useRef(false);

  useEffect(() => {
    unmountedRef.current = false;
    if (!enabled) return;

    const connect = () => {
      if (unmountedRef.current) return;

      try {
        const ws = new WebSocket(url);
        wsRef.current = ws;

        ws.onopen = () => {
          retryCountRef.current = 0;
        };

        ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            onMessage(data);
          } catch {
            // ignore malformed messages
          }
        };

        ws.onerror = (e) => {
          onError?.(e);
        };

        ws.onclose = () => {
          if (unmountedRef.current) return;
          if (retryCountRef.current < maxRetries) {
            const delay = Math.min(baseDelay * Math.pow(2, retryCountRef.current), 30000);
            retryCountRef.current++;
            retryTimerRef.current = setTimeout(connect, delay);
          }
        };
      } catch {
        // WebSocket constructor can throw if URL is invalid
      }
    };

    connect();

    return () => {
      unmountedRef.current = true;
      if (retryTimerRef.current) clearTimeout(retryTimerRef.current);
      if (wsRef.current) {
        wsRef.current.onclose = null; // prevent reconnect on intentional close
        wsRef.current.close();
      }
      wsRef.current = null;
    };
  }, [url, enabled]);
}
