import type { SummaryResult } from "../types"

interface SummaryViewProps {
  summaryResult: SummaryResult
  onTransform: () => void
}

export function SummaryView({ summaryResult, onTransform }: SummaryViewProps) {
  return (
    <div className="space-y-3">
      {/* Key Points */}
      {summaryResult.key_points.length > 0 && (
        <div>
          <h3 className="font-bold text-sm text-gray-300 mb-2">📌 Key Points</h3>
          <ul className="space-y-1.5">
            {summaryResult.key_points.map((point, i) => (
              <li key={i} className="text-sm text-gray-200 flex gap-2">
                <span className="text-purple-400 shrink-0">•</span>
                <span>{typeof point === "string" ? point : JSON.stringify(point)}</span>
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
                {typeof concept === "string" ? concept : JSON.stringify(concept)}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Transform link — subtle, not a giant banner */}
      <button
        onClick={onTransform}
        className="w-full mt-2 py-2 text-xs text-indigo-400 hover:text-indigo-300 bg-indigo-900/10 hover:bg-indigo-900/25 border border-indigo-500/20 rounded transition-colors"
      >
        ✨ Explore Deeper — Action Items, Quiz, Timeline, Brief...
      </button>
    </div>
  )
}
