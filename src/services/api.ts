// API service for backend communication
import axios from 'axios';
import { invoke } from '@tauri-apps/api/core';

// If VITE_API_URL is not set, dynamically determine the backend IP
// based on where the frontend was loaded from (so it works across the network)
const defaultHost = typeof window !== 'undefined' ? window.location.hostname : 'localhost';
export const API_BASE_URL = import.meta.env.VITE_API_URL || `http://${defaultHost}:8000`;

export const WS_BASE_URL = (() => {
  try {
    const url = new URL(API_BASE_URL);
    url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:';
    return url.toString().replace(/\/$/, '');
  } catch {
    return API_BASE_URL.replace(/^https:/, 'wss:').replace(/^http:/, 'ws:');
  }
})();

// P0.1b (2026-05-15) → revised P0.1f (2026-05-21): per-launch app token,
// fetched from the Tauri shell via `get_app_token`. Cached here for the
// renderer's lifetime. If the backend rotates the token (e.g. crash +
// watchdog restart) the cached value goes stale; we detect that via a
// 401 response, clear caches on both sides (webview + Rust), and retry
// once. After that the new token is cached.
let appToken: string | null = null;
let tokenPromise: Promise<string | null> | null = null;

async function getAppToken(): Promise<string | null> {
  if (appToken) return appToken;
  if (tokenPromise) return tokenPromise;
  tokenPromise = invoke<string>('get_app_token')
    .then((t) => { appToken = t; return t; })
    .catch((err) => {
      console.warn('[api] could not fetch app token; continuing without:', err);
      return null;
    });
  return tokenPromise;
}

/** Clear webview-side cache + ask Rust to clear its cache and re-read from disk. */
async function refreshAppToken(): Promise<string | null> {
  appToken = null;
  tokenPromise = null;
  try {
    const t = await invoke<string>('refresh_app_token');
    appToken = t;
    return t;
  } catch (err) {
    console.warn('[api] refresh_app_token failed:', err);
    return null;
  }
}

/** Public: pre-warm the token cache (e.g. during app boot). */
export async function ensureAppToken(): Promise<void> {
  await getAppToken();
}

/**
 * P0.1f (2026-05-15): drop-in replacement for raw `fetch()` that attaches
 * the X-LocalBook-Token header. Used by call sites that need fetch-only
 * features (SSE streaming, multipart upload, etc.) the axios `api`
 * instance doesn't easily provide.
 *
 * On 401 (stale token after backend restart), clears caches and retries
 * once with a freshly-read token.
 */
export async function localFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  const buildInit = (t: string | null): RequestInit => {
    const headers = new Headers(init?.headers || {});
    if (t) headers.set('X-LocalBook-Token', t);
    return { ...init, headers };
  };
  let token = await getAppToken();
  let resp = await fetch(input, buildInit(token));
  if (resp.status === 401) {
    const fresh = await refreshAppToken();
    if (fresh && fresh !== token) {
      resp = await fetch(input, buildInit(fresh));
    }
  }
  return resp;
}

export const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 300000, // 5 minute timeout for long operations (uploads, concept extraction)
});

// Request interceptor: set Content-Type + attach app token.
api.interceptors.request.use(async (config) => {
  // Let axios set the Content-Type automatically for FormData (includes boundary)
  // Only set application/json for non-FormData requests
  if (!(config.data instanceof FormData)) {
    config.headers['Content-Type'] = 'application/json';
  }
  const token = await getAppToken();
  if (token) {
    config.headers['X-LocalBook-Token'] = token;
  }
  return config;
});

// Response interceptor: error logging + P0.1f 401 retry with refreshed token.
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const cfg = error.config;
    // 401 with no prior retry: token may be stale (backend restart). Refresh
    // and retry once. The _retried flag prevents an infinite loop if the
    // refresh itself returns 401 (e.g. backend genuinely down).
    if (error.response?.status === 401 && cfg && !cfg._retried) {
      cfg._retried = true;
      try {
        const fresh = await refreshAppToken();
        if (fresh) {
          cfg.headers = cfg.headers || {};
          cfg.headers['X-LocalBook-Token'] = fresh;
          return api.request(cfg);
        }
      } catch (refreshErr) {
        console.warn('API: token refresh failed during 401 retry:', refreshErr);
      }
    }
    console.error('API Error:', error);
    return Promise.reject(error);
  }
);

export default api;
