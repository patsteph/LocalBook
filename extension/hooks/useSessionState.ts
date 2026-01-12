import type { SessionState, SummaryResult, SearchResult, ChatMessage, ActionType, ViewMode } from "../types"

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
    const stateKey = `lb_page_${btoa(cleanUrl).slice(0, 32)}`
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
    const stateKey = `lb_page_${btoa(cleanUrl).slice(0, 32)}`
    const result = await chrome.storage.session.get(stateKey)
    const saved = result[stateKey] as SessionState | undefined
    
    if (saved && Date.now() - saved.timestamp < 30 * 60 * 1000) { // 30 min TTL
      console.log("Restoring session state for:", cleanUrl)
      return saved
    }
  } catch (e) {
    console.log("Session restore failed (non-critical):", e)
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
