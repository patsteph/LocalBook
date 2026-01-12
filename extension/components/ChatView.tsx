import { useRef, useEffect } from "react"
import type { ChatMessage, PageContext } from "../types"

interface ChatViewProps {
  chatMessages: ChatMessage[]
  chatInput: string
  pageContext: PageContext | null
  loading: boolean
  onInputChange: (value: string) => void
  onSend: () => void
  onBack: () => void
}

export function ChatView({
  chatMessages,
  chatInput,
  pageContext,
  loading,
  onInputChange,
  onSend,
  onBack
}: ChatViewProps) {
  const chatEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [chatMessages])

  return (
    <div className="flex flex-col h-full">
      {/* Back button */}
      <button
        onClick={onBack}
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
          onChange={(e) => onInputChange(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && onSend()}
          placeholder="Ask about this page or your notebook..."
          className="flex-1 p-2 bg-gray-800 border border-gray-600 rounded text-sm"
          disabled={loading}
        />
        <button
          onClick={onSend}
          disabled={loading || !chatInput.trim()}
          className="px-3 py-2 bg-indigo-600 hover:bg-indigo-700 disabled:bg-gray-700 rounded text-sm"
        >
          Send
        </button>
      </div>
    </div>
  )
}
