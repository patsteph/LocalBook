import { useState } from "react"
import { API_BASE, tokenFetch } from "../types"

interface AutomationViewProps {
  pageUrl: string
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

// ----------------------------------------------------------------------------
// Page-context builder — the backend's /agent-browser endpoints now require a
// structured `page_context: {url, title, elements[], text_content}` (elements
// each carry tag/text/selector/xpath/attributes/position, matching ElementInfo
// in backend/api/agent_browser.py). We extract that LIVE from the active tab
// via executeScript (the extension used to send raw page_html, which the
// refactored backend rejects with 422). Runs in the page — self-contained.
// ----------------------------------------------------------------------------
interface PageElement {
  tag: string
  text: string
  selector: string
  xpath: string | null
  attributes: Record<string, string>
  position: { x: number; y: number; width: number; height: number }
}
interface PageContextPayload {
  url: string
  title: string
  elements: PageElement[]
  text_content: string
}

function extractPageForAgent(maxEls: number): { title: string; text_content: string; elements: PageElement[] } {
  const cssPath = (start: Element): string => {
    const parts: string[] = []
    let node: Element | null = start
    while (node && node.nodeType === 1 && parts.length < 5) {
      if (node.id) {
        try { parts.unshift(`#${CSS.escape(node.id)}`); break } catch { /* fall through */ }
      }
      let sel = node.tagName.toLowerCase()
      const parent: Element | null = node.parentElement
      if (parent) {
        const sameTag = Array.from(parent.children).filter((c) => c.tagName === node!.tagName)
        if (sameTag.length > 1) sel += `:nth-of-type(${sameTag.indexOf(node) + 1})`
      }
      parts.unshift(sel)
      node = node.parentElement
    }
    return parts.join(" > ")
  }
  const ATTRS = ["type", "name", "id", "aria-label", "placeholder", "href", "role", "title", "value"]
  let nodes: Element[] = []
  try {
    nodes = Array.from(
      document.querySelectorAll("a[href], button, input, select, textarea, [role=button], [role=link], [onclick], summary, label")
    ).slice(0, maxEls)
  } catch { /* querySelectorAll can throw on exotic docs */ }
  const elements: PageElement[] = nodes.map((node) => {
    const rect = (node as HTMLElement).getBoundingClientRect()
    const attributes: Record<string, string> = {}
    for (const name of ATTRS) {
      const v = node.getAttribute(name)
      if (v) attributes[name] = v.slice(0, 120)
    }
    return {
      tag: node.tagName.toLowerCase(),
      text: ((node as HTMLElement).innerText || node.textContent || "").trim().slice(0, 120),
      selector: cssPath(node),
      xpath: null,
      attributes,
      position: {
        x: Math.round(rect.x), y: Math.round(rect.y),
        width: Math.round(rect.width), height: Math.round(rect.height),
      },
    }
  })
  return {
    title: document.title,
    text_content: (document.body?.innerText || "").slice(0, 2000),
    elements,
  }
}

async function collectPageContext(url: string): Promise<PageContextPayload | null> {
  const tabs = await chrome.tabs.query({ active: true, currentWindow: true })
  const tabId = tabs[0]?.id
  if (!tabId) return null
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId },
      func: extractPageForAgent,
      args: [200],
    })
    const r = results?.[0]?.result
    if (!r) return null
    return { url, title: r.title, elements: r.elements, text_content: r.text_content }
  } catch {
    // Restricted page (chrome://, Web Store), no permission, etc.
    return null
  }
}

interface ActionStep {
  action: string
  target?: string
  value?: string
}

export function AutomationView({ pageUrl, onMessage }: AutomationViewProps) {
  const [goal, setGoal] = useState("")
  const [loading, setLoading] = useState(false)
  const [steps, setSteps] = useState<ActionStep[]>([])
  const [currentStep, setCurrentStep] = useState(-1)
  const [elementDescription, setElementDescription] = useState("")
  const [foundElement, setFoundElement] = useState<{ selector?: string; element_type?: string; confidence?: number } | null>(null)

  const planActions = async () => {
    if (!goal.trim()) return
    setLoading(true)
    setSteps([])
    setCurrentStep(-1)
    try {
      const page_context = await collectPageContext(pageUrl)
      if (!page_context) {
        onMessage("Couldn't read this page (it may be a restricted page).", "error")
        return
      }
      const response = await tokenFetch(`${API_BASE}/agent-browser/plan-actions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ goal, page_context })
      })
      if (!response.ok) throw new Error(await response.text())
      const data = await response.json()
      const planned: ActionStep[] = Array.isArray(data.actions) ? data.actions : []
      if (planned.length === 0) {
        onMessage("No actions planned for that goal on this page.", "info")
        return
      }
      setSteps(planned)
      setCurrentStep(0)
      onMessage(`Planned ${planned.length} step${planned.length !== 1 ? "s" : ""}`, "success")
    } catch (err) {
      onMessage(`Planning failed: ${err instanceof Error ? err.message : err}`, "error")
    } finally {
      setLoading(false)
    }
  }

  const findElement = async () => {
    if (!elementDescription.trim()) return
    setLoading(true)
    setFoundElement(null)
    try {
      const page_context = await collectPageContext(pageUrl)
      if (!page_context) {
        onMessage("Couldn't read this page (it may be a restricted page).", "error")
        return
      }
      const response = await tokenFetch(`${API_BASE}/agent-browser/find-element`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description: elementDescription, page_context })
      })
      if (!response.ok) throw new Error(await response.text())
      const data = await response.json()
      if (data.found && data.element) {
        setFoundElement({
          selector: data.element.selector,
          element_type: data.element.element_type,
          confidence: data.confidence,
        })
        onMessage(`Found: ${data.element.element_type || "element"} — ${data.element.selector || "?"}`, "success")
      } else {
        onMessage("No matching element found on this page.", "info")
      }
    } catch (err) {
      onMessage(`Element search failed: ${err instanceof Error ? err.message : err}`, "error")
    } finally {
      setLoading(false)
    }
  }

  const executeStep = async (step: ActionStep, index: number) => {
    setLoading(true)
    try {
      const page_context = await collectPageContext(pageUrl)
      if (!page_context) {
        onMessage("Couldn't read this page (it may be a restricted page).", "error")
        return
      }
      // 1. Resolve the step's natural-language target to a concrete selector.
      let selector: string | undefined
      let xpath: string | undefined
      if (step.target) {
        const findRes = await tokenFetch(`${API_BASE}/agent-browser/find-element`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ description: step.target, page_context })
        })
        if (findRes.ok) {
          const found = await findRes.json()
          if (found.found && found.element) {
            selector = found.element.selector ?? undefined
            xpath = found.element.xpath ?? undefined
          }
        }
      }
      // 2. Prepare the action payload (backend builds {action, selector, xpath, description, value}).
      const prepRes = await tokenFetch(`${API_BASE}/agent-browser/prepare-action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: step.action,
          element_description: step.target || step.action,
          element_selector: selector,
          element_xpath: xpath,
          value: step.value
        })
      })
      if (!prepRes.ok) throw new Error(await prepRes.text())
      const data = await prepRes.json()

      // 3. Run the action_payload in the active tab (finite verb switch, no eval).
      const payload: ActionPayload | undefined = data?.action_payload
      if (payload && payload.action) {
        const result = await runStructuredAction(payload)
        if (!result.ok) {
          onMessage(`Step ${index + 1} failed: ${result.error ?? "unknown"}`, "error")
          return
        }
        if (result.text) {
          onMessage(`Extracted: ${result.text.slice(0, 120)}${result.text.length > 120 ? "…" : ""}`, "info")
        } else if (result.value !== undefined && result.value !== null) {
          onMessage(`Attribute value: ${result.value}`, "info")
        }
      }

      onMessage(`Executed step ${index + 1}: ${step.action}`, "success")
      if (index < steps.length - 1) setCurrentStep(index + 1)
    } catch (err) {
      onMessage(`Action failed: ${err instanceof Error ? err.message : err}`, "error")
    } finally {
      setLoading(false)
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
          {loading ? "Working..." : "🎯 Plan Actions"}
        </button>
      </div>

      {/* Action Plan */}
      {steps.length > 0 && (
        <div className="bg-gray-800 rounded p-3 space-y-2">
          <div className="text-xs text-gray-400">Action Plan ({steps.length} steps)</div>
          <div className="space-y-2">
            {steps.map((step, i) => (
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
                    className="mt-2 px-3 py-1 bg-green-600 hover:bg-green-700 rounded text-xs disabled:opacity-50"
                    onClick={() => executeStep(step, i)}
                    disabled={loading}
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
            {foundElement.selector || "(no selector)"}
          </div>
          <div className="text-xs text-gray-400 mt-2">
            Type: {foundElement.element_type || "unknown"} |
            Confidence: {Math.round((foundElement.confidence ?? 0) * 100)}%
          </div>
        </div>
      )}
    </div>
  )
}
