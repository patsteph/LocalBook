import { useState } from "react"
import { API_BASE } from "../types"

interface ImportMenuProps {
  notebookId: string
  onMessage: (msg: string, type: "success" | "error" | "info") => void
  onRefresh: () => void
}

interface ImportItem {
  url: string
  title: string
  selected: boolean
}

export function ImportMenu({ notebookId, onMessage, onRefresh }: ImportMenuProps) {
  const [open, setOpen] = useState(false)
  const [source, setSource] = useState<"tabs" | "bookmarks" | "history" | null>(null)
  const [items, setItems] = useState<ImportItem[]>([])
  const [loading, setLoading] = useState(false)

  async function loadTabs() {
    setSource("tabs")
    const tabs = await chrome.tabs.query({})
    setItems(
      tabs
        .filter(t => t.url?.startsWith("http") && t.id)
        .map(t => ({ url: t.url!, title: t.title || t.url!, selected: false }))
    )
  }

  async function loadBookmarks() {
    setSource("bookmarks")
    try {
      const tree = await chrome.bookmarks.getRecent(30)
      setItems(
        tree
          .filter(b => b.url?.startsWith("http"))
          .map(b => ({ url: b.url!, title: b.title || b.url!, selected: false }))
      )
    } catch {
      onMessage("Bookmark access not available", "error")
    }
  }

  async function loadHistory() {
    setSource("history")
    try {
      const results = await chrome.history.search({ text: "", maxResults: 30, startTime: Date.now() - 7 * 86400000 })
      setItems(
        results
          .filter(h => h.url?.startsWith("http"))
          .map(h => ({ url: h.url!, title: h.title || h.url!, selected: false }))
      )
    } catch {
      onMessage("History access not available", "error")
    }
  }

  function toggleItem(idx: number) {
    setItems(prev => prev.map((item, i) => i === idx ? { ...item, selected: !item.selected } : item))
  }

  function selectAll() {
    const allSelected = items.every(i => i.selected)
    setItems(prev => prev.map(i => ({ ...i, selected: !allSelected })))
  }

  async function importSelected() {
    const selected = items.filter(i => i.selected)
    if (selected.length === 0) {
      onMessage("Select at least one item", "info")
      return
    }

    setLoading(true)
    let added = 0
    for (const item of selected) {
      try {
        const res = await fetch(`${API_BASE}/web/quick-add`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ notebook_id: notebookId, url: item.url, title: item.title })
        })
        if (res.ok) added++
      } catch { /* skip failed */ }
    }

    setLoading(false)
    onMessage(`Added ${added} of ${selected.length} sources`, added > 0 ? "success" : "error")
    if (added > 0) onRefresh()
    setOpen(false)
    setSource(null)
    setItems([])
  }

  function close() {
    setOpen(false)
    setSource(null)
    setItems([])
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="w-6 h-6 flex items-center justify-center bg-gray-700 hover:bg-gray-600 rounded text-xs text-gray-300 shrink-0"
        title="Import sources"
      >
        +
      </button>
    )
  }

  return (
    <div className="absolute z-50 top-10 right-2 bg-gray-800 border border-gray-600 rounded-lg shadow-xl w-72 max-h-80 overflow-hidden flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-gray-700">
        <span className="text-xs font-medium text-gray-200">Import Sources</span>
        <button onClick={close} className="text-gray-500 hover:text-gray-300 text-xs">✕</button>
      </div>

      {/* Source picker */}
      {!source && (
        <div className="p-2 space-y-1">
          <button onClick={loadTabs} className="w-full text-left px-3 py-2 text-sm hover:bg-gray-700 rounded flex items-center gap-2">
            <span>🗂️</span> Open Tabs
          </button>
          <button onClick={loadBookmarks} className="w-full text-left px-3 py-2 text-sm hover:bg-gray-700 rounded flex items-center gap-2">
            <span>⭐</span> Recent Bookmarks
          </button>
          <button onClick={loadHistory} className="w-full text-left px-3 py-2 text-sm hover:bg-gray-700 rounded flex items-center gap-2">
            <span>🕐</span> Browser History (7d)
          </button>
        </div>
      )}

      {/* Item list */}
      {source && (
        <>
          <div className="flex items-center justify-between px-3 py-1.5 border-b border-gray-700">
            <button onClick={() => { setSource(null); setItems([]) }} className="text-xs text-gray-400 hover:text-gray-200">
              ← Back
            </button>
            <button onClick={selectAll} className="text-xs text-blue-400 hover:text-blue-300">
              {items.every(i => i.selected) ? "Deselect All" : "Select All"}
            </button>
          </div>
          <div className="flex-1 overflow-auto max-h-48">
            {items.length === 0 && (
              <div className="text-center text-gray-500 text-xs py-4">No items found</div>
            )}
            {items.map((item, i) => (
              <label key={i} className="flex items-start gap-2 px-3 py-1.5 hover:bg-gray-700/50 cursor-pointer">
                <input
                  type="checkbox"
                  checked={item.selected}
                  onChange={() => toggleItem(i)}
                  className="mt-0.5 shrink-0"
                />
                <div className="min-w-0">
                  <div className="text-xs text-gray-200 truncate">{item.title}</div>
                  <div className="text-[10px] text-gray-500 truncate">{item.url}</div>
                </div>
              </label>
            ))}
          </div>
          <div className="px-3 py-2 border-t border-gray-700">
            <button
              onClick={importSelected}
              disabled={loading || items.filter(i => i.selected).length === 0}
              className="w-full py-1.5 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 disabled:text-gray-500 rounded text-xs font-medium"
            >
              {loading ? "Importing..." : `Import ${items.filter(i => i.selected).length} selected`}
            </button>
          </div>
        </>
      )}
    </div>
  )
}
