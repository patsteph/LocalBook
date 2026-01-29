import { useEffect, useState } from "react"
import "./style.css"

import type {
  Notebook,
  PageInfo,
  SummaryResult,
  LinkInfo,
  ChatMessage,
  PageContext,
  ViewMode,
  ActionType,
  SearchResult
} from "./types"
import { API_BASE } from "./types"

import {
  cleanUrl,
  getPageContent,
  getCurrentPageInfo
} from "./hooks"

import {
  saveSessionState,
  restoreSessionState,
  loadSavedNotebook,
  saveSelectedNotebook
} from "./hooks"

import {
  checkConnection,
  fetchNotebooks as fetchNotebooksApi,
  fetchPrimaryNotebookId,
  createNotebook as createNotebookApi
} from "./hooks"

import {
  Header,
  PageInfoBar,
  ActionSelector,
  SummaryView,
  ScrapeResult,
  LinksResult,
  CompareResult,
  ChatView,
  ResearchView,
  StatusMessage,
  LoadingSpinner,
  DisconnectedView,
  AutomationView
} from "./components"

function SidePanel() {
  // Core state
  const [notebooks, setNotebooks] = useState<Notebook[]>([])
  const [selectedNotebook, setSelectedNotebook] = useState<string>("")
  const [pageInfo, setPageInfo] = useState<PageInfo | null>(null)
  const [connected, setConnected] = useState(false)
  const [loading, setLoading] = useState(false)
  const [primaryNotebookId, setPrimaryNotebookId] = useState<string | null>(null)

  // Action state
  const [currentAction, setCurrentAction] = useState<ActionType>(null)
  const [summaryResult, setSummaryResult] = useState<SummaryResult | null>(null)
  const [scrapeResult, setScrapeResult] = useState<string | null>(null)
  const [linksResult, setLinksResult] = useState<LinkInfo | null>(null)
  const [compareResult, setCompareResult] = useState<string | null>(null)

  // Message state
  const [message, setMessage] = useState("")
  const [messageType, setMessageType] = useState<"success" | "error" | "info">("info")

  // View state
  const [viewMode, setViewMode] = useState<ViewMode>("actions")

  // Chat state
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([])
  const [chatInput, setChatInput] = useState("")
  const [pageContext, setPageContext] = useState<PageContext | null>(null)
  const [suggestedQuestions, setSuggestedQuestions] = useState<string[]>([])

  // Research state
  const [searchResults, setSearchResults] = useState<SearchResult[]>([])
  const [searchQuery, setSearchQuery] = useState("")
  const [selectedSite, setSelectedSite] = useState<string>("")

  // Journey tracking
  const [pageActions, setPageActions] = useState<string[]>([])

  // Initialize
  useEffect(() => {
    handleCheckConnection()
    handleGetCurrentPage()

    chrome.tabs.onActivated.addListener(() => handleGetCurrentPage())
    chrome.tabs.onUpdated.addListener((_, changeInfo) => {
      if (changeInfo.status === 'complete') handleGetCurrentPage()
    })
  }, [])

  // Save session state when it changes
  useEffect(() => {
    if (pageInfo?.cleanUrl && (summaryResult || searchResults.length > 0 || chatMessages.length > 0)) {
      saveSessionState(pageInfo.cleanUrl, {
        summaryResult,
        searchResults,
        chatMessages,
        pageActions,
        currentAction,
        viewMode
      })
    }
  }, [summaryResult, searchResults, chatMessages, pageActions, currentAction, viewMode, pageInfo])

  // Load saved notebook selection
  useEffect(() => {
    loadSavedNotebook().then((savedId) => {
      if (savedId) setSelectedNotebook(savedId)
    })
  }, [])

  // Save notebook selection
  useEffect(() => {
    if (selectedNotebook) {
      saveSelectedNotebook(selectedNotebook)
    }
  }, [selectedNotebook])

  async function handleCheckConnection() {
    const isConnected = await checkConnection()
    setConnected(isConnected)
    if (isConnected) {
      await handleFetchNotebooks()
    }
  }

  async function handleFetchNotebooks() {
    const nbs = await fetchNotebooksApi()
    setNotebooks(nbs)
    if (nbs.length > 0 && !selectedNotebook) {
      setSelectedNotebook(nbs[0].id)
    }

    const primaryId = await fetchPrimaryNotebookId()
    setPrimaryNotebookId(primaryId)
    if (!selectedNotebook && primaryId) {
      setSelectedNotebook(primaryId)
    }
  }

  async function handleCreateNotebook(name: string) {
    setLoading(true)
    try {
      const newNb = await createNotebookApi(name)
      if (newNb) {
        setSelectedNotebook(newNb.id)
        showMessage(`Created "${newNb.name || name}"`, "success")
        await handleFetchNotebooks()
      } else {
        showMessage("Failed to create notebook", "error")
      }
    } finally {
      setLoading(false)
    }
  }

  async function handleGetCurrentPage() {
    const info = await getCurrentPageInfo()
    if (info) {
      if (pageInfo?.cleanUrl !== info.cleanUrl) {
        const restored = await restoreSessionState(info.cleanUrl)
        if (restored) {
          if (restored.summaryResult) setSummaryResult(restored.summaryResult)
          if (restored.searchResults?.length) setSearchResults(restored.searchResults)
          if (restored.chatMessages?.length) setChatMessages(restored.chatMessages)
          if (restored.pageActions?.length) setPageActions(restored.pageActions)
          if (restored.currentAction) setCurrentAction(restored.currentAction)
          if (restored.viewMode) setViewMode(restored.viewMode)
        } else {
          setPageActions([])
          setSuggestedQuestions([])
          setCurrentAction(null)
          setSummaryResult(null)
          setSearchResults([])
          setChatMessages([])
          setViewMode("actions")
        }
      }
      setPageInfo(info)
    }
  }

  function showMessage(text: string, type: "success" | "error" | "info" = "info") {
    setMessage(text)
    setMessageType(type)
    setTimeout(() => setMessage(""), 5000)
  }

  async function trackAction(action: string) {
    const newActions = [...pageActions, action]
    setPageActions(newActions)

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

  function generatePageQuestions(keyPoints: string[], keyConcepts: string[]) {
    const questions: string[] = []

    if (keyPoints.length > 0) {
      const firstPoint = keyPoints[0]
      if (firstPoint.length > 20) {
        const truncated = firstPoint.length > 150 ? firstPoint.substring(0, 150) + "..." : firstPoint
        questions.push(`Can you explain more about: ${truncated}?`)
      }
    }

    if (keyConcepts.length > 0) {
      questions.push(`What does the article say about ${keyConcepts[0]}?`)
      if (keyConcepts.length > 1) {
        questions.push(`How are ${keyConcepts[0]} and ${keyConcepts[1]} related in this article?`)
      }
    }

    if (questions.length === 0) {
      questions.push("What are the main takeaways from this article?")
    }

    setSuggestedQuestions(questions.slice(0, 2))
  }

  // Action handlers
  async function handleAction(action: ActionType) {
    if (!action) return

    switch (action) {
      case "summary": await handleSummarize(); break
      case "scrape": await handleScrape(); break
      case "links": await handleExtractLinks(); break
      case "compare": await handleCompareWithNotebook(); break
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
        raw_content: content.content.substring(0, 8000)
      })
      showMessage("Summary generated!", "success")
      trackAction("summarize")
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
        handleFetchNotebooks()
        trackAction("scrape")
      } else {
        const errorMsg = data.error || "Capture failed"
        setScrapeResult(`✗ Capture failed\n${errorMsg}`)
        showMessage(errorMsg, "error")
      }
    } catch (e: any) {
      const errorMsg = e.message || "Failed to scrape page"
      setScrapeResult(`✗ Error\n${errorMsg}`)
      showMessage(errorMsg, "error")
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
        incoming: []
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

  // Chat handlers
  function startChatWithContext() {
    if (pageInfo) {
      setPageContext({
        url: pageInfo.url,
        title: pageInfo.title,
        summary: summaryResult?.summary
      })

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
      const endpoint = summaryResult ? `${API_BASE}/chat/query-with-context` : `${API_BASE}/chat/query`

      const historyForRequest = chatMessages
        .filter((m, idx) => {
          if (m.role === "user") return true
          if (m.role === "assistant" && idx === 0 && m.content.includes("I've analyzed")) return false
          return m.role === "assistant"
        })
        .slice(-6)
        .map(m => ({ role: m.role, content: m.content }))

      const requestBody = summaryResult ? {
        notebook_id: selectedNotebook,
        question: chatInput,
        page_context: {
          title: pageInfo?.title || "",
          summary: summaryResult.summary,
          key_points: summaryResult.key_points,
          key_concepts: summaryResult.key_concepts,
          raw_content: summaryResult.raw_content
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

  // Research handlers
  async function handleResearchThis() {
    if (!pageInfo) return

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
      handleFetchNotebooks()
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

  // Render
  if (!connected) {
    return <DisconnectedView onRetry={handleCheckConnection} />
  }

  return (
    <div className="bg-gray-900 text-white min-h-screen flex flex-col">
      <Header
        notebooks={notebooks}
        selectedNotebook={selectedNotebook}
        primaryNotebookId={primaryNotebookId}
        onSelectNotebook={setSelectedNotebook}
        onCreateNotebook={handleCreateNotebook}
        loading={loading}
      />

      {pageInfo && (
        <PageInfoBar pageInfo={pageInfo} onCopyCleanUrl={copyCleanUrl} />
      )}

      {viewMode === "actions" && (
        <ActionSelector loading={loading} onAction={handleAction} />
      )}

      <StatusMessage message={message} type={messageType} />

      {loading && <LoadingSpinner />}

      {!loading && (
        <div className="flex-1 overflow-auto p-3">
          {/* Summary Result */}
          {currentAction === "summary" && summaryResult && viewMode === "actions" && (
            <SummaryView
              summaryResult={summaryResult}
              onStartChat={startChatWithContext}
              onResearch={handleResearchThis}
            />
          )}

          {/* Scrape Result */}
          {currentAction === "scrape" && scrapeResult && (
            <ScrapeResult result={scrapeResult} />
          )}

          {/* Links Result */}
          {currentAction === "links" && linksResult && (
            <LinksResult linksResult={linksResult} />
          )}

          {/* Compare Result */}
          {currentAction === "compare" && compareResult && (
            <CompareResult result={compareResult} />
          )}

          {/* Automation View */}
          {currentAction === "automate" && pageInfo && (
            <AutomationView
              pageUrl={pageInfo.url}
              onMessage={showMessage}
            />
          )}

          {/* Empty State */}
          {!currentAction && viewMode === "actions" && (
            <div className="text-center text-gray-500 py-8">
              <p className="text-sm">Select an action above to get started</p>
            </div>
          )}

          {/* Chat View */}
          {viewMode === "chat" && (
            <ChatView
              chatMessages={chatMessages}
              chatInput={chatInput}
              pageContext={pageContext}
              loading={loading}
              onInputChange={setChatInput}
              onSend={handleSendChat}
              onBack={() => setViewMode("actions")}
            />
          )}

          {/* Research View */}
          {viewMode === "research" && (
            <ResearchView
              searchQuery={searchQuery}
              selectedSite={selectedSite}
              searchResults={searchResults}
              loading={loading}
              onQueryChange={setSearchQuery}
              onSiteChange={setSelectedSite}
              onSearch={handleResearchThis}
              onQuickAdd={quickAddToNotebook}
              onBack={() => setViewMode("actions")}
            />
          )}
        </div>
      )}
    </div>
  )
}

export default SidePanel
