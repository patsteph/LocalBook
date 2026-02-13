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

// Create context menu on install
chrome.runtime.onInstalled.addListener(() => {
  createContextMenus()
})

// Also refresh menus when extension starts
chrome.runtime.onStartup.addListener(() => {
  refreshNotebookMenus()
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
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id! },
      func: () => ({
        content: document.body.innerText || "",
        html: document.documentElement.outerHTML
      })
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
  chrome.notifications.create({
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

export {}
