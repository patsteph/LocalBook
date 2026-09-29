import type { PageInfo } from "../types"

export function cleanUrl(url: string): string {
  try {
    const parsed = new URL(url)
    const trackingParams = [
      'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
      'fbclid', 'gclid', 'ref', 'source', 'mc_cid', 'mc_eid'
    ]
    trackingParams.forEach(param => parsed.searchParams.delete(param))
    return parsed.toString()
  } catch {
    return url
  }
}

export function extractDomain(url: string): string {
  try {
    return new URL(url).hostname.replace('www.', '')
  } catch {
    return url
  }
}

export interface PageContent {
  content: string
  html: string
  outboundLinks?: Array<{ url: string; text: string; context: string }>
}

export async function getPageContent(): Promise<PageContent | null> {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
    if (!tab?.id) return null

    // Try the content script first — it picks the article element, so both its text
    // and its HTML describe the same region of the page.
    try {
      const response = await chrome.tabs.sendMessage(tab.id, { action: "getPageContent" })
      if (response?.content) {
        return {
          content: response.content,
          html: response.html || "",
          outboundLinks: response.outboundLinks || []
        }
      }
    } catch {
      // Content script not injected on this page — fall back to scripting API
    }

    // Fallback: basic extraction via the scripting API. Same rule as the content
    // script — prefer the article element, and on the rare page whose markup exceeds
    // the cap send NO html rather than a truncated document, because the backend
    // compares the two extractions by length and half a page must not win.
    const MAX_HTML = 2_000_000
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: (maxChars: number, selectors: string[]) => {
        let el: HTMLElement | null = null
        for (const sel of selectors) {
          const found = document.querySelector(sel) as HTMLElement | null
          if (found && (found.textContent || "").trim().length > 200) { el = found; break }
        }
        el = el || document.body
        if (!el) return { content: "", html: "" }
        const html = el.outerHTML
        return {
          content: (el.innerText || el.textContent || "").trim(),
          html: html.length > maxChars ? "" : html
        }
      },
      args: [MAX_HTML, ["article", "main", '[role="main"]', ".post-content",
        ".article-body", ".entry-content", "#content"]]
    })
    return results[0]?.result || null
  } catch (e) {
    console.error("Failed to get page content:", e)
    return null
  }
}

export async function getCurrentPageInfo(): Promise<PageInfo | null> {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
    if (tab?.url && tab?.title) {
      return {
        url: tab.url,
        cleanUrl: cleanUrl(tab.url),
        title: tab.title,
        domain: extractDomain(tab.url)
      }
    }
    return null
  } catch (e) {
    console.error("Failed to get current page:", e)
    return null
  }
}
