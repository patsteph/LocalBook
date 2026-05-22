import { useState } from "react"
import { API_BASE, tokenFetch } from "../types"

interface AutomationViewProps {
  pageUrl: string
  pageHtml?: string
  onMessage: (msg: string, type: "success" | "error" | "info") => void
}

// ----------------------------------------------------------------------------
// Structured action interpreter — runs an action_payload from the backend in
// the active tab via chrome.scripting.executeScript. Replaces the previous
// eval(data.script) approach (P0.3, 2026-05-15). The verb set mirrors
// BrowserAction in backend/services/agent_browser.py.
// ----------------------------------------------------------------------------
interface ActionPayload {
  action: string
  selector?: string | null
  xpath?: string | null
  description?: string | null
  value?: string | null
}

interface ActionResult {
  ok: boolean
  error?: string
  text?: string
  value?: string | null
  found?: boolean
}

async function runStructuredAction(payload: ActionPayload): Promise<ActionResult> {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true })
  const tabId = tabs[0]?.id
  if (!tabId) {
    return { ok: false, error: "no active tab" }
  }
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      // NOTE: this `func` runs in the page context, isolated from this
      // module's scope. Everything it needs must come through `args`. No
      // closures over outer variables. No `eval`.
      func: (a: ActionPayload): ActionResult => {
        const findElement = (): Element | null => {
          if (a.selector) {
            try { return document.querySelector(a.selector) } catch { /* invalid selector */ }
          }
          if (a.xpath) {
            try {
              const r = document.evaluate(a.xpath, document, null, XPathResult.FIRST_ORDERED_NODE_TYPE, null)
              return r.singleNodeValue as Element | null
            } catch { /* invalid xpath */ }
          }
          return null
        }
        const el = findElement()
        const needsElement = !["screenshot"].includes(a.action)
        if (!el && needsElement) {
          return { ok: false, error: "element not found" }
        }
        try {
          switch (a.action) {
            case "click":
              (el as HTMLElement).click()
              return { ok: true }
            case "type": {
              const input = el as HTMLInputElement | HTMLTextAreaElement
              input.value = a.value ?? ""
              input.dispatchEvent(new Event("input", { bubbles: true }))
              input.dispatchEvent(new Event("change", { bubbles: true }))
              return { ok: true }
            }
            case "select": {
              const sel = el as HTMLSelectElement
              sel.value = a.value ?? ""
              sel.dispatchEvent(new Event("change", { bubbles: true }))
              return { ok: true }
            }
            case "scroll_to":
              (el as HTMLElement).scrollIntoView({ behavior: "smooth", block: "center" })
              return { ok: true }
            case "hover":
              el!.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }))
              el!.dispatchEvent(new MouseEvent("mouseenter", { bubbles: true }))
              return { ok: true }
            case "extract_text":
              return { ok: true, text: (el as HTMLElement).innerText ?? el!.textContent ?? "" }
            case "extract_attribute":
              return { ok: true, value: el!.getAttribute(a.value ?? "") }
            case "wait_for":
              return { ok: true, found: !!el }
            case "screenshot":
              return { ok: false, error: "screenshot not supported by in-page interpreter" }
            default:
              return { ok: false, error: `unknown action: ${a.action}` }
          }
        } catch (e) {
          return { ok: false, error: String(e) }
        }
      },
      args: [payload],
    })
    return (results?.[0]?.result as ActionResult) ?? { ok: false, error: "no result returned" }
  } catch (e) {
    return { ok: false, error: String(e) }
  }
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
      const response = await tokenFetch(`${API_BASE}/agent-browser/plan-actions`, {
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
      const response = await tokenFetch(`${API_BASE}/agent-browser/find-element`, {
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
      const response = await tokenFetch(`${API_BASE}/agent-browser/prepare-action`, {
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

      // Structured-action interpreter (P0.3+, 2026-05-15). Runs the
      // backend-provided action_payload in the active tab via
      // chrome.scripting.executeScript with a finite verb switch. No eval.
      const payload: ActionPayload | undefined = data?.action_payload
      if (payload && payload.action) {
        const result = await runStructuredAction(payload)
        if (!result.ok) {
          onMessage(`Action failed: ${result.error ?? "unknown"}`, "error")
          return
        }
        // Surface any extracted data so the user sees the result.
        if (result.text) {
          onMessage(`Extracted: ${result.text.slice(0, 120)}${result.text.length > 120 ? "…" : ""}`, "info")
        } else if (result.value !== undefined && result.value !== null) {
          onMessage(`Attribute value: ${result.value}`, "info")
        }
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
