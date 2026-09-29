import type { PlasmoCSConfig } from "plasmo"

export const config: PlasmoCSConfig = {
  matches: ["<all_urls>"]
}

// Content script for page capture
// This runs on every page and listens for messages from the popup

// Semantic containers, best first. Shared by the text and the HTML extraction so the
// two always describe the SAME element — they used to be chosen independently.
const ARTICLE_SELECTORS = ["article", "main", '[role="main"]', ".post-content",
  ".article-body", ".entry-content", "#content"]

// Upper bound on the HTML we hand the backend. This is the ARTICLE element's markup,
// not the whole document, so on a real page it is 5-10× smaller than what we used to
// send and the cap effectively never binds. When it does bind we send NO html rather
// than a truncated document: the backend prefers whichever extraction is longer, and a
// half-page of HTML used to be able to win against the full text we already had.
const MAX_HTML = 2_000_000

// Outbound-link extraction is bounded at the LOOP, not by slicing a full list at the
// end — every link we look at costs a `closest()` walk and a `textContent` read.
const MAX_OUTBOUND_LINKS = 30
const LINK_CONTEXT_CHARS = 200

function findArticleElement(): HTMLElement | null {
  for (const sel of ARTICLE_SELECTORS) {
    const el = document.querySelector(sel) as HTMLElement | null
    if (el && el.textContent && el.textContent.trim().length > 200) return el
  }
  return null
}

/** Text + markup for the capture request.
 *
 * This used to convert the whole article to Markdown with Turndown, including a rule
 * that ran a 15-entry substring scan over `className` and `id` for EVERY node in the
 * document — the single most expensive thing the extension did, on the page's own main
 * thread. The backend then re-extracted the same page with trafilatura and threw the
 * Markdown away. So: send the text cheaply, send the markup, and let the one extractor
 * that actually feeds the index do the work.
 */
function extractForCapture(): { content: string; html: string } {
  const el = findArticleElement() || document.body
  if (!el) return { content: "", html: "" }

  // innerText (layout-aware, so it drops hidden nav/menus) with textContent as the
  // backstop for detached or display:none containers.
  const content = (el.innerText || el.textContent || "").trim()
  const html = el.outerHTML
  return { content, html: html.length > MAX_HTML ? "" : html }
}

function extractOutboundLinks(): Array<{ url: string; text: string; context: string }> {
  const links: Array<{ url: string; text: string; context: string }> = []
  const seen = new Set<string>()
  const hostname = window.location.hostname

  const anchors = document.querySelectorAll("a[href]")
  for (let i = 0; i < anchors.length; i++) {
    if (links.length >= MAX_OUTBOUND_LINKS) break
    const a = anchors[i] as HTMLAnchorElement
    try {
      const href = a.href
      if (!href.startsWith("http") || new URL(href).hostname === hostname) continue
      if (seen.has(href)) continue
      seen.add(href)

      const text = (a.textContent || "").trim()
      if (!text || text.length < 3) continue

      // Grab surrounding sentence for context
      const parent = a.closest("p, li, td, div")
      const context = (parent?.textContent || "").trim().substring(0, LINK_CONTEXT_CHARS)

      links.push({ url: href, text, context })
    } catch {}
  }
  return links
}

// Single unified message listener — avoids duplicate listener registration
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  switch (request.action) {
    case "getPageContent": {
      const { content, html } = extractForCapture()
      const metadata = extractMetadata()
      const outboundLinks = extractOutboundLinks()
      sendResponse({ content, html, metadata, outboundLinks })
      return true
    }

    case "getSelection": {
      const selection = window.getSelection()?.toString() || ""
      sendResponse({ selection })
      return true
    }

    case "captureSelection": {
      const selection = window.getSelection()?.toString() || ""
      if (selection) {
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
      return false  // No async response needed
    }

    default:
      return false  // Not our message — don't hold the channel open
  }
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

export {}
