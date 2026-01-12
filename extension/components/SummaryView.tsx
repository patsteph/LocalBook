import type { SummaryResult } from "../types"

interface SummaryViewProps {
  summaryResult: SummaryResult
  onStartChat: () => void
  onResearch: () => void
}

export function SummaryView({ summaryResult, onStartChat, onResearch }: SummaryViewProps) {
  return (
    <div className="space-y-4">
      {/* Key Points */}
      {summaryResult.key_points.length > 0 && (
        <div>
          <h3 className="font-bold text-sm text-gray-300 mb-2">📌 Key Points</h3>
          <ul className="space-y-1.5">
            {summaryResult.key_points.map((point, i) => (
              <li key={i} className="text-sm text-gray-200 flex gap-2">
                <span className="text-purple-400 shrink-0">•</span>
                <span>{point}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Summary Paragraphs */}
      {summaryResult.summary && (
        <div>
          <h3 className="font-bold text-sm text-gray-300 mb-2">📝 Summary</h3>
          <div className="text-sm text-gray-200 whitespace-pre-wrap leading-relaxed">
            {summaryResult.summary}
          </div>
        </div>
      )}

      {/* Key Concepts */}
      {summaryResult.key_concepts.length > 0 && (
        <div>
          <h4 className="text-xs text-gray-400 mb-2">🏷️ Key Concepts</h4>
          <div className="flex flex-wrap gap-1.5">
            {summaryResult.key_concepts.map((concept, i) => (
              <span key={i} className="px-2 py-1 bg-purple-900/40 text-purple-300 border border-purple-700/50 rounded text-xs">
                {concept}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Side-by-side action buttons */}
      <div className="flex gap-2 mt-3 pt-2 border-t border-gray-700">
        <button
          onClick={onStartChat}
          className="flex-1 p-2 bg-indigo-600 hover:bg-indigo-700 rounded text-sm font-medium flex items-center justify-center gap-1"
        >
          💬 Interact
        </button>
        <button
          onClick={onResearch}
          className="flex-1 p-2 bg-emerald-600 hover:bg-emerald-700 rounded text-sm font-medium flex items-center justify-center gap-1"
        >
          🔍 Research
        </button>
      </div>
    </div>
  )
}
