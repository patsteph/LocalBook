import { useState } from "react"
import type { TransformResult } from "../types"
import { API_BASE } from "../types"

interface TransformViewProps {
  content: string
  title: string
  onBack: () => void
  onMessage: (msg: string, type: "success" | "error" | "info") => void
}

const TRANSFORMS = [
  { id: "action_items", label: "Action Items", icon: "✅" },
  { id: "executive_brief", label: "Brief", icon: "📋" },
  { id: "timeline", label: "Timeline", icon: "📅" },
  { id: "quiz", label: "Quiz", icon: "🧠" },
  { id: "study_guide", label: "Study Guide", icon: "📖" },
  { id: "outline", label: "Outline", icon: "📝" },
]

export function TransformView({ content, title, onBack, onMessage }: TransformViewProps) {
  const [loading, setLoading] = useState(false)
  const [activeTransform, setActiveTransform] = useState<string | null>(null)
  const [result, setResult] = useState<TransformResult | null>(null)

  async function handleTransform(type: string) {
    setLoading(true)
    setActiveTransform(type)
    setResult(null)

    try {
      const res = await fetch(`${API_BASE}/browser/transform`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: content.substring(0, 12000),
          title,
          transform_type: type
        })
      })

      if (!res.ok) throw new Error(await res.text())

      const data = await res.json()
      if (data.success) {
        setResult({ type, content: data.content, timestamp: Date.now() })
        onMessage("Transform complete!", "success")
      } else {
        throw new Error(data.error || "Transform failed")
      }
    } catch (e: any) {
      onMessage(e.message || "Transform failed", "error")
      setActiveTransform(null)
    } finally {
      setLoading(false)
    }
  }

  function copyResult() {
    if (result?.content) {
      navigator.clipboard.writeText(result.content)
      onMessage("Copied to clipboard!", "success")
    }
  }

  return (
    <div className="flex flex-col">
      <button
        onClick={onBack}
        className="text-xs text-gray-400 hover:text-gray-200 mb-2 flex items-center gap-1"
      >
        ← Back to summary
      </button>

      {/* Result view */}
      {result && (
        <div className="flex-1 overflow-auto">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center gap-2">
              <span>{TRANSFORMS.find(t => t.id === result.type)?.icon}</span>
              <span className="text-sm font-medium text-gray-200">
                {TRANSFORMS.find(t => t.id === result.type)?.label}
              </span>
            </div>
            <div className="flex gap-1">
              <button
                onClick={copyResult}
                className="px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 rounded"
                title="Copy to clipboard"
              >
                📋 Copy
              </button>
              <button
                onClick={() => { setResult(null); setActiveTransform(null) }}
                className="px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 rounded"
              >
                ↻ Try another
              </button>
            </div>
          </div>
          <div className="bg-gray-800 rounded p-3 text-sm text-gray-200 whitespace-pre-wrap">
            {result.content}
          </div>
        </div>
      )}

      {/* Transform picker — compact row */}
      {!result && (
        <>
          <div className="text-xs text-gray-400 mb-2">
            Generate a different view of this content:
          </div>
          <div className="grid grid-cols-3 gap-1.5">
            {TRANSFORMS.map(t => (
              <button
                key={t.id}
                onClick={() => handleTransform(t.id)}
                disabled={loading}
                className={`flex flex-col items-center py-2 px-1 rounded text-center transition-colors ${
                  loading && activeTransform === t.id
                    ? "bg-indigo-900/50 border border-indigo-500"
                    : "bg-gray-800 hover:bg-gray-700 border border-gray-700"
                } ${loading && activeTransform !== t.id ? "opacity-40" : ""}`}
              >
                <span className="text-base">{t.icon}</span>
                <span className="text-[10px] text-gray-300 mt-0.5 leading-tight">{t.label}</span>
              </button>
            ))}
          </div>
          {loading && (
            <div className="text-center text-xs text-indigo-300 mt-3 animate-pulse">
              Generating {TRANSFORMS.find(t => t.id === activeTransform)?.label}...
            </div>
          )}
        </>
      )}
    </div>
  )
}
