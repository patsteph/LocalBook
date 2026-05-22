export interface Notebook {
  id: string
  name: string
  source_count: number
}

export interface PageInfo {
  url: string
  cleanUrl: string
  title: string
  domain: string
}

export interface SummaryResult {
  summary: string
  key_points: string[]
  key_concepts: string[]
  reading_time: number
  raw_content?: string
  outbound_links?: OutboundLink[]
}

export interface OutboundLink {
  url: string
  text: string
  context: string
}

export interface TransformResult {
  type: string
  content: string
  timestamp: number
}

export interface LinkInfo {
  outgoing: string[]
  incoming: string[]
}

export interface ChatMessage {
  role: "user" | "assistant"
  content: string
  timestamp: number
}

export interface PageContext {
  url: string
  title: string
  summary?: string
  content?: string
}

export type ViewMode = "actions" | "chat" | "research" | "transform"
export type ActionType = "summary" | "scrape" | "links" | "compare" | "automate" | "chat" | "collector" | null

export interface SearchResult {
  title: string
  url: string
  snippet: string
  source_site: string
  published_date?: string
  author?: string
  thumbnail?: string
  metadata?: {
    duration?: string
    view_count?: string
    read_time?: string
    video_id?: string
  }
}

export interface JourneyEntry {
  url: string
  title: string
  actions: string[]
  concepts: string[]
  timestamp: number
}

export interface SessionState {
  summaryResult: SummaryResult | null
  searchResults: SearchResult[]
  chatMessages: ChatMessage[]
  pageActions: string[]
  currentAction: ActionType
  viewMode: ViewMode
  timestamp: number
}

export const API_BASE = "http://localhost:8000"

// ----------------------------------------------------------------------------
// P0.1e (2026-05-15): app-token bootstrap + token-bearing fetch wrapper.
//
// The Tauri webview reads the per-launch token from disk via a Rust command.
// The extension can't do that, so on first need we GET /auth/bootstrap —
// the backend verifies our Origin header matches the pinned extension ID
// (browser-enforced, JS-unspoofable), and returns the token. We cache it
// in chrome.storage.local. tokenFetch attaches X-LocalBook-Token on every
// request; on 401 (token rotated after a backend restart) it auto-refreshes
// once and retries — fully transparent to callers.
// ----------------------------------------------------------------------------

const APP_TOKEN_KEY = "localbook_app_token"

async function getStoredToken(): Promise<string | null> {
  try {
    const r = await chrome.storage.local.get(APP_TOKEN_KEY)
    return r[APP_TOKEN_KEY] ?? null
  } catch {
    return null
  }
}

async function bootstrapToken(): Promise<string | null> {
  try {
    // POST with a JSON body to force "non-simple" CORS so Chrome sends the
    // Origin header — Chrome omits Origin on simple GETs from extension
    // service workers, which prevents our backend from identifying us.
    const r = await fetch(`${API_BASE}/auth/bootstrap`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    })
    if (!r.ok) {
      console.warn(`[ext] /auth/bootstrap failed: ${r.status}`)
      return null
    }
    const { token } = await r.json()
    if (token) {
      await chrome.storage.local.set({ [APP_TOKEN_KEY]: token })
    }
    return token ?? null
  } catch (e) {
    console.warn("[ext] /auth/bootstrap error:", e)
    return null
  }
}

async function getToken(): Promise<string | null> {
  const stored = await getStoredToken()
  if (stored) return stored
  return bootstrapToken()
}

/** Public: clear the cached token (e.g. for testing or after a hard 401). */
export async function clearStoredToken(): Promise<void> {
  await chrome.storage.local.remove(APP_TOKEN_KEY)
}

/**
 * Drop-in replacement for `fetch()` that attaches X-LocalBook-Token.
 * Auto-bootstraps on first call; auto-refreshes once on 401.
 */
export async function tokenFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  let token = await getToken()
  const withToken = (t: string | null): RequestInit => {
    const headers = new Headers(init?.headers || {})
    if (t) headers.set("X-LocalBook-Token", t)
    return { ...init, headers }
  }
  let resp = await fetch(input, withToken(token))
  // On 401, token may have rotated (backend restart). Force re-bootstrap once.
  if (resp.status === 401 && token) {
    await clearStoredToken()
    token = await bootstrapToken()
    if (token) {
      resp = await fetch(input, withToken(token))
    }
  }
  return resp
}
