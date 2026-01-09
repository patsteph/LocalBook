import type { PlasmoCSConfig } from "plasmo"

export const config: PlasmoCSConfig = {
  matches: ["<all_urls>"]
}

// Content script for page capture
// This runs on every page and listens for messages from the popup

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "getPageContent") {
    const content = document.body.innerText || ""
    const html = document.documentElement.outerHTML
    
    // Extract metadata
    const metadata = extractMetadata()
    
    sendResponse({
      content,
      html,
      metadata
    })
  }
  
  if (request.action === "getSelection") {
    const selection = window.getSelection()?.toString() || ""
    sendResponse({ selection })
  }
  
  return true // Keep message channel open for async response
})

function extractMetadata() {
  const getMeta = (name: string) => {
    const el = document.querySelector(`meta[name="${name}"], meta[property="${name}"]`)
    return el?.getAttribute("content") || ""
  }
  
  return {
    title: document.title,
    description: getMeta("description") || getMeta("og:description"),
    author: getMeta("author"),
    publishDate: getMeta("article:published_time") || getMeta("datePublished"),
    ogImage: getMeta("og:image"),
    keywords: getMeta("keywords").split(",").map(k => k.trim()).filter(Boolean)
  }
}

// Listen for context menu clicks (for "Add to LocalBook" option)
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "captureSelection") {
    const selection = window.getSelection()?.toString() || ""
    if (selection) {
      // Send selection to background script for capture
      chrome.runtime.sendMessage({
        action: "captureToLocalBook",
        data: {
          type: "selection",
          content: selection,
          url: window.location.href,
          title: document.title
        }
      })
    }
  }
})

export {}
