import { useState } from "react"
import type { LinkInfo } from "../types"
import { API_BASE, tokenFetch } from "../types"
import { extractDomain } from "../hooks/usePageContent"

interface ScrapeResultProps {
  result: string
}

export function ScrapeResult({ result }: ScrapeResultProps) {
  return (
    <div className="p-3 bg-green-900/30 rounded">
      <pre className="text-sm text-green-300 whitespace-pre-wrap">{result}</pre>
    </div>
  )
}

interface LinksResultProps {
  linksResult: LinkInfo
  notebookId: string | null
  // Set after /browser/capture succeeds. When present, batch scraping
  // routes through /sources/{nb}/{src}/expand-links (depth+1, queued
  // for approval). When absent, falls back to per-link /web/quick-add.
  sourceId?: string | null
  onMessage: (msg: string, type: "success" | "error" | "info") => void
}

export function LinksResult({ linksResult, notebookId, sourceId, onMessage }: LinksResultProps) {
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [submitting, setSubmitting] = useState(false)
  const [addedUrls, setAddedUrls] = useState<Set<string>>(new Set())

  const links = linksResult.outgoing
  const eligible = links.filter(l => !addedUrls.has(l))
  const allEligibleSelected = eligible.length > 0 && eligible.every(l => selected.has(l))

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
    setSelected(new Set(eligible))
  }

  async function scrapeSelected() {
    if (selected.size === 0 || submitting || !notebookId) return
    setSubmitting(true)
    const urls = Array.from(selected)

    try {
      if (sourceId) {
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
        let ok = 0
        let fail = 0
        for (const url of urls) {
          try {
            const res = await tokenFetch(`${API_BASE}/web/quick-add`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ notebook_id: notebookId, url, title: url })
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
          fail === 0 ? `Added ${ok} link${ok !== 1 ? "s" : ""}` : `Added ${ok}, ${fail} failed`,
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

  return (
    <div className="space-y-2">
      <h3 className="font-bold text-sm text-gray-300">
        Outgoing Links ({links.length})
      </h3>
      {eligible.length > 0 && notebookId && (
        <div className="px-2 py-1.5 bg-gray-900/60 border border-gray-700 rounded flex items-center justify-between text-[10px]">
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
      <div className="space-y-1 max-h-80 overflow-auto">
        {links.map((link, i) => {
          const isAdded = addedUrls.has(link)
          const isChecked = selected.has(link)
          return (
            <div
              key={i}
              className={`px-2 py-1 flex items-start gap-2 rounded ${
                isAdded ? "opacity-50" : isChecked ? "bg-blue-900/20" : ""
              }`}
            >
              {notebookId && (
                <input
                  type="checkbox"
                  checked={isChecked}
                  disabled={isAdded || submitting}
                  onChange={() => toggleOne(link)}
                  className="mt-1 shrink-0"
                />
              )}
              <a
                href={link}
                target="_blank"
                rel="noopener noreferrer"
                className="flex-1 min-w-0 text-xs text-blue-400 hover:text-blue-300 truncate"
              >
                {extractDomain(link)}: {link}
              </a>
              {isAdded && (
                <span className="shrink-0 text-[10px] text-gray-500">queued</span>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

interface CompareResultProps {
  result: string
}

export function CompareResult({ result }: CompareResultProps) {
  return (
    <div className="space-y-3">
      <h3 className="font-bold text-sm text-gray-300">Notebook Comparison</h3>
      <p className="text-sm text-gray-200 whitespace-pre-wrap">
        {result}
      </p>
    </div>
  )
}
