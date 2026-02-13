import type { PlasmoCSConfig } from "plasmo"
import TurndownService from "turndown"

export const config: PlasmoCSConfig = {
  matches: ["<all_urls>"]
}

// Content script for page capture
// This runs on every page and listens for messages from the popup

// Reusable Turndown instance for HTML → Markdown conversion
const turndown = new TurndownService({
  headingStyle: "atx",
  codeBlockStyle: "fenced",
  bulletListMarker: "-"
})

// Filter noise: remove nav, footer, ads, sidebars, cookie banners
turndown.remove(["nav", "footer", "aside", "script", "style", "noscript",
  "iframe", "form", "button"] as any)
turndown.addRule("removeSvg", {
  filter: "svg" as any,
  replacement: () => ""
})
turndown.addRule("removeByClass", {
  filter: (node: HTMLElement) => {
    const cl = (node.className || "").toString().toLowerCase()
    const id = (node.id || "").toLowerCase()
    const noise = ["cookie", "banner", "popup", "modal", "sidebar", "ad-",
      "advertisement", "newsletter", "social-share", "related-posts",
      "comments", "footer", "nav", "menu", "breadcrumb"]
    return noise.some(n => cl.includes(n) || id.includes(n))
  },
  replacement: () => ""
})

function extractMainContent(): string {
  // Try semantic selectors first (like FolioLM's approach)
  const selectors = ["article", "main", '[role="main"]', ".post-content",
    ".article-body", ".entry-content", "#content"]
  for (const sel of selectors) {
    const el = document.querySelector(sel)
    if (el && el.textContent && el.textContent.trim().length > 200) {
      return turndown.turndown(el.innerHTML)
    }
  }
  // Fallback to body
  return turndown.turndown(document.body.innerHTML)
}

function extractOutboundLinks(): Array<{ url: string; text: string; context: string }> {
  const links: Array<{ url: string; text: string; context: string }> = []
  const seen = new Set<string>()
  const hostname = window.location.hostname

  document.querySelectorAll("a[href]").forEach((a: HTMLAnchorElement) => {
    try {
      const href = a.href
      if (!href.startsWith("http") || new URL(href).hostname === hostname) return
      if (seen.has(href)) return
      seen.add(href)

      const text = (a.textContent || "").trim()
      if (!text || text.length < 3) return

      // Grab surrounding sentence for context
      const parent = a.closest("p, li, td, div")
      const context = (parent?.textContent || "").trim().substring(0, 200)

      links.push({ url: href, text, context })
    } catch {}
  })
  return links.slice(0, 30)
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "getPageContent") {
    const markdown = extractMainContent()
    const html = document.documentElement.outerHTML
    const metadata = extractMetadata()
    const outboundLinks = extractOutboundLinks()
    
    sendResponse({
      content: markdown,
      html,
      metadata,
      outboundLinks
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
