import { useEffect, useState, useRef } from "react"
import "./style.css"

const API_BASE = "http://localhost:8000"

interface Notebook {
  id: string
  name: string
  source_count: number
}

interface PageInfo {
  url: string
  cleanUrl: string
  title: string
  domain: string
}

interface SummaryResult {
  summary: string
  key_points: string[]
  key_concepts: string[]
  reading_time: number
  raw_content?: string  // Store full page content for detailed Q&A
}

interface LinkInfo {
  outgoing: string[]
  incoming: string[]
}

interface ChatMessage {
  role: "user" | "assistant"
  content: string
  timestamp: number
}

interface PageContext {
  url: string
  title: string
  summary?: string
  content?: string
}

type ViewMode = "actions" | "chat" | "research"
type ActionType = "summary" | "scrape" | "links" | "compare" | null

interface SearchResult {
  title: string
  url: string
  snippet: string
  source_site: string
  published_date?: string
  author?: string
  thumbnail?: string
  metadata?: {
    duration?: string
    view_count?: string
    read_time?: string
    video_id?: string
  }
}

interface JourneyEntry {
  url: string
  title: string
  actions: string[]
  concepts: string[]
  timestamp: number
}

function cleanUrl(url: string): string {
  try {
    const parsed = new URL(url)
    // Remove common tracking parameters
    const trackingParams = [
      'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
      'fbclid', 'gclid', 'ref', 'source', 'mc_cid', 'mc_eid'
    ]
    trackingParams.forEach(param => parsed.searchParams.delete(param))
    return parsed.toString()
  } catch {
    return url
  }
}

function extractDomain(url: string): string {
  try {
    return new URL(url).hostname.replace('www.', '')
  } catch {
    return url
  }
}

function SidePanel() {
  const [notebooks, setNotebooks] = useState<Notebook[]>([])
  const [selectedNotebook, setSelectedNotebook] = useState<string>("")
  const [pageInfo, setPageInfo] = useState<PageInfo | null>(null)
  const [connected, setConnected] = useState(false)
  const [loading, setLoading] = useState(false)
  const [currentAction, setCurrentAction] = useState<ActionType>(null)
  const [summaryResult, setSummaryResult] = useState<SummaryResult | null>(null)
  const [scrapeResult, setScrapeResult] = useState<string | null>(null)
  const [linksResult, setLinksResult] = useState<LinkInfo | null>(null)
  const [compareResult, setCompareResult] = useState<string | null>(null)
  const [message, setMessage] = useState("")
  const [messageType, setMessageType] = useState<"success" | "error" | "info">("info")
  
  // Chat state - connects to notebook RAG
  const [viewMode, setViewMode] = useState<ViewMode>("actions")
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([])
  const [chatInput, setChatInput] = useState("")
  const [pageContext, setPageContext] = useState<PageContext | null>(null)
  const chatEndRef = useRef<HTMLDivElement>(null)
  
  // Research state
  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [searchQuery, setSearchQuery] = useState("")
  const [selectedSite, setSelectedSite] = useState<string>("")
  
  // Journey tracking - auto-track when 2+ actions on a page
  const [pageActions, setPageActions] = useState<string[]>([])
  const [suggestedQuestions, setSuggestedQuestions] = useState<string[]>([])
  
  // Notebook selector UI state
  const [notebookExpanded, setNotebookExpanded] = useState(false)
  const [creatingNotebook, setCreatingNotebook] = useState(false)
  const [newNotebookName, setNewNotebookName] = useState("")
  const [primaryNotebookId, setPrimaryNotebookId] = useState<string | null>(null)

  // Check backend connection and fetch notebooks
  useEffect(() => {
    checkConnection()
    getCurrentPage()
    
    // Listen for tab changes
    chrome.tabs.onActivated.addListener(() => getCurrentPage())
    chrome.tabs.onUpdated.addListener((_, changeInfo) => {
      if (changeInfo.status === 'complete') getCurrentPage()
    })
  }, [])

  // Save state to session storage when it changes
  useEffect(() => {
    if (pageInfo?.cleanUrl && (summaryResult || searchResults.length > 0 || chatMessages.length > 0)) {
      const stateKey = `lb_page_${btoa(pageInfo.cleanUrl).slice(0, 32)}`
      chrome.storage.session.set({
        [stateKey]: {
          summaryResult,
          searchResults,
          chatMessages,
          pageActions,
          currentAction,
          viewMode,
          timestamp: Date.now()
        }
      }).catch(() => {}) // Ignore errors
    }
  }, [summaryResult, searchResults, chatMessages, pageActions, currentAction, viewMode, pageInfo])

  async function restoreSessionState(url: string) {
    try {
      const stateKey = `lb_page_${btoa(url).slice(0, 32)}`
      const result = await chrome.storage.session.get(stateKey)
      const saved = result[stateKey]
      if (saved && Date.now() - saved.timestamp < 30 * 60 * 1000) { // 30 min TTL
        console.log("Restoring session state for:", url)
        if (saved.summaryResult) setSummaryResult(saved.summaryResult)
        if (saved.searchResults?.length) setSearchResults(saved.searchResults)
        if (saved.chatMessages?.length) setChatMessages(saved.chatMessages)
        if (saved.pageActions?.length) setPageActions(saved.pageActions)
        if (saved.currentAction) setCurrentAction(saved.currentAction)
        if (saved.viewMode) setViewMode(saved.viewMode)
        return true // State was restored
      }
    } catch (e) {
      console.log("Session restore failed (non-critical):", e)
    }
    return false // No state to restore
  }

  // Load saved notebook selection
  useEffect(() => {
    chrome.storage.local.get("selectedNotebook", (result) => {
      if (result.selectedNotebook) setSelectedNotebook(result.selectedNotebook)
    })
  }, [])

  // Save notebook selection
  useEffect(() => {
    if (selectedNotebook) {
      chrome.storage.local.set({ selectedNotebook })
    }
  }, [selectedNotebook])

  async function checkConnection() {
    try {
      const res = await fetch(`${API_BASE}/browser/status`)
      if (res.ok) {
        setConnected(true)
        fetchNotebooks()
      } else {
        setConnected(false)
      }
    } catch {
      setConnected(false)
    }
  }

  async function fetchNotebooks() {
    try {
      const res = await fetch(`${API_BASE}/browser/notebooks`)
      if (res.ok) {
        const data = await res.json()
        setNotebooks(data)
        if (data.length > 0 && !selectedNotebook) {
          setSelectedNotebook(data[0].id)
        }
      }
      
      // Also fetch primary notebook preference
      const prefsRes = await fetch(`${API_BASE}/settings/primary-notebook`)
      if (prefsRes.ok) {
        const prefsData = await prefsRes.json()
        setPrimaryNotebookId(prefsData.primary_notebook_id)
        // If no notebook selected yet, prefer primary
        if (!selectedNotebook && prefsData.primary_notebook_id) {
          setSelectedNotebook(prefsData.primary_notebook_id)
        }
      }
    } catch (e) {
      console.error("Failed to fetch notebooks:", e)
    }
  }

  async function createNotebook() {
    if (!newNotebookName.trim()) return
    setLoading(true)
    try {
      const res = await fetch(`${API_BASE}/notebooks/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: newNotebookName.trim() })
      })
      if (!res.ok) throw new Error(await res.text())
      
      const newNb = await res.json()
      setSelectedNotebook(newNb.id)
      setNewNotebookName("")
      setCreatingNotebook(false)
      showMessage(`Created "${newNb.title}"`, "success")
      fetchNotebooks()
    } catch (e: any) {
      showMessage(e.message || "Failed to create notebook", "error")
    } finally {
      setLoading(false)
    }
  }

  async function getCurrentPage() {
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
      if (tab?.url && tab?.title) {
        const newUrl = cleanUrl(tab.url)
        // Check if page changed
        if (pageInfo?.cleanUrl !== newUrl) {
          // Try to restore saved state for this URL first
          const restored = await restoreSessionState(newUrl)
          if (!restored) {
            // No saved state - reset to defaults
            setPageActions([])
            setSuggestedQuestions([])
            setCurrentAction(null)
            setSummaryResult(null)
            setSearchResults([])
            setChatMessages([])
            setViewMode("actions")
          }
        }
        setPageInfo({
          url: tab.url,
          cleanUrl: newUrl,
          title: tab.title,
          domain: extractDomain(tab.url)
        })
      }
    } catch (e) {
      console.error("Failed to get current page:", e)
    }
  }

  function showMessage(text: string, type: "success" | "error" | "info" = "info") {
    setMessage(text)
    setMessageType(type)
    setTimeout(() => setMessage(""), 5000)
  }

  async function getPageContent(): Promise<{ content: string; html: string } | null> {
    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
      if (!tab?.id) return null

      const results = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => ({
          content: document.body.innerText,
          html: document.documentElement.outerHTML
        })
      })
      return results[0]?.result || null
    } catch (e) {
      console.error("Failed to get page content:", e)
      return null
    }
  }

  async function handleSummarize() {
    if (!pageInfo) return
    setLoading(true)
    setCurrentAction("summary")
    setSummaryResult(null)

    try {
      const content = await getPageContent()
      if (!content) throw new Error("Could not extract page content")

      const res = await fetch(`${API_BASE}/browser/summarize`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          content: content.content,
          url: pageInfo.url
        })
      })

      if (!res.ok) throw new Error(await res.text())
      
      const data = await res.json()
      setSummaryResult({
        summary: data.summary || "",
        key_points: data.key_points || [],
        key_concepts: data.key_concepts || [],
        reading_time: data.reading_time_minutes || 0,
        raw_content: content.content.substring(0, 8000)  // Store truncated content for Q&A (fits in context)
      })
      showMessage("Summary generated!", "success")
      trackAction("summarize")
      // Generate suggested questions from page content, not notebook
      generatePageQuestions(data.key_points || [], data.key_concepts || [])
    } catch (e: any) {
      showMessage(e.message || "Failed to summarize", "error")
    } finally {
      setLoading(false)
    }
  }

  async function handleScrape() {
    if (!pageInfo || !selectedNotebook) return
    setLoading(true)
    setCurrentAction("scrape")
    setScrapeResult(null)

    try {
      const content = await getPageContent()
      if (!content) throw new Error("Could not extract page content")

      const res = await fetch(`${API_BASE}/browser/capture`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          notebook_id: selectedNotebook,
          url: pageInfo.cleanUrl,
          title: pageInfo.title,
          content: content.content,
          html_content: content.html,
          capture_type: "full_page"
        })
      })

      if (!res.ok) throw new Error(await res.text())
      
      const data = await res.json()
      if (data.success) {
        setScrapeResult(`✓ Saved to notebook\n${data.word_count} words • ${data.reading_time_minutes} min read`)
        showMessage("Page scraped successfully!", "success")
        fetchNotebooks() // Refresh notebook counts
        trackAction("scrape")
      } else {
        throw new Error(data.error || "Capture failed")
      }
    } catch (e: any) {
      showMessage(e.message || "Failed to scrape page", "error")
    } finally {
      setLoading(false)
    }
  }

  async function handleExtractLinks() {
    if (!pageInfo) return
    setLoading(true)
    setCurrentAction("links")
    setLinksResult(null)

    try {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true })
      if (!tab?.id) throw new Error("No active tab")

      const results = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => {
          const links = Array.from(document.querySelectorAll('a[href]'))
          const outgoing = links
            .map(a => (a as HTMLAnchorElement).href)
            .filter(href => href.startsWith('http') && !href.includes(window.location.hostname))
            .filter((v, i, a) => a.indexOf(v) === i)
            .slice(0, 20)
          return { outgoing }
        }
      })

      setLinksResult({
        outgoing: results[0]?.result?.outgoing || [],
        incoming: [] // Would require web search API
      })
      showMessage("Links extracted!", "success")
      trackAction("links")
    } catch (e: any) {
      showMessage(e.message || "Failed to extract links", "error")
    } finally {
      setLoading(false)
    }
  }

  async function handleCompareWithNotebook() {
    if (!pageInfo || !selectedNotebook) return
    setLoading(true)
    setCurrentAction("compare")
    setCompareResult(null)

    try {
      const content = await getPageContent()
      if (!content) throw new Error("Could not extract page content")

      // Use RAG to find related/contradicting info in notebook
      const res = await fetch(`${API_BASE}/chat/query`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          notebook_id: selectedNotebook,
          question: `Compare this new information with what's in my notebook. Identify any contradictions, confirmations, or new insights:\n\n${content.content.slice(0, 3000)}`
        })
      })

      if (!res.ok) throw new Error(await res.text())
      
      const data = await res.json()
      setCompareResult(data.answer || "No comparison results")
      showMessage("Comparison complete!", "success")
      trackAction("compare")
    } catch (e: any) {
      showMessage(e.message || "Failed to compare", "error")
    } finally {
      setLoading(false)
    }
  }

  // Chat with notebook context + current page context
  async function handleSendChat() {
    if (!chatInput.trim() || !selectedNotebook) return
    
    const userMessage: ChatMessage = {
      role: "user",
      content: chatInput,
      timestamp: Date.now()
    }
    
    setChatMessages(prev => [...prev, userMessage])
    setChatInput("")
    setLoading(true)

    try {
      // Use context-aware endpoint when we have page summary
      const endpoint = summaryResult ? `${API_BASE}/chat/query-with-context` : `${API_BASE}/chat/query`
      
      // Build chat history (exclude system/welcome messages, limit to recent exchanges)
      // Skip first message if it's the welcome message with suggested questions
      const historyForRequest = chatMessages
        .filter((m, idx) => {
          // Always include user messages
          if (m.role === "user") return true
          // For assistant messages, skip if it's the first one (welcome message)
          if (m.role === "assistant" && idx === 0 && m.content.includes("I've analyzed")) return false
          return m.role === "assistant"
        })
        .slice(-6)  // Last 3 exchanges
        .map(m => ({ role: m.role, content: m.content }))
      
      const requestBody = summaryResult ? {
        notebook_id: selectedNotebook,
        question: chatInput,
        page_context: {
          title: pageInfo?.title || "",
          summary: summaryResult.summary,
          key_points: summaryResult.key_points,
          key_concepts: summaryResult.key_concepts,
          raw_content: summaryResult.raw_content  // Full article content for detailed Q&A
        },
        chat_history: historyForRequest,
        enable_web_search: true
      } : {
        notebook_id: selectedNotebook,
        question: pageInfo ? `[Viewing: "${pageInfo.title}"]\n\n${chatInput}` : chatInput,
        enable_web_search: true
      }

      const res = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(requestBody)
      })

      if (!res.ok) throw new Error(await res.text())
      
      const data = await res.json()
      const assistantMessage: ChatMessage = {
        role: "assistant",
        content: data.answer || "No response",
        timestamp: Date.now()
      }
      
      setChatMessages(prev => [...prev, assistantMessage])
      
      // Scroll to bottom
      setTimeout(() => chatEndRef.current?.scrollIntoView({ behavior: "smooth" }), 100)
    } catch (e: any) {
      const errorMessage: ChatMessage = {
        role: "assistant",
        content: `Error: ${e.message || "Failed to get response"}`,
        timestamp: Date.now()
      }
      setChatMessages(prev => [...prev, errorMessage])
    } finally {
      setLoading(false)
    }
  }

  // Switch to chat mode after summary with context
  function startChatWithContext() {
    if (pageInfo) {
      setPageContext({
        url: pageInfo.url,
        title: pageInfo.title,
        summary: summaryResult?.summary
      })
      
      // Build welcome message with suggested questions if available
      let welcomeContent = `I've analyzed "${pageInfo.title}". Ask me anything about this page or how it relates to your notebook "${notebooks.find(n => n.id === selectedNotebook)?.name || 'selected notebook'}".`
      
      if (suggestedQuestions.length > 0) {
        welcomeContent += "\n\n💡 Try asking:\n" + suggestedQuestions.slice(0, 2).map(q => `• ${q}`).join("\n")
      }
      
      setChatMessages([{
        role: "assistant",
        content: welcomeContent,
        timestamp: Date.now()
      }])
      setViewMode("chat")
    }
  }

  // Track action and auto-record journey when 2+ actions
  async function trackAction(action: string) {
    const newActions = [...pageActions, action]
    setPageActions(newActions)
    
    // Auto-record journey when user does 2+ actions (shows engagement)
    if (newActions.length === 2 && pageInfo && selectedNotebook) {
      try {
        await fetch(`${API_BASE}/exploration/record`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            notebook_id: selectedNotebook,
            query: `Browsed: ${pageInfo.title}`,
            topics: summaryResult?.key_concepts || [],
            sources_used: [],
            confidence: 0.8,
            answer_preview: `Actions: ${newActions.join(", ")} on ${pageInfo.domain}`
          })
        })
      } catch (e) {
        console.log("Journey tracking failed (non-critical):", e)
      }
    }
  }

  // Generate suggested questions from page content (key points and concepts)
  function generatePageQuestions(keyPoints: string[], keyConcepts: string[]) {
    const questions: string[] = []
    
    // Generate questions from key points (these are answerable from the summary)
    if (keyPoints.length > 0) {
      // Take first key point and form a question about it
      const firstPoint = keyPoints[0]
      if (firstPoint.length > 20) {
        // Use full key point up to 150 chars, only truncate very long ones
        const truncated = firstPoint.length > 150 ? firstPoint.substring(0, 150) + "..." : firstPoint
        questions.push(`Can you explain more about: ${truncated}?`)
      }
    }
    
    // Generate questions from key concepts
    if (keyConcepts.length > 0) {
      questions.push(`What does the article say about ${keyConcepts[0]}?`)
      if (keyConcepts.length > 1) {
        questions.push(`How are ${keyConcepts[0]} and ${keyConcepts[1]} related in this article?`)
      }
    }
    
    // Fallback generic but answerable question
    if (questions.length === 0) {
      questions.push("What are the main takeaways from this article?")
    }
    
    setSuggestedQuestions(questions.slice(0, 2))
  }

  // Research This - search web and site-specific sources
  async function handleResearchThis() {
    if (!pageInfo) return
    
    // Extract search terms from page title and key concepts
    const searchTerms = summaryResult?.key_concepts?.slice(0, 3).join(" ") || pageInfo.title
    setSearchQuery(searchTerms)
    setViewMode("research")
    setLoading(true)
    setSearchResults([])
    
    try {
      const res = await fetch(`${API_BASE}/site-search/search`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          query: searchTerms,
          site_domain: selectedSite || null,
          time_range: "all",
          max_results: 10
        })
      })
      
      if (!res.ok) throw new Error(await res.text())
      
      const data = await res.json()
      setSearchResults(data.results || [])
      trackAction("research")
    } catch (e: any) {
      showMessage(e.message || "Search failed", "error")
    } finally {
      setLoading(false)
    }
  }

  // Quick add search result to notebook
  async function quickAddToNotebook(result: SearchResult) {
    if (!selectedNotebook) return
    setLoading(true)
    
    try {
      const res = await fetch(`${API_BASE}/web/quick-add`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          notebook_id: selectedNotebook,
          url: result.url,
          title: result.title
        })
      })
      
      if (!res.ok) throw new Error(await res.text())
      
      showMessage(`Added "${result.title}" to notebook`, "success")
      fetchNotebooks()
    } catch (e: any) {
      showMessage(e.message || "Failed to add", "error")
    } finally {
      setLoading(false)
    }
  }

  function copyCleanUrl() {
    if (pageInfo?.cleanUrl) {
      navigator.clipboard.writeText(pageInfo.cleanUrl)
      showMessage("Clean URL copied!", "success")
    }
  }

  if (!connected) {
    return (
      <div className="p-4 bg-gray-900 text-white min-h-screen flex flex-col items-center justify-center">
        <div className="text-6xl mb-4">📚</div>
        <h1 className="text-xl font-bold mb-2">LocalBook</h1>
        <p className="text-gray-400 text-center mb-4">
          Cannot connect to LocalBook.
          <br />Make sure the app is running.
        </p>
        <button
          onClick={checkConnection}
          className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded font-medium"
        >
          Retry Connection
        </button>
      </div>
    )
  }

  return (
    <div className="bg-gray-900 text-white min-h-screen flex flex-col">
      {/* Header - Single Line: LocalBook | Notebook ▼ | Connected */}
      <div className="px-3 py-2 border-b border-gray-700">
        <div className="flex items-center gap-2">
          {/* Logo + Name */}
          <span className="text-lg">📚</span>
          <span className="font-bold text-sm">LocalBook</span>
          <span className="text-gray-600">|</span>
          
          {/* Notebook Selector - inline */}
          <div className="relative flex-1 min-w-0">
            <button
              onClick={() => setNotebookExpanded(!notebookExpanded)}
              className="flex items-center gap-1 px-2 py-1 bg-gray-800 border border-gray-600 rounded text-sm hover:bg-gray-750 max-w-full"
            >
              <span className="truncate text-xs">
                {notebooks.find(n => n.id === selectedNotebook)?.name || "Select"}
              </span>
              {selectedNotebook === primaryNotebookId && (
                <span className="text-purple-400 text-xs">★</span>
              )}
              <span className="text-gray-400 text-xs">{notebookExpanded ? "▲" : "▼"}</span>
            </button>
            
            {/* Dropdown */}
            {notebookExpanded && (
              <div className="absolute z-50 left-0 right-0 mt-1 bg-gray-800 border border-gray-600 rounded shadow-lg max-h-60 overflow-auto" style={{minWidth: "200px"}}>
                {notebooks.map((nb) => (
                  <button
                    key={nb.id}
                    onClick={() => {
                      setSelectedNotebook(nb.id)
                      setNotebookExpanded(false)
                    }}
                    className={`w-full text-left px-3 py-2 text-sm hover:bg-gray-700 flex items-center justify-between ${
                      nb.id === selectedNotebook ? "bg-gray-700" : ""
                    }`}
                  >
                    <span className="truncate">{nb.name}</span>
                    <span className="text-xs text-gray-500 ml-2 flex items-center gap-1">
                      {nb.id === primaryNotebookId && <span className="text-purple-400">★</span>}
                      {nb.source_count}
                    </span>
                  </button>
                ))}
                
                {/* Create new notebook */}
                {creatingNotebook ? (
                  <div className="p-2 border-t border-gray-700">
                    <input
                      type="text"
                      value={newNotebookName}
                      onChange={(e) => setNewNotebookName(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && createNotebook()}
                      placeholder="Notebook name..."
                      className="w-full p-2 bg-gray-700 border border-gray-600 rounded text-sm"
                      autoFocus
                    />
                    <div className="flex gap-2 mt-2">
                      <button
                        onClick={createNotebook}
                        disabled={!newNotebookName.trim() || loading}
                        className="flex-1 px-2 py-1 bg-purple-600 hover:bg-purple-700 disabled:bg-gray-700 rounded text-xs"
                      >
                        Create
                      </button>
                      <button
                        onClick={() => { setCreatingNotebook(false); setNewNotebookName("") }}
                        className="px-2 py-1 bg-gray-700 hover:bg-gray-600 rounded text-xs"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  <button
                    onClick={() => setCreatingNotebook(true)}
                    className="w-full text-left px-3 py-2 text-sm text-purple-400 hover:bg-gray-700 border-t border-gray-700"
                  >
                    + New Notebook
                  </button>
                )}
              </div>
            )}
          </div>
          
          <span className="text-gray-600">|</span>
          
          {/* Connected Status */}
          <div className="flex items-center gap-1 shrink-0">
            <div className="w-2 h-2 rounded-full bg-green-500"></div>
            <span className="text-xs text-gray-400">Connected</span>
          </div>
        </div>
      </div>

      {/* Page Info - compact */}
      {pageInfo && (
        <div className="px-3 py-2 border-b border-gray-700 flex items-center gap-2">
          <div className="flex-1 min-w-0">
            <div className="text-xs font-medium text-gray-300 truncate">
              {pageInfo.title}
            </div>
            <div className="text-xs text-gray-500 truncate">
              {pageInfo.domain}
            </div>
          </div>
          <button
            onClick={copyCleanUrl}
            className="text-xs text-blue-400 hover:text-blue-300 shrink-0"
            title="Copy clean URL"
          >
            📋
          </button>
        </div>
      )}

      {/* Action Dropdown */}
      {viewMode === "actions" && (
        <div className="p-3 border-b border-gray-700">
          <label className="text-xs text-gray-400 block mb-2">Action:</label>
          <select
            className="w-full p-2 bg-gray-800 border border-gray-600 rounded text-sm"
            defaultValue=""
            onChange={(e) => {
              const action = e.target.value
              if (!action) return
              
              switch (action) {
                case "summarize": handleSummarize(); break
                case "scrape": handleScrape(); break
                case "links": handleExtractLinks(); break
                case "compare": handleCompareWithNotebook(); break
              }
              e.target.value = ""
            }}
            disabled={loading}
          >
            <option value="">Select an action...</option>
            <option value="summarize">📝 Summarize Page</option>
            <option value="scrape">📄 Scrape to Notebook</option>
            <option value="links">🔗 Extract Links</option>
            <option value="compare">⚖️ Compare with Notebook</option>
          </select>
        </div>
      )}

      {/* Status Message */}
      {message && (
        <div className={`mx-3 mt-3 p-2 rounded text-sm ${
          messageType === "success" ? "bg-green-900/50 text-green-300" :
          messageType === "error" ? "bg-red-900/50 text-red-300" :
          "bg-gray-800 text-gray-300"
        }`}>
          {message}
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="flex-1 flex items-center justify-center p-4">
          <div className="text-center">
            <div className="animate-spin text-4xl mb-2">⏳</div>
            <p className="text-gray-400">Processing...</p>
          </div>
        </div>
      )}

      {/* Results Area */}
      {!loading && (
        <div className="flex-1 overflow-auto p-3">
          {/* Summary Result */}
          {currentAction === "summary" && summaryResult && viewMode === "actions" && (
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
                  onClick={startChatWithContext}
                  className="flex-1 p-2 bg-indigo-600 hover:bg-indigo-700 rounded text-sm font-medium flex items-center justify-center gap-1"
                >
                  💬 Interact
                </button>
                <button
                  onClick={handleResearchThis}
                  className="flex-1 p-2 bg-emerald-600 hover:bg-emerald-700 rounded text-sm font-medium flex items-center justify-center gap-1"
                >
                  🔍 Research
                </button>
              </div>
            </div>
          )}

          {/* Scrape Result */}
          {currentAction === "scrape" && scrapeResult && (
            <div className="p-3 bg-green-900/30 rounded">
              <pre className="text-sm text-green-300 whitespace-pre-wrap">{scrapeResult}</pre>
            </div>
          )}

          {/* Links Result */}
          {currentAction === "links" && linksResult && (
            <div className="space-y-3">
              <h3 className="font-bold text-sm text-gray-300">
                Outgoing Links ({linksResult.outgoing.length})
              </h3>
              <div className="space-y-1 max-h-80 overflow-auto">
                {linksResult.outgoing.map((link, i) => (
                  <a
                    key={i}
                    href={link}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="block text-xs text-blue-400 hover:text-blue-300 truncate"
                  >
                    {extractDomain(link)}: {link}
                  </a>
                ))}
              </div>
            </div>
          )}

          {/* Compare Result */}
          {currentAction === "compare" && compareResult && (
            <div className="space-y-3">
              <h3 className="font-bold text-sm text-gray-300">Notebook Comparison</h3>
              <p className="text-sm text-gray-200 whitespace-pre-wrap">
                {compareResult}
              </p>
            </div>
          )}

          {/* Empty State */}
          {!currentAction && viewMode === "actions" && (
            <div className="text-center text-gray-500 py-8">
              <p className="text-sm">Select an action above to get started</p>
            </div>
          )}

          {/* Chat View */}
          {viewMode === "chat" && (
            <div className="flex flex-col h-full">
              {/* Back button */}
              <button
                onClick={() => setViewMode("actions")}
                className="text-xs text-gray-400 hover:text-gray-200 mb-2 flex items-center gap-1"
              >
                ← Back to actions
              </button>

              {/* Context indicator */}
              {pageContext && (
                <div className="p-2 bg-indigo-900/30 rounded mb-3 text-xs">
                  <span className="text-indigo-300">📄 Context:</span>
                  <span className="text-gray-300 ml-1 truncate">{pageContext.title}</span>
                </div>
              )}

              {/* Chat messages */}
              <div className="flex-1 overflow-auto space-y-3 mb-3">
                {chatMessages.map((msg, i) => (
                  <div
                    key={i}
                    className={`p-2 rounded text-sm ${
                      msg.role === "user"
                        ? "bg-blue-900/50 text-blue-100 ml-4"
                        : "bg-gray-800 text-gray-200 mr-4"
                    }`}
                  >
                    <div className="text-xs text-gray-500 mb-1">
                      {msg.role === "user" ? "You" : "LocalBook"}
                    </div>
                    <div className="whitespace-pre-wrap">{msg.content}</div>
                  </div>
                ))}
                <div ref={chatEndRef} />
              </div>

              {/* Chat input */}
              <div className="flex gap-2">
                <input
                  type="text"
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && handleSendChat()}
                  placeholder="Ask about this page or your notebook..."
                  className="flex-1 p-2 bg-gray-800 border border-gray-600 rounded text-sm"
                  disabled={loading}
                />
                <button
                  onClick={handleSendChat}
                  disabled={loading || !chatInput.trim()}
                  className="px-3 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-gray-700 rounded text-sm"
                >
                  Send
                </button>
              </div>
            </div>
          )}

          {/* Research View */}
          {viewMode === "research" && (
            <div className="flex flex-col h-full">
              {/* Back button */}
              <button
                onClick={() => setViewMode("actions")}
                className="text-xs text-gray-400 hover:text-gray-200 mb-2 flex items-center gap-1"
              >
                ← Back to actions
              </button>

              {/* Search controls */}
              <div className="space-y-2 mb-3">
                <div className="flex gap-2">
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleResearchThis()}
                    placeholder="Search terms..."
                    className="flex-1 p-2 bg-gray-800 border border-gray-600 rounded text-sm"
                  />
                  <button
                    onClick={handleResearchThis}
                    disabled={loading}
                    className="px-3 py-2 bg-emerald-600 hover:bg-emerald-700 disabled:bg-gray-700 rounded text-sm"
                  >
                    🔍
                  </button>
                </div>
                <select
                  value={selectedSite}
                  onChange={(e) => setSelectedSite(e.target.value)}
                  className="w-full p-2 bg-gray-800 border border-gray-600 rounded text-xs"
                >
                  <option value="">All sources</option>
                  <option value="youtube.com">📺 YouTube</option>
                  <option value="arxiv.org">📄 ArXiv</option>
                  <option value="github.com">💻 GitHub</option>
                  <option value="reddit.com">🗣️ Reddit</option>
                  <option value="news.ycombinator.com">🟠 Hacker News</option>
                  <option value="pubmed.gov">🏥 PubMed</option>
                  <option value="wikipedia.org">📚 Wikipedia</option>
                </select>
              </div>

              {/* Search results */}
              <div className="flex-1 overflow-auto space-y-2">
                {searchResults.length === 0 && !loading && (
                  <div className="text-center text-gray-500 py-4">
                    <p className="text-sm">No results yet. Modify search terms or select a source.</p>
                  </div>
                )}
                {searchResults.map((result, i) => (
                  <div key={i} className="p-2 bg-gray-800 rounded border border-gray-700">
                    <div className="flex items-start justify-between gap-2">
                      {/* Thumbnail for YouTube */}
                      {result.thumbnail && (
                        <img 
                          src={result.thumbnail} 
                          alt="" 
                          className="w-16 h-12 object-cover rounded shrink-0"
                        />
                      )}
                      <div className="flex-1 min-w-0">
                        <a
                          href={result.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-sm text-blue-400 hover:text-blue-300 font-medium line-clamp-2"
                        >
                          {result.title}
                        </a>
                        {/* Metadata row: source + duration/read time + views */}
                        <div className="flex flex-wrap items-center gap-1.5 mt-1">
                          <span className="text-xs bg-gray-700 px-1.5 py-0.5 rounded text-gray-400">
                            {result.source_site}
                          </span>
                          {result.metadata?.duration && (
                            <span className="text-xs bg-purple-900/50 text-purple-300 px-1.5 py-0.5 rounded">
                              ⏱️ {result.metadata.duration}
                            </span>
                          )}
                          {result.metadata?.view_count && (
                            <span className="text-xs bg-blue-900/50 text-blue-300 px-1.5 py-0.5 rounded">
                              👁️ {result.metadata.view_count}
                            </span>
                          )}
                          {result.metadata?.read_time && (
                            <span className="text-xs bg-green-900/50 text-green-300 px-1.5 py-0.5 rounded">
                              📖 {result.metadata.read_time}
                            </span>
                          )}
                          {result.published_date && (
                            <span className="text-xs text-gray-500">
                              {result.published_date}
                            </span>
                          )}
                        </div>
                        <p className="text-xs text-gray-400 mt-1 line-clamp-2">{result.snippet}</p>
                      </div>
                      <button
                        onClick={() => quickAddToNotebook(result)}
                        disabled={loading}
                        className="shrink-0 px-2 py-1 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-700 rounded text-xs"
                        title="Add to notebook"
                      >
                        + Add
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default SidePanel
