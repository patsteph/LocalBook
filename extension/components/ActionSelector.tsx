import { useState } from "react"
import type { ActionType } from "../types"

interface ActionSelectorProps {
  loading: boolean
  onAction: (action: ActionType) => void
}

export function ActionSelector({ loading, onAction }: ActionSelectorProps) {
  const [showMore, setShowMore] = useState(false)

  return (
    <div className="px-3 py-2 border-b border-gray-700">
      {/* Primary actions — always visible */}
      <div className="flex gap-1.5">
        <button
          onClick={() => onAction("summary")}
          disabled={loading}
          className="flex-1 flex flex-col items-center gap-0.5 py-2 px-1 bg-gray-800 hover:bg-indigo-900/40 border border-gray-700 hover:border-indigo-500/40 rounded transition-colors disabled:opacity-40"
        >
          <span className="text-base">📝</span>
          <span className="text-[10px] text-gray-300">Summarize</span>
        </button>
        <button
          onClick={() => onAction("scrape")}
          disabled={loading}
          className="flex-1 flex flex-col items-center gap-0.5 py-2 px-1 bg-gray-800 hover:bg-emerald-900/40 border border-gray-700 hover:border-emerald-500/40 rounded transition-colors disabled:opacity-40"
        >
          <span className="text-base">📄</span>
          <span className="text-[10px] text-gray-300">Capture</span>
        </button>
        <button
          onClick={() => onAction("chat")}
          disabled={loading}
          className="flex-1 flex flex-col items-center gap-0.5 py-2 px-1 bg-gray-800 hover:bg-blue-900/40 border border-gray-700 hover:border-blue-500/40 rounded transition-colors disabled:opacity-40"
        >
          <span className="text-base">💬</span>
          <span className="text-[10px] text-gray-300">Chat</span>
        </button>

        {/* More actions toggle */}
        <button
          onClick={() => setShowMore(!showMore)}
          className={`w-9 flex flex-col items-center justify-center gap-0.5 py-2 rounded border transition-colors ${
            showMore
              ? "bg-gray-700 border-gray-500"
              : "bg-gray-800 hover:bg-gray-700 border-gray-700"
          }`}
        >
          <span className="text-base">⋯</span>
        </button>
      </div>

      {/* Secondary actions — expandable */}
      {showMore && (
        <div className="flex gap-1.5 mt-1.5">
          <button
            onClick={() => { onAction("links"); setShowMore(false) }}
            disabled={loading}
            className="flex-1 flex items-center justify-center gap-1 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded text-[10px] text-gray-400 transition-colors disabled:opacity-40"
          >
            🔗 Links
          </button>
          <button
            onClick={() => { onAction("compare"); setShowMore(false) }}
            disabled={loading}
            className="flex-1 flex items-center justify-center gap-1 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded text-[10px] text-gray-400 transition-colors disabled:opacity-40"
          >
            ⚖️ Compare
          </button>
          <button
            onClick={() => { onAction("automate"); setShowMore(false) }}
            disabled={loading}
            className="flex-1 flex items-center justify-center gap-1 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded text-[10px] text-gray-400 transition-colors disabled:opacity-40"
          >
            🤖 Automate
          </button>
        </div>
      )}
    </div>
  )
}
