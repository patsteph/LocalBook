import { useState } from "react"
import type { OutboundLink } from "../types"
import { API_BASE } from "../types"

interface SuggestedLinksProps {
  links: OutboundLink[]
  pageTitle: string
  notebookIntent: string
  notebookId: string
  onMessage: (msg: string, type: "success" | "error" | "info") => void
}

interface Suggestion {
  url: string
  text: string
  reason: string
}

export function SuggestedLinks({ links, pageTitle, notebookIntent, notebookId, onMessage }: SuggestedLinksProps) {
  const [suggestions, setSuggestions] = useState<Suggestion[]>([])
  const [loading, setLoading] = useState(false)
  const [analyzed, setAnalyzed] = useState(false)
  const [collapsed, setCollapsed] = useState(true)

  async function analyzeSuggestions() {
    if (links.length === 0) return
    setLoading(true)
    setCollapsed(false)

    try {
      const res = await fetch(`${API_BASE}/browser/suggest-links`, {
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
    } catch (e) {
      console.log("Link suggestions failed:", e)
    } finally {
      setLoading(false)
      setAnalyzed(true)
    }
  }

  async function quickAdd(url: string, title: string) {
    try {
      const res = await fetch(`${API_BASE}/web/quick-add`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ notebook_id: notebookId, url, title })
      })
      if (res.ok) {
        onMessage(`Added "${title}"`, "success")
        setSuggestions(prev => prev.filter(s => s.url !== url))
      }
    } catch {
      onMessage("Failed to add", "error")
    }
  }

  if (links.length === 0) return null

  // Compact toggle before analysis
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
        <div className="divide-y divide-gray-700">
          {suggestions.map((s, i) => (
            <div key={i} className="px-3 py-2 bg-gray-800/50 flex items-start justify-between gap-2">
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
              <button
                onClick={() => quickAdd(s.url, s.text)}
                className="shrink-0 px-2 py-0.5 bg-blue-600 hover:bg-blue-700 rounded text-[10px]"
              >
                + Add
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
