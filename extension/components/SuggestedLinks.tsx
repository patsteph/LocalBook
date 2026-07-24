import { useState } from "react"
import type { OutboundLink } from "../types"
import { API_BASE, tokenFetch } from "../types"

interface SuggestedLinksProps {
  links: OutboundLink[]
  pageTitle: string
  notebookIntent: string
  notebookId: string
  /**
   * Optional. When the captured page already has a source_id (set after
   * /browser/capture succeeds), batch expansion routes through the
   * depth+1 endpoint /sources/{notebookId}/{sourceId}/expand-links —
   * results go to the approval queue with parent_source_id stamped on
   * them. When source_id is missing (summary-without-capture flow),
   * batch falls back to per-link /web/quick-add calls.
   */
  sourceId?: string | null
  onMessage: (msg: string, type: "success" | "error" | "info") => void
}

interface Suggestion {
  url: string
  text: string
  reason: string
}

export function SuggestedLinks({ links, pageTitle, notebookIntent, notebookId, sourceId, onMessage }: SuggestedLinksProps) {
  const [suggestions, setSuggestions] = useState<Suggestion[]>([])
  const [loading, setLoading] = useState(false)
  const [analyzed, setAnalyzed] = useState(false)
  const [collapsed, setCollapsed] = useState(true)
  // Selected URLs for batch expansion. The checkbox flow gives the user
  // explicit control over what gets scraped — opt-in per link instead of
  // the prior fire-and-forget "+ Add" buttons. Submitting goes to the
  // depth+1 endpoint (which always queues for approval, never auto-adds).
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [submitting, setSubmitting] = useState(false)
  const [addedUrls, setAddedUrls] = useState<Set<string>>(new Set())

  async function analyzeSuggestions() {
    if (links.length === 0) return
    setLoading(true)
    setCollapsed(false)

    try {
      const res = await tokenFetch(`${API_BASE}/browser/suggest-links`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          links,
          intent: notebookIntent,
          title: pageTitle
        })
      })

      if (res.ok) {
        const data = await res.json()
        setSuggestions(data.suggestions || [])
      }
    } catch {
      /* link suggestions are non-critical — ignore */
    } finally {
      setLoading(false)
      setAnalyzed(true)
    }
  }

  function toggleOne(url: string) {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(url)) next.delete(url)
      else next.add(url)
      return next
    })
  }

  function toggleAll(checked: boolean) {
    if (!checked) {
      setSelected(new Set())
      return
    }
    setSelected(new Set(suggestions.filter(s => !addedUrls.has(s.url)).map(s => s.url)))
  }

  /**
   * Submit the checked URLs for scraping.
   * - With sourceId: one batch POST to /sources/{nb}/{src}/expand-links
   *   (depth+1, robots.txt-respected, results queued for approval).
   * - Without sourceId: fall back to per-link /web/quick-add — same
   *   behaviour as the legacy "+ Add" buttons, just bulk.
   */
  async function scrapeSelected() {
    if (selected.size === 0 || submitting) return
    setSubmitting(true)
    const urls = Array.from(selected)

    try {
      if (sourceId) {
        // Preferred path — batch expansion via the depth+1 service.
        const res = await tokenFetch(`${API_BASE}/sources/${notebookId}/${sourceId}/expand-links`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ selected_urls: urls })
        })
        if (!res.ok) {
          const detail = await res.text()
          throw new Error(detail || `HTTP ${res.status}`)
        }
        const data = await res.json()
        onMessage(
          `Queued ${data.selected_count ?? urls.length} link${urls.length !== 1 ? "s" : ""} — results will appear in the approval queue`,
          "success"
        )
        setAddedUrls(prev => new Set([...prev, ...urls]))
        setSelected(new Set())
      } else {
        // Fallback — per-link quick-add. Runs sequentially so a single
        // failure doesn't kill the whole batch.
        let ok = 0
        let fail = 0
        for (const url of urls) {
          const text = suggestions.find(s => s.url === url)?.text || url
          try {
            const res = await tokenFetch(`${API_BASE}/web/quick-add`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ notebook_id: notebookId, url, title: text })
            })
            if (res.ok) {
              ok++
              setAddedUrls(prev => new Set([...prev, url]))
            } else {
              fail++
            }
          } catch {
            fail++
          }
        }
        onMessage(
          fail === 0
            ? `Added ${ok} link${ok !== 1 ? "s" : ""}`
            : `Added ${ok}, ${fail} failed`,
          fail === 0 ? "success" : "info"
        )
        setSelected(new Set())
      }
    } catch (e: any) {
      onMessage(e?.message || "Failed to scrape selected", "error")
    } finally {
      setSubmitting(false)
    }
  }

  if (links.length === 0) return null

  // Compact toggle before analysis — same affordance as before, just
  // unchanged so the user reaches the suggestion list via one click.
  if (!analyzed) {
    return (
      <button
        onClick={analyzeSuggestions}
        disabled={loading}
        className="w-full mt-2 px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded text-xs text-gray-400 flex items-center gap-1.5"
      >
        <span>🔗</span>
        {loading ? "Analyzing links..." : `${links.length} outbound links — find related sources`}
      </button>
    )
  }

  if (suggestions.length === 0) return null

  const eligible = suggestions.filter(s => !addedUrls.has(s.url))
  const allEligibleSelected = eligible.length > 0 && eligible.every(s => selected.has(s.url))

  return (
    <div className="mt-2 border border-gray-700 rounded overflow-hidden">
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="w-full px-3 py-1.5 bg-gray-800 text-xs text-gray-400 flex items-center justify-between"
      >
        <span className="flex items-center gap-1.5">
          <span>🔗</span> {suggestions.length} suggested sources
        </span>
        <span>{collapsed ? "▼" : "▲"}</span>
      </button>
      {!collapsed && (
        <>
          {/* Header: select-all + scrape-selected. Only renders when
              there's at least one suggestion left to act on. */}
          {eligible.length > 0 && (
            <div className="px-3 py-1.5 bg-gray-900/60 border-b border-gray-700 flex items-center justify-between text-[10px]">
              <label className="flex items-center gap-1.5 text-gray-400 cursor-pointer">
                <input
                  type="checkbox"
                  checked={allEligibleSelected}
                  onChange={e => toggleAll(e.target.checked)}
                />
                Select all ({eligible.length})
              </label>
              <button
                onClick={scrapeSelected}
                disabled={selected.size === 0 || submitting}
                className={`px-2 py-0.5 rounded text-[10px] font-medium transition-colors ${
                  selected.size === 0 || submitting
                    ? "bg-gray-700 text-gray-500 cursor-not-allowed"
                    : "bg-blue-600 hover:bg-blue-700 text-white"
                }`}
              >
                {submitting ? "…" : `Scrape selected (${selected.size})`}
              </button>
            </div>
          )}
          <div className="divide-y divide-gray-700">
            {suggestions.map((s, i) => {
              const isAdded = addedUrls.has(s.url)
              const isChecked = selected.has(s.url)
              return (
                <div
                  key={i}
                  className={`px-3 py-2 flex items-start gap-2 ${
                    isAdded ? "bg-gray-900/40 opacity-50" : isChecked ? "bg-blue-900/20" : "bg-gray-800/50"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={isChecked}
                    disabled={isAdded || submitting}
                    onChange={() => toggleOne(s.url)}
                    className="mt-0.5 shrink-0"
                  />
                  <div className="flex-1 min-w-0">
                    <a
                      href={s.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs text-blue-400 hover:text-blue-300 line-clamp-1"
                    >
                      {s.text || s.url}
                    </a>
                    <p className="text-[10px] text-gray-500 mt-0.5 line-clamp-1">{s.reason}</p>
                  </div>
                  {isAdded && (
                    <span className="shrink-0 text-[10px] text-gray-500">queued</span>
                  )}
                </div>
              )
            })}
          </div>
        </>
      )}
    </div>
  )
}
