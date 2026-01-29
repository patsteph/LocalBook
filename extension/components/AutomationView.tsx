import { useState } from "react"
import { API_BASE } from "../types"

interface AutomationViewProps {
  pageUrl: string
  pageHtml?: string
  onMessage: (msg: string, type: "success" | "error" | "info") => void
}

interface ActionStep {
  action: string
  target?: string
  value?: string
  reasoning: string
}

interface ActionPlan {
  goal: string
  steps: ActionStep[]
  estimated_time: string
}

export function AutomationView({ pageUrl, pageHtml, onMessage }: AutomationViewProps) {
  const [goal, setGoal] = useState("")
  const [loading, setLoading] = useState(false)
  const [plan, setPlan] = useState<ActionPlan | null>(null)
  const [currentStep, setCurrentStep] = useState(-1)
  const [elementDescription, setElementDescription] = useState("")
  const [foundElement, setFoundElement] = useState<any>(null)

  const planActions = async () => {
    if (!goal.trim()) return
    
    setLoading(true)
    setPlan(null)
    
    try {
      const response = await fetch(`${API_BASE}/agent-browser/plan-actions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          goal: goal,
          page_url: pageUrl,
          page_html: pageHtml?.substring(0, 50000)
        })
      })
      
      if (!response.ok) throw new Error("Failed to plan actions")
      
      const data = await response.json()
      setPlan(data)
      setCurrentStep(0)
      onMessage("Action plan created", "success")
    } catch (err) {
      onMessage(`Planning failed: ${err}`, "error")
    } finally {
      setLoading(false)
    }
  }

  const findElement = async () => {
    if (!elementDescription.trim()) return
    
    setLoading(true)
    setFoundElement(null)
    
    try {
      const response = await fetch(`${API_BASE}/agent-browser/find-element`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          description: elementDescription,
          page_url: pageUrl,
          page_html: pageHtml?.substring(0, 50000)
        })
      })
      
      if (!response.ok) throw new Error("Failed to find element")
      
      const data = await response.json()
      setFoundElement(data)
      onMessage(`Found: ${data.element_type} - ${data.selector}`, "success")
    } catch (err) {
      onMessage(`Element search failed: ${err}`, "error")
    } finally {
      setLoading(false)
    }
  }

  const executeStep = async (step: ActionStep) => {
    try {
      const response = await fetch(`${API_BASE}/agent-browser/prepare-action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: step.action,
          target_description: step.target,
          value: step.value,
          page_url: pageUrl,
          page_html: pageHtml?.substring(0, 50000)
        })
      })
      
      if (!response.ok) throw new Error("Failed to prepare action")
      
      const data = await response.json()
      
      // Execute in page context
      if (data.script) {
        chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
          if (tabs[0]?.id) {
            chrome.scripting.executeScript({
              target: { tabId: tabs[0].id },
              func: (script: string) => {
                try {
                  eval(script)
                  return { success: true }
                } catch (e) {
                  return { success: false, error: String(e) }
                }
              },
              args: [data.script]
            })
          }
        })
      }
      
      onMessage(`Executed: ${step.action}`, "success")
      if (plan && currentStep < plan.steps.length - 1) {
        setCurrentStep(currentStep + 1)
      }
    } catch (err) {
      onMessage(`Action failed: ${err}`, "error")
    }
  }

  return (
    <div className="p-3 space-y-4">
      <div className="text-sm text-gray-400 mb-2">
        🤖 AI Browser Automation
      </div>

      {/* Goal Input */}
      <div className="space-y-2">
        <label className="text-xs text-gray-400">What do you want to do?</label>
        <textarea
          className="w-full p-2 bg-gray-800 border border-gray-600 rounded text-sm resize-none"
          rows={2}
          placeholder="e.g., Fill out the contact form with my info..."
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          disabled={loading}
        />
        <button
          className="w-full py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium disabled:opacity-50"
          onClick={planActions}
          disabled={loading || !goal.trim()}
        >
          {loading ? "Planning..." : "🎯 Plan Actions"}
        </button>
      </div>

      {/* Action Plan */}
      {plan && (
        <div className="bg-gray-800 rounded p-3 space-y-2">
          <div className="text-xs text-gray-400">Action Plan ({plan.estimated_time})</div>
          <div className="space-y-2">
            {plan.steps.map((step, i) => (
              <div
                key={i}
                className={`p-2 rounded text-sm ${
                  i === currentStep
                    ? "bg-blue-900 border border-blue-500"
                    : i < currentStep
                    ? "bg-green-900/30 border border-green-700"
                    : "bg-gray-700"
                }`}
              >
                <div className="flex items-center gap-2">
                  <span className="text-xs font-mono bg-gray-600 px-1 rounded">
                    {i + 1}
                  </span>
                  <span className="font-medium">{step.action}</span>
                  {step.target && (
                    <span className="text-gray-400">→ {step.target}</span>
                  )}
                </div>
                {i === currentStep && (
                  <button
                    className="mt-2 px-3 py-1 bg-green-600 hover:bg-green-700 rounded text-xs"
                    onClick={() => executeStep(step)}
                  >
                    ▶ Execute
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Divider */}
      <div className="border-t border-gray-700 my-3" />

      {/* Quick Element Finder */}
      <div className="space-y-2">
        <label className="text-xs text-gray-400">Find Element (Natural Language)</label>
        <div className="flex gap-2">
          <input
            className="flex-1 p-2 bg-gray-800 border border-gray-600 rounded text-sm"
            placeholder="e.g., the blue submit button"
            value={elementDescription}
            onChange={(e) => setElementDescription(e.target.value)}
            disabled={loading}
          />
          <button
            className="px-3 py-2 bg-purple-600 hover:bg-purple-700 rounded text-sm disabled:opacity-50"
            onClick={findElement}
            disabled={loading || !elementDescription.trim()}
          >
            🔍
          </button>
        </div>
      </div>

      {/* Found Element */}
      {foundElement && (
        <div className="bg-gray-800 rounded p-3 text-sm">
          <div className="text-xs text-gray-400 mb-1">Found Element:</div>
          <div className="font-mono text-xs bg-gray-900 p-2 rounded overflow-x-auto">
            {foundElement.selector}
          </div>
          <div className="text-xs text-gray-400 mt-2">
            Type: {foundElement.element_type} | 
            Confidence: {(foundElement.confidence * 100).toFixed(0)}%
          </div>
        </div>
      )}
    </div>
  )
}
