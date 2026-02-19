import { useState, useEffect, useCallback } from 'react';
import { API_BASE_URL } from '../services/api';

interface SystemHealth {
  llm: 'ok' | 'warn' | 'error' | 'unknown';
  embedding: 'ok' | 'warn' | 'error' | 'unknown';
  system: 'ok' | 'warn' | 'error' | 'unknown';
}

const POLL_INTERVAL = 30_000; // 30 seconds

export function useSystemHealth(): SystemHealth {
  const [health, setHealth] = useState<SystemHealth>({
    llm: 'unknown',
    embedding: 'unknown',
    system: 'unknown',
  });

  const check = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE_URL}/health`, { signal: AbortSignal.timeout(5000) });
      if (!res.ok) {
        setHealth({ llm: 'error', embedding: 'error', system: 'error' });
        return;
      }
      const data = await res.json();
      // Backend health endpoint typically returns service statuses
      setHealth({
        llm: data.llm_status === 'ok' || data.llm_available ? 'ok'
           : data.llm_status === 'degraded' ? 'warn' : data.llm_status ? 'error' : 'ok',
        embedding: data.embedding_status === 'ok' || data.embedding_available ? 'ok'
           : data.embedding_status === 'degraded' ? 'warn' : data.embedding_status ? 'error' : 'ok',
        system: res.ok ? 'ok' : 'error',
      });
    } catch {
      setHealth({ llm: 'error', embedding: 'error', system: 'error' });
    }
  }, []);

  useEffect(() => {
    check();
    const interval = setInterval(check, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [check]);

  return health;
}

export const STATUS_COLORS: Record<string, string> = {
  ok: 'text-green-600 dark:text-green-400',
  warn: 'text-yellow-500 dark:text-yellow-400',
  error: 'text-red-500 dark:text-red-400',
  unknown: 'text-gray-400 dark:text-gray-500',
};
