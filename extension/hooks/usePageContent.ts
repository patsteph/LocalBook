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

export async function getPageContent(): Promise<{ content: string; html: string } | null> {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
    if (!tab?.id) return null

    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => ({
        content: document.body.innerText,
        html: document.documentElement.outerHTML
      })
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
