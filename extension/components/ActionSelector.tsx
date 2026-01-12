import type { ActionType } from "../types"

interface ActionSelectorProps {
  loading: boolean
  onAction: (action: ActionType) => void
}

export function ActionSelector({ loading, onAction }: ActionSelectorProps) {
  return (
    <div className="p-3 border-b border-gray-700">
      <label className="text-xs text-gray-400 block mb-2">Action:</label>
      <select
        className="w-full p-2 bg-gray-800 border border-gray-600 rounded text-sm"
        defaultValue=""
        onChange={(e) => {
          const action = e.target.value as ActionType
          if (!action) return
          onAction(action)
          e.target.value = ""
        }}
        disabled={loading}
      >
        <option value="">Select an action...</option>
        <option value="summary">📝 Summarize Page</option>
        <option value="scrape">📄 Scrape to Notebook</option>
        <option value="links">🔗 Extract Links</option>
        <option value="compare">⚖️ Compare with Notebook</option>
      </select>
    </div>
  )
}
