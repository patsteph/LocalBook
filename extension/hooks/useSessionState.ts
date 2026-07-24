import type { SessionState, SummaryResult, SearchResult, ChatMessage, ActionType, ViewMode } from "../types"

// Per-page session key. `btoa(url).slice(0,32)` only captured the first ~24 chars of the URL,
// so every page on a domain (e.g. all of nytimes.com/…) collided into ONE key — page A's
// summary/chat leaked onto page B and B's capture overwrote A. djb2 over the WHOLE URL keeps
// the key short while being collision-safe for a session cache. (Fixed 2026-07-24.)
function sessionKeyFor(cleanUrl: string): string {
  let h = 5381
  for (let i = 0; i < cleanUrl.length; i++) {
    h = ((h << 5) + h + cleanUrl.charCodeAt(i)) | 0
  }
  return `lb_page_${(h >>> 0).toString(36)}`
}

export async function saveSessionState(
  cleanUrl: string,
  state: {
    summaryResult: SummaryResult | null
    searchResults: SearchResult[]
    chatMessages: ChatMessage[]
    pageActions: string[]
    currentAction: ActionType
    viewMode: ViewMode
  }
): Promise<void> {
  if (!cleanUrl) return
  
  try {
    const stateKey = sessionKeyFor(cleanUrl)
    await chrome.storage.session.set({
      [stateKey]: {
        ...state,
        timestamp: Date.now()
      }
    })
  } catch {
    // Ignore errors
  }
}

export async function restoreSessionState(cleanUrl: string): Promise<SessionState | null> {
  try {
    const stateKey = sessionKeyFor(cleanUrl)
    const result = await chrome.storage.session.get(stateKey)
    const saved = result[stateKey] as SessionState | undefined

    if (saved && Date.now() - saved.timestamp < 30 * 60 * 1000) { // 30 min TTL
      return saved
    }
  } catch {
    // Non-critical — a restore miss just means a fresh page view.
  }
  return null
}

export async function loadSavedNotebook(): Promise<string | null> {
  return new Promise((resolve) => {
    chrome.storage.local.get("selectedNotebook", (result) => {
      resolve(result.selectedNotebook || null)
    })
  })
}

export async function saveSelectedNotebook(notebookId: string): Promise<void> {
  if (notebookId) {
    await chrome.storage.local.set({ selectedNotebook: notebookId })
  }
}
