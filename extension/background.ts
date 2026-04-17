// Background service worker for LocalBook extension

import { API_BASE } from "./types"

// Set side panel to open when extension icon is clicked
chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(console.error)

interface Notebook {
  id: string
  name: string
  source_count: number
}

let cachedNotebooks: Notebook[] = []

// Scrape-assist polling constants (used in onInstalled/onStartup below)
const SCRAPE_POLL_ALARM = "localbook-scrape-poll"
// Poll at 30s normally. Only switches to fast (3s) when there are actually
// pending scrape requests, then reverts to slow when the queue is empty.
// This prevents the service worker from waking every 3s for nothing.
const SCRAPE_POLL_SLOW_MINUTES = 0.5   // 30 seconds
const SCRAPE_POLL_FAST_MINUTES = 0.05  // ~3 seconds (Chrome minimum)
let scrapeAlarmFast = false

// Concurrency limit — never open more than 2 scrape-assist tabs at once
const MAX_CONCURRENT_SCRAPES = 2

// Track tabs opened for scraping so we can clean up orphans.
// Uses chrome.storage.session so the map survives service worker
// termination (cleared on browser close, not on worker restart).
const SCRAPE_TAB_TTL_MS = 60_000  // close orphaned tabs after 60s
const SCRAPE_TABS_KEY = "_scrapeTabIds"  // storage key

async function trackScrapeTab(tabId: number) {
  const data = await chrome.storage.session.get(SCRAPE_TABS_KEY)
  const map: Record<string, number> = data[SCRAPE_TABS_KEY] || {}
  map[String(tabId)] = Date.now()
  await chrome.storage.session.set({ [SCRAPE_TABS_KEY]: map })
}

async function untrackScrapeTab(tabId: number) {
  const data = await chrome.storage.session.get(SCRAPE_TABS_KEY)
  const map: Record<string, number> = data[SCRAPE_TABS_KEY] || {}
  delete map[String(tabId)]
  await chrome.storage.session.set({ [SCRAPE_TABS_KEY]: map })
}

// Create context menu on install + start scrape-assist polling
chrome.runtime.onInstalled.addListener(() => {
  createContextMenus()
  chrome.alarms.create(SCRAPE_POLL_ALARM, { periodInMinutes: SCRAPE_POLL_SLOW_MINUTES })
})

// Also refresh menus when extension starts + ensure alarm is running
chrome.runtime.onStartup.addListener(() => {
  refreshNotebookMenus()
  chrome.alarms.create(SCRAPE_POLL_ALARM, { periodInMinutes: SCRAPE_POLL_SLOW_MINUTES })
})

async function createContextMenus() {
  // Remove all existing menus first
  await chrome.contextMenus.removeAll()
  
  // Parent menu for page capture
  chrome.contextMenus.create({
    id: "localbook-page-parent",
    title: "📚 Scrape to LocalBook",
    contexts: ["page"]
  })
  
  // Parent menu for selection capture  
  chrome.contextMenus.create({
    id: "localbook-selection-parent",
    title: "📚 Scrape Selection to LocalBook",
    contexts: ["selection"]
  })
  
  // Fetch notebooks and create submenus
  await refreshNotebookMenus()
}

async function refreshNotebookMenus() {
  try {
    const res = await fetch(`${API_BASE}/browser/notebooks`)
    if (res.ok) {
      cachedNotebooks = await res.json()
      
      // Remove old notebook submenus
      for (const nb of cachedNotebooks) {
        try {
          await chrome.contextMenus.remove(`localbook-page-${nb.id}`)
          await chrome.contextMenus.remove(`localbook-selection-${nb.id}`)
        } catch {}
      }
      
      // Create submenu for each notebook (page capture)
      for (const nb of cachedNotebooks) {
        chrome.contextMenus.create({
          id: `localbook-page-${nb.id}`,
          parentId: "localbook-page-parent",
          title: `${nb.name} (${nb.source_count} sources)`,
          contexts: ["page"]
        })
        
        // Create submenu for each notebook (selection capture)
        chrome.contextMenus.create({
          id: `localbook-selection-${nb.id}`,
          parentId: "localbook-selection-parent", 
          title: `${nb.name} (${nb.source_count} sources)`,
          contexts: ["selection"]
        })
      }
      
      // Add separator and option to open popup
      chrome.contextMenus.create({
        id: "localbook-page-separator",
        parentId: "localbook-page-parent",
        type: "separator",
        contexts: ["page"]
      })
      
      chrome.contextMenus.create({
        id: "localbook-page-popup",
        parentId: "localbook-page-parent",
        title: "Open LocalBook Companion...",
        contexts: ["page"]
      })
    }
  } catch (e) {
    console.error("Failed to fetch notebooks for context menu:", e)
  }
}

// Handle context menu clicks
chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const menuId = info.menuItemId as string
  
  // Handle "Open popup" option
  if (menuId === "localbook-page-popup") {
    chrome.action.openPopup()
    return
  }
  
  // Handle page capture with notebook selection
  if (menuId.startsWith("localbook-page-") && tab) {
    const notebookId = menuId.replace("localbook-page-", "")
    if (notebookId && notebookId !== "parent" && notebookId !== "separator") {
      await capturePage(tab, notebookId)
    }
    return
  }
  
  // Handle selection capture with notebook selection
  if (menuId.startsWith("localbook-selection-") && info.selectionText) {
    const notebookId = menuId.replace("localbook-selection-", "")
    if (notebookId && notebookId !== "parent") {
      await captureSelection(info.selectionText, tab?.url || "", tab?.title || "", notebookId)
    }
    return
  }
})

async function getSelectedNotebook(): Promise<string | null> {
  const result = await chrome.storage.local.get("selectedNotebook")
  return result.selectedNotebook || null
}

async function captureSelection(text: string, url: string, title: string, notebookId: string) {
  try {
    const res = await fetch(`${API_BASE}/browser/capture/selection`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url,
        title,
        selected_text: text,
        notebook_id: notebookId
      })
    })
    
    const result = await res.json()
    showNotification(result.success ? "Captured selection!" : "Capture failed")
  } catch (e) {
    showNotification("Failed to connect to LocalBook")
  }
}

async function capturePage(tab: chrome.tabs.Tab, notebookId: string) {
  try {
    // Execute script to get page content
    // Cap HTML size to prevent OOM in service worker (~500KB max)
    const MAX_HTML_CHARS = 500_000
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id! },
      func: (maxChars: number) => ({
        content: document.body.innerText || "",
        html: document.documentElement.outerHTML.substring(0, maxChars)
      }),
      args: [MAX_HTML_CHARS]
    })
    
    const pageData = results[0]?.result
    if (!pageData) throw new Error("Could not get page content")
    
    const res = await fetch(`${API_BASE}/browser/capture`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        url: tab.url,
        title: tab.title,
        content: pageData.content,
        html_content: pageData.html,
        notebook_id: notebookId,
        capture_type: "page"
      })
    })
    
    const result = await res.json()
    if (result.success) {
      showNotification(`Captured! ${result.word_count} words`)
    } else {
      // Show meaningful error to user
      const errorMsg = result.error || "Capture failed"
      console.error("[LocalBook] Capture failed:", errorMsg)
      showNotification(errorMsg.length > 80 ? errorMsg.substring(0, 77) + "..." : errorMsg)
    }
  } catch (e: any) {
    console.error("[LocalBook] Capture error:", e)
    showNotification("Failed to capture page - check if LocalBook is running")
  }
}

async function captureLink(url: string, notebookId: string) {
  try {
    // For now, just fetch the page content server-side
    // This could be enhanced to scrape the link
    showNotification("Link capture coming soon!")
  } catch (e) {
    showNotification("Failed to capture link")
  }
}

function showNotification(message: string) {
  // Use a stable ID so repeated notifications replace instead of stacking
  chrome.notifications.create("localbook-status", {
    type: "basic",
    iconUrl: "icon.png",
    title: "LocalBook",
    message
  })
}

// Handle messages from content scripts
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "captureToLocalBook") {
    // Handle capture request from content script
    getSelectedNotebook().then(notebookId => {
      if (notebookId && request.data.type === "selection") {
        captureSelection(
          request.data.content,
          request.data.url,
          request.data.title,
          notebookId
        )
      }
    })
  }
})


// ═══════════════════════════════════════════════════════════════════
// Extension-Assisted Scrape Queue (Phase 3 fallback)
// ═══════════════════════════════════════════════════════════════════
// The backend queues URLs it couldn't scrape (bot protection, etc.)
// and opens them in the user's default browser.  This poller picks
// up those requests, waits for the tab to load, extracts content
// via the content script, and posts the result back.

// IDs we've already started processing — avoid duplicate work
const processingIds = new Map<string, number>()  // id -> timestamp
const PROCESSING_TTL_MS = 120_000  // 2 minutes — auto-expire stale entries

chrome.alarms.onAlarm.addListener(async (alarm) => {
  if (alarm.name !== SCRAPE_POLL_ALARM) return
  await pollPendingScrapes()
})

async function pollPendingScrapes() {
  // GC stale processingIds to prevent memory leaks
  const now = Date.now()
  for (const [id, ts] of processingIds) {
    if (now - ts > PROCESSING_TTL_MS) processingIds.delete(id)
  }

  // Clean up any scrape tabs left open by a suspended service worker
  await cleanupOrphanedScrapeTabs()

  try {
    // Abort if fetch takes >5s (backend might be hung)
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 5000)
    const res = await fetch(`${API_BASE}/browser/pending-scrapes`, { signal: controller.signal })
    clearTimeout(timeout)

    if (!res.ok) {
      // Backend down — switch to slow polling to save resources
      setScrapeAlarmSpeed(false)
      return
    }
    const data = await res.json()
    const requests: Array<{ id: string; url: string }> = data.requests || []

    if (requests.length === 0) {
      // Nothing pending — slow down polling
      setScrapeAlarmSpeed(false)
      return
    }

    // Something pending — speed up polling
    setScrapeAlarmSpeed(true)

    // Respect concurrency limit
    const activeCount = processingIds.size
    const slotsAvailable = Math.max(0, MAX_CONCURRENT_SCRAPES - activeCount)

    for (const req of requests.slice(0, slotsAvailable)) {
      if (processingIds.has(req.id)) continue
      processingIds.set(req.id, Date.now())
      handleScrapeRequest(req.id, req.url).finally(() => {
        processingIds.delete(req.id)
      })
    }
  } catch {
    // Backend not running or unreachable — slow down polling
    setScrapeAlarmSpeed(false)
  }
}

function setScrapeAlarmSpeed(fast: boolean) {
  if (fast === scrapeAlarmFast) return  // no change needed
  scrapeAlarmFast = fast
  chrome.alarms.create(SCRAPE_POLL_ALARM, {
    periodInMinutes: fast ? SCRAPE_POLL_FAST_MINUTES : SCRAPE_POLL_SLOW_MINUTES
  })
}

async function handleScrapeRequest(requestId: string, url: string) {
  let tabWeOpened: number | null = null
  try {
    // Find tab that already has this URL open (backend opened it via webbrowser.open)
    let tab = await findTabByUrl(url)

    if (!tab) {
      // Open in a background tab
      tab = await chrome.tabs.create({ url, active: false })
      tabWeOpened = tab.id ?? null
      // Track globally so orphan sweep can clean up if service worker suspends/terminates
      if (tabWeOpened) await trackScrapeTab(tabWeOpened)
    }

    if (!tab?.id) {
      console.warn(`[ExtScrape] Could not get tab for ${url}`)
      return
    }

    // Wait for the tab to finish loading (with tab-close safety)
    await waitForTabLoad(tab.id, 25000)

    // Small delay for JS-heavy pages to finish rendering
    await sleep(2500)

    // Extract content via content script message
    // Cap HTML to 500KB to avoid blowing up service worker memory
    const MAX_HTML = 500_000
    let content = ""
    let title = ""
    let html = ""

    try {
      const response = await chrome.tabs.sendMessage(tab.id, { action: "getPageContent" })
      if (response?.content) {
        content = response.content
        title = response.metadata?.title || ""
        html = (response.html || "").substring(0, MAX_HTML)
      }
    } catch {
      // Content script not injected — try scripting API fallback
      try {
        const results = await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          func: (maxHtml: number) => ({
            content: document.body.innerText || "",
            html: document.documentElement.outerHTML.substring(0, maxHtml),
            title: document.title
          }),
          args: [MAX_HTML]
        })
        const result = results[0]?.result
        if (result) {
          content = result.content
          title = result.title || ""
          html = result.html || ""
        }
      } catch (scriptErr) {
        console.error(`[ExtScrape] Scripting fallback failed for ${url}:`, scriptErr)
      }
    }

    // Post result back to backend (with timeout)
    const postController = new AbortController()
    const postTimeout = setTimeout(() => postController.abort(), 10000)
    await fetch(`${API_BASE}/browser/scrape-result/${requestId}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, title, html }),
      signal: postController.signal
    })
    clearTimeout(postTimeout)

    console.log(`[ExtScrape] Submitted result for ${requestId}: ${content.length} chars`)
  } catch (err) {
    console.error(`[ExtScrape] Failed to process ${requestId}:`, err)
  } finally {
    // ALWAYS close tabs we opened — finally{} is more reliable than
    // duplicating cleanup in try + catch (catches service worker suspension edge cases)
    if (tabWeOpened) {
      await untrackScrapeTab(tabWeOpened)
      try { await chrome.tabs.remove(tabWeOpened) } catch {}
    }
  }
}

/** Close any scrape tabs that have been open longer than SCRAPE_TAB_TTL_MS.
 *  Called on every poll tick as a belt-and-suspenders safety net.
 *  Uses chrome.storage.session so it works even after worker termination. */
async function cleanupOrphanedScrapeTabs() {
  const data = await chrome.storage.session.get(SCRAPE_TABS_KEY)
  const map: Record<string, number> = data[SCRAPE_TABS_KEY] || {}
  const now = Date.now()
  let changed = false
  for (const [tabIdStr, openedAt] of Object.entries(map)) {
    if (now - openedAt > SCRAPE_TAB_TTL_MS) {
      const tabId = Number(tabIdStr)
      console.warn(`[ExtScrape] Closing orphaned scrape tab ${tabId} (open ${Math.round((now - openedAt) / 1000)}s)`)
      delete map[tabIdStr]
      changed = true
      try { await chrome.tabs.remove(tabId) } catch {}
    }
  }
  if (changed) await chrome.storage.session.set({ [SCRAPE_TABS_KEY]: map })
}

async function findTabByUrl(url: string): Promise<chrome.tabs.Tab | null> {
  try {
    // Use Chrome's built-in URL filter first (much cheaper than querying all tabs)
    const urlBase = url.split("#")[0].split("?")[0]
    const tabs = await chrome.tabs.query({ url: `${urlBase}*` })
    if (tabs.length > 0) return tabs[0]

    // Fallback: normalize and compare (handles query string differences)
    const allTabs = await chrome.tabs.query({ currentWindow: true })
    const normalize = (u: string) => u.split("#")[0].replace(/\/$/, "").toLowerCase()
    const target = normalize(url)
    for (const tab of allTabs) {
      if (tab.url && normalize(tab.url) === target) return tab
    }
  } catch {}
  return null
}

function waitForTabLoad(tabId: number, timeoutMs: number): Promise<void> {
  return new Promise((resolve) => {
    let settled = false
    const settle = () => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      chrome.tabs.onUpdated.removeListener(onUpdated)
      chrome.tabs.onRemoved.removeListener(onRemoved)
      resolve()
    }

    const timer = setTimeout(settle, timeoutMs)

    function onUpdated(updatedTabId: number, changeInfo: chrome.tabs.TabChangeInfo) {
      if (updatedTabId === tabId && changeInfo.status === "complete") settle()
    }

    function onRemoved(removedTabId: number) {
      if (removedTabId === tabId) settle()  // Tab was closed before loading
    }

    chrome.tabs.onRemoved.addListener(onRemoved)

    // Check if already loaded
    chrome.tabs.get(tabId).then(tab => {
      if (tab.status === "complete") settle()
      else chrome.tabs.onUpdated.addListener(onUpdated)
    }).catch(settle)  // Tab doesn't exist
  })
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}

export {}
