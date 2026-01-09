import { useState, useEffect } from "react"
import "./style.css"

interface Notebook {
  id: string
  name: string
  source_count: number
}

interface CaptureResult {
  success: boolean
  title: string
  word_count: number
  reading_time_minutes: number
  summary?: string
  key_concepts?: string[]
  error?: string
}

const API_BASE = "http://localhost:8000"

function IndexPopup() {
  const [notebooks, setNotebooks] = useState<Notebook[]>([])
  const [selectedNotebook, setSelectedNotebook] = useState<string>("")
  const [status, setStatus] = useState<"idle" | "loading" | "success" | "error" | "offline">("idle")
  const [message, setMessage] = useState("")
  const [lastCapture, setLastCapture] = useState<CaptureResult | null>(null)
  const [pageInfo, setPageInfo] = useState<{ title: string; url: string } | null>(null)

  useEffect(() => {
    checkConnection()
    loadNotebooks()
    getCurrentTab()
  }, [])

  const checkConnection = async () => {
    try {
      const res = await fetch(`${API_BASE}/browser/status`)
      if (!res.ok) throw new Error("Offline")
    } catch {
      setStatus("offline")
      setMessage("LocalBook is not running. Start the app first.")
    }
  }

  const loadNotebooks = async () => {
    try {
      const res = await fetch(`${API_BASE}/browser/notebooks`)
      if (res.ok) {
        const data = await res.json()
        setNotebooks(data)
        if (data.length > 0) {
          // Load saved preference or use first notebook
          const saved = await chrome.storage.local.get("selectedNotebook")
          if (saved.selectedNotebook && data.find((n: Notebook) => n.id === saved.selectedNotebook)) {
            setSelectedNotebook(saved.selectedNotebook)
          } else {
            setSelectedNotebook(data[0].id)
          }
        }
      }
    } catch (e) {
      console.error("Failed to load notebooks:", e)
    }
  }

  const getCurrentTab = async () => {
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
      if (tab?.title && tab?.url) {
        setPageInfo({ title: tab.title, url: tab.url })
      }
    } catch (e) {
      console.error("Failed to get tab:", e)
    }
  }

  const handleNotebookChange = async (notebookId: string) => {
    setSelectedNotebook(notebookId)
    await chrome.storage.local.set({ selectedNotebook: notebookId })
  }

  const capturePage = async () => {
    if (!selectedNotebook || !pageInfo) return
    
    setStatus("loading")
    setMessage("Capturing page...")

    try {
      // Get page content from content script
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
      
      const results = await chrome.scripting.executeScript({
        target: { tabId: tab.id! },
        func: () => {
          // Get text content
          const content = document.body.innerText || ""
          // Get HTML for metadata extraction
          const html = document.documentElement.outerHTML
          return { content, html }
        }
      })

      const pageData = results[0]?.result
      if (!pageData) throw new Error("Could not extract page content")

      // Send to LocalBook
      const res = await fetch(`${API_BASE}/browser/capture`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: pageInfo.url,
          title: pageInfo.title,
          content: pageData.content,
          html_content: pageData.html,
          notebook_id: selectedNotebook,
          capture_type: "page"
        })
      })

      const result: CaptureResult = await res.json()
      
      if (result.success) {
        setStatus("success")
        setMessage(`Captured! ${result.word_count} words, ~${result.reading_time_minutes} min read`)
        setLastCapture(result)
      } else {
        throw new Error(result.error || "Capture failed")
      }
    } catch (e) {
      setStatus("error")
      setMessage(e instanceof Error ? e.message : "Failed to capture page")
    }
  }

  const captureSelection = async () => {
    if (!selectedNotebook) return
    
    setStatus("loading")
    setMessage("Capturing selection...")

    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
      
      const results = await chrome.scripting.executeScript({
        target: { tabId: tab.id! },
        func: () => window.getSelection()?.toString() || ""
      })

      const selectedText = results[0]?.result
      if (!selectedText) {
        setStatus("error")
        setMessage("No text selected")
        return
      }

      const res = await fetch(`${API_BASE}/browser/capture/selection`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: pageInfo?.url || "",
          title: pageInfo?.title || "",
          selected_text: selectedText,
          notebook_id: selectedNotebook
        })
      })

      const result: CaptureResult = await res.json()
      
      if (result.success) {
        setStatus("success")
        setMessage(`Captured selection! ${result.word_count} words`)
        setLastCapture(result)
      } else {
        throw new Error(result.error || "Capture failed")
      }
    } catch (e) {
      setStatus("error")
      setMessage(e instanceof Error ? e.message : "Failed to capture selection")
    }
  }

  if (status === "offline") {
    return (
      <div className="w-80 p-4 bg-gray-900 text-white">
        <div className="text-center">
          <div className="text-4xl mb-2">📚</div>
          <h1 className="text-lg font-bold mb-2">LocalBook</h1>
          <p className="text-red-400 text-sm">{message}</p>
          <button 
            onClick={checkConnection}
            className="mt-4 px-4 py-2 bg-blue-600 rounded hover:bg-blue-700"
          >
            Retry Connection
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="w-80 p-4 bg-gray-900 text-white">
      {/* Header */}
      <div className="flex items-center gap-2 mb-4">
        <span className="text-2xl">📚</span>
        <h1 className="text-lg font-bold">LocalBook Companion</h1>
      </div>

      {/* Current Page */}
      {pageInfo && (
        <div className="mb-4 p-2 bg-gray-800 rounded text-sm">
          <div className="truncate font-medium">{pageInfo.title}</div>
          <div className="truncate text-gray-400 text-xs">{pageInfo.url}</div>
        </div>
      )}

      {/* Notebook Selector */}
      <div className="mb-4">
        <label className="text-sm text-gray-400 block mb-1">Save to notebook:</label>
        <select
          value={selectedNotebook}
          onChange={(e) => handleNotebookChange(e.target.value)}
          className="w-full p-2 bg-gray-800 border border-gray-700 rounded text-white"
        >
          {notebooks.map((nb) => (
            <option key={nb.id} value={nb.id}>
              {nb.name} ({nb.source_count} sources)
            </option>
          ))}
        </select>
      </div>

      {/* Capture Buttons */}
      <div className="space-y-2 mb-4">
        <button
          onClick={capturePage}
          disabled={status === "loading" || !selectedNotebook}
          className="w-full py-2 px-4 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 rounded font-medium transition-colors"
        >
          {status === "loading" ? "Scraping..." : "📄 Scrape Page"}
        </button>
        
        <button
          onClick={captureSelection}
          disabled={status === "loading" || !selectedNotebook}
          className="w-full py-2 px-4 bg-green-600 hover:bg-green-700 disabled:bg-gray-700 rounded font-medium transition-colors"
        >
          ✂️ Scrape Section
        </button>
      </div>

      {/* Status Message */}
      {message && (
        <div className={`p-2 rounded text-sm ${
          status === "success" ? "bg-green-900/50 text-green-300" :
          status === "error" ? "bg-red-900/50 text-red-300" :
          "bg-gray-800 text-gray-300"
        }`}>
          {message}
        </div>
      )}

      {/* Last Capture Summary */}
      {lastCapture?.summary && (
        <div className="mt-4 p-2 bg-gray-800 rounded text-sm">
          <div className="text-gray-400 text-xs mb-1">Summary:</div>
          <div className="text-gray-300">{lastCapture.summary.slice(0, 200)}...</div>
          {lastCapture.key_concepts && lastCapture.key_concepts.length > 0 && (
            <div className="mt-2">
              <div className="text-gray-400 text-xs mb-1">Key Concepts:</div>
              <div className="flex flex-wrap gap-1">
                {lastCapture.key_concepts.slice(0, 5).map((concept, i) => (
                  <span key={i} className="px-2 py-0.5 bg-blue-900/50 text-blue-300 rounded text-xs">
                    {concept}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default IndexPopup
