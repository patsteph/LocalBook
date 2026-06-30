// API service for backend communication
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

// Q2 (2026-06-30): localFetch-backed `api` shim — drops axios while preserving
// the axios-style interface the services already use (api.get/post/put/patch/
// delete returning `{ data }`, throwing on non-2xx). Token attach + 401-retry
// come from localFetch; JSON Content-Type is set here but NOT for FormData (the
// browser adds the multipart boundary); a `params` config serializes to a query
// string. Error shape mirrors axios's `err.response.{status,data}`.
interface ApiConfig {
  params?: Record<string, any>;
  headers?: Record<string, string>;
}

async function apiRequest<T = any>(
  method: string,
  path: string,
  body?: any,
  config?: ApiConfig,
): Promise<{ data: T; status: number }> {
  let url = path.startsWith('http') ? path : `${API_BASE_URL}${path}`;
  if (config?.params) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(config.params)) {
      if (v !== undefined && v !== null) qs.append(k, String(v));
    }
    const s = qs.toString();
    if (s) url += (url.includes('?') ? '&' : '?') + s;
  }
  const headers: Record<string, string> = { ...(config?.headers || {}) };
  const init: RequestInit = { method };
  if (body !== undefined && body !== null) {
    if (body instanceof FormData) {
      init.body = body; // do NOT set Content-Type — the browser adds the boundary
    } else {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
  }
  if (Object.keys(headers).length) init.headers = headers;

  const response = await localFetch(url, init);
  if (!response.ok) {
    const err: any = new Error(`API ${method} ${path} failed: HTTP ${response.status}`);
    err.response = { status: response.status };
    try { err.response.data = await response.json(); } catch { /* non-JSON error body */ }
    console.error('API Error:', err);
    throw err;
  }
  const text = await response.text();
  return { data: (text ? JSON.parse(text) : null) as T, status: response.status };
}

export const api = {
  get: <T = any>(path: string, config?: ApiConfig) => apiRequest<T>('GET', path, undefined, config),
  post: <T = any>(path: string, body?: any, config?: ApiConfig) => apiRequest<T>('POST', path, body, config),
  put: <T = any>(path: string, body?: any, config?: ApiConfig) => apiRequest<T>('PUT', path, body, config),
  patch: <T = any>(path: string, body?: any, config?: ApiConfig) => apiRequest<T>('PATCH', path, body, config),
  delete: <T = any>(path: string, config?: ApiConfig) => apiRequest<T>('DELETE', path, undefined, config),
};

export default api;
