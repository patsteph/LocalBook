import React, { useState, useRef, useEffect } from 'react';
import { chatService } from '../services/chat';
import { explorationService } from '../services/exploration';
import { ChatMessage, Citation as CitationType } from '../types';
import { Button } from './shared/Button';
// LoadingSpinner removed - now using statusMessage in message bubble
import { ErrorMessage } from './shared/ErrorMessage';
import { Citation, CitationList } from './Citation';
import { SourceNotesViewer } from './SourceNotesViewer';

interface ChatInterfaceProps {
  notebookId: string | null;
  llmProvider: string;
  onOpenWebSearch?: (query?: string) => void;
  prefillQuery?: string;  // Pre-fill the input from external sources (e.g., Constellation)
}

export const ChatInterface: React.FC<ChatInterfaceProps> = ({ notebookId, llmProvider, onOpenWebSearch, prefillQuery }) => {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [deepThink, setDeepThink] = useState(false);  // Deep Think mode toggle
  
  // Handle prefill from external sources
  useEffect(() => {
    if (prefillQuery) {
      setInput(prefillQuery);
    }
  }, [prefillQuery]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // loadingMessage state removed - now using statusMessage from backend
  const [activeDeepThink, setActiveDeepThink] = useState(false);  // Actual mode being used (may differ from toggle due to auto-upgrade)
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Source viewer state
  const [sourceViewerOpen, setSourceViewerOpen] = useState(false);
  const [selectedSource, setSelectedSource] = useState<{
    sourceId: string;
    sourceName: string;
    searchTerm: string;
  } | null>(null);

  useEffect(() => {
    scrollToBottom();
  }, [messages]);

  // Note: We intentionally do NOT load suggested questions before the user starts chatting.
  // Follow-up questions come with each response after the user asks their first question.
  // Messages are preserved when switching tabs, but cleared when notebook changes
  const prevNotebookId = React.useRef(notebookId);
  useEffect(() => {
    // Only clear messages when switching to a DIFFERENT notebook
    if (notebookId && notebookId !== prevNotebookId.current) {
      setMessages([]);
      prevNotebookId.current = notebookId;
    }
  }, [notebookId]);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  // Strip trailing citation/reference lists that LLM might add despite instructions
  const stripTrailingCitations = (content: string): string => {
    // Only strip explicit reference sections at the very end
    // Be conservative to avoid removing actual content
    const patterns = [
      // "References:" or "Sources:" header followed by list
      /\n\n(?:References|Sources|Citations):\s*\n(?:\s*[-•*]?\s*\[?\d+\]?[.:]\s*[^\n]+\n?)+$/i,
      // Horizontal rule followed by numbered list
      /\n\n---+\s*\n(?:\s*\[?\d+\]?[.:]\s*[^\n]+\n?)+$/,
    ];
    
    let result = content;
    for (const pattern of patterns) {
      const match = result.match(pattern);
      // Only strip if the match is less than 30% of total content (safety check)
      if (match && match[0].length < result.length * 0.3) {
        result = result.replace(pattern, '');
      }
    }
    return result.trim();
  };

  // Render message content with inline clickable citations
  const renderMessageWithCitations = (content: string, citations?: CitationType[]) => {
    if (!citations || citations.length === 0) {
      return <p className="text-sm whitespace-pre-wrap">{content}</p>;
    }

    // Parse the content and replace [N] with clickable citation components
    const parts: (string | React.ReactElement)[] = [];
    let lastIndex = 0;
    const citationRegex = /\[(\d+)\]/g;
    let match;

    while ((match = citationRegex.exec(content)) !== null) {
      const citationNumber = parseInt(match[1], 10);
      const citation = citations.find(c => c.number === citationNumber);

      // Add text before the citation
      if (match.index > lastIndex) {
        parts.push(content.substring(lastIndex, match.index));
      }

      // Add the clickable citation
      if (citation) {
        parts.push(<Citation key={`cite-${citationNumber}-${match.index}`} citation={citation} onViewSource={handleViewSource} />);
      } else {
        // If citation not found, just show the number
        parts.push(match[0]);
      }

      lastIndex = match.index + match[0].length;
    }

    // Add remaining text
    if (lastIndex < content.length) {
      parts.push(content.substring(lastIndex));
    }

    return (
      <p className="text-sm whitespace-pre-wrap">
        {parts.map((part, i) =>
          typeof part === 'string' ? <span key={i}>{part}</span> : part
        )}
      </p>
    );
  };

  // Old cycling loading messages removed - now using statusMessage from backend (Phase 1.2)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || !notebookId || loading) return;

    const currentQuestion = input;
    
    const userMessage: ChatMessage = {
      role: 'user',
      content: input,
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput('');
    setLoading(true);
    setError(null);

    // Create a placeholder for the streaming response
    const streamingMessage: ChatMessage = {
      role: 'assistant',
      content: '',
      citations: [],
      timestamp: new Date(),
    };
    
    // Add the streaming message placeholder
    setMessages((prev) => [...prev, streamingMessage]);

    let currentContent = '';
    let currentCitations: CitationType[] = [];
    let isLowConfidence = false;
    let hasStartedStreaming = false;
    let tokenBuffer = '';
    let lastUpdateTime = 0;
    const UPDATE_INTERVAL = 50; // Update every 50ms for smooth appearance

    try {
      await chatService.queryStream(
        {
          notebook_id: notebookId,
          question: currentQuestion,
          top_k: 5,
          enable_web_search: false,
          llm_provider: llmProvider,
          deep_think: deepThink,
        },
        {
          onMode: (isDeepThink, _autoUpgraded) => {
            // Track when deep think mode is active (manual or auto-upgraded)
            setActiveDeepThink(isDeepThink);
          },
          onStatus: (message, _queryType) => {
            // Phase 1.2: Show progressive status updates
            setMessages((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              if (updated[lastIdx]?.role === 'assistant') {
                updated[lastIdx] = {
                  ...updated[lastIdx],
                  statusMessage: message,
                };
              }
              return updated;
            });
          },
          onCitations: (citations, _sources, lowConfidence) => {
            // Store citations but don't show yet - wait for quick summary
            currentCitations = citations;
            isLowConfidence = lowConfidence;
          },
          onQuickSummary: (summary) => {
            // Show quick summary immediately - this gives user fast feedback
            setMessages((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              if (updated[lastIdx]?.role === 'assistant') {
                updated[lastIdx] = {
                  ...updated[lastIdx],
                  quickSummary: summary,
                  citations: currentCitations,
                };
              }
              return updated;
            });
            
            // Stop showing loading indicator once we have quick summary
            setLoading(false);
          },
          onToken: (token) => {
            tokenBuffer += token;
            currentContent += token;
            
            // Hide loading indicator once streaming starts
            if (!hasStartedStreaming && currentContent.length > 0) {
              hasStartedStreaming = true;
              setLoading(false);  // Stop showing "Searching..." indicator
            }
            
            const now = Date.now();
            // Batch updates for smoother appearance
            if (now - lastUpdateTime >= UPDATE_INTERVAL) {
              lastUpdateTime = now;
              const contentToShow = currentContent;
              const citationsToShow = hasStartedStreaming ? currentCitations : 
                (currentContent.length > 0 ? currentCitations : []);
              
              setMessages((prev) => {
                const updated = [...prev];
                const lastIdx = updated.length - 1;
                if (updated[lastIdx]?.role === 'assistant') {
                  updated[lastIdx] = {
                    ...updated[lastIdx],
                    content: contentToShow,
                    citations: citationsToShow,
                  };
                }
                return updated;
              });
              tokenBuffer = '';
            }
          },
          onDone: (followUpQuestions) => {
            // Finalize the message with follow-up questions and low confidence prompt
            setMessages((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              if (updated[lastIdx]?.role === 'assistant') {
                // Strip any trailing citation/reference lists the LLM might have added
                let finalContent = stripTrailingCitations(currentContent);
                let lowConfidenceQuery: string | undefined;
                
                if (isLowConfidence) {
                  finalContent += "\n\n---\n\n**I found limited information in your sources.** Would you like me to search the web for more information?";
                  lowConfidenceQuery = currentQuestion;
                }
                
                updated[lastIdx] = {
                  ...updated[lastIdx],
                  content: finalContent,
                  citations: currentCitations,
                  follow_up_questions: followUpQuestions,
                  lowConfidenceQuery,
                };
              }
              return updated;
            });
            setLoading(false);
            setActiveDeepThink(false);  // Reset deep think indicator
            
            // Record query in exploration journey (fire and forget)
            const topics = [...new Set(currentCitations.map(c => c.filename).filter(Boolean))];
            const sourceIds = [...new Set(currentCitations.map(c => c.source_id))];
            const confidence = isLowConfidence ? 0.3 : 0.7;
            explorationService.recordQuery(
              notebookId!,
              currentQuestion,
              topics.slice(0, 5),
              sourceIds,
              confidence,
              currentContent.slice(0, 200)
            ).catch(err => console.error('Failed to record query:', err));
          },
          onError: (errorMsg) => {
            console.error('Stream error:', errorMsg);
            setError(errorMsg);
            setLoading(false);
          },
        }
      );
    } catch (err) {
      console.error('Query failed:', err);
      setError('Failed to get response. Please try again.');
      setLoading(false);
    }
  };

  const handleFollowUpQuestion = (question: string) => {
    setInput(question);
    // Auto-submit
    setTimeout(() => {
      const form = document.querySelector('form');
      if (form) {
        form.dispatchEvent(new Event('submit', { cancelable: true, bubbles: true }));
      }
    }, 100);
  };

  const handleViewSource = (sourceId: string, sourceName: string, searchTerm: string) => {
    setSelectedSource({ sourceId, sourceName, searchTerm });
    setSourceViewerOpen(true);
  };

  return (
    <div className="flex flex-col h-full">
      {error && (
        <div className="p-4 flex-shrink-0">
          <ErrorMessage message={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {/* Messages area */}
      <div className="flex-1 overflow-y-auto px-4 py-6 space-y-4">
        {messages.length === 0 && (
          <div className="text-center text-gray-500 dark:text-gray-400 mt-8">
            <p className="text-base mb-2">👋 Ask me anything about your documents!</p>
            <p className="text-xs">Upload some documents and start chatting.</p>
          </div>
        )}

        {messages.map((message, index) => (
          <div
            key={index}
            className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-3xl rounded-lg p-3 ${
                message.role === 'user'
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-100 text-gray-900'
              }`}
            >
              {message.role === 'user' ? (
                <p className="text-sm whitespace-pre-wrap">{message.content}</p>
              ) : (
                <>
                  {/* Status message - shown while processing (Phase 1.2) */}
                  {message.statusMessage && !message.content && !message.quickSummary && (
                    <div className="flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
                      <span className="animate-pulse">{message.statusMessage}</span>
                    </div>
                  )}
                  
                  {/* Memory indicator - subtle tooltip when memory is used */}
                  {message.memoryUsed && message.memoryUsed.length > 0 && (
                    <div className="mb-2 flex items-center gap-1.5 group relative">
                      <span className="text-xs text-purple-500 dark:text-purple-400 flex items-center gap-1 cursor-help">
                        <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
                          <path d="M10 2a8 8 0 100 16 8 8 0 000-16zm0 14a6 6 0 110-12 6 6 0 010 12zm-1-5h2v2H9v-2zm0-6h2v4H9V5z"/>
                        </svg>
                        Using context from our conversations
                      </span>
                      {message.memoryContextSummary && (
                        <div className="absolute left-0 top-full mt-1 p-2 bg-gray-900 text-white text-xs rounded shadow-lg opacity-0 group-hover:opacity-100 transition-opacity z-10 max-w-xs whitespace-normal">
                          {message.memoryContextSummary}
                        </div>
                      )}
                    </div>
                  )}
                  
                  {/* Quick Summary - only show if Detailed Answer is substantially longer */}
                  {message.quickSummary && message.content && 
                   message.content.length > message.quickSummary.length * 1.5 && (
                    <div className="mb-3 pb-3 border-b border-gray-200 dark:border-gray-600">
                      <div className="flex items-center gap-1.5 mb-1.5">
                        <span className="text-xs font-semibold text-blue-600 dark:text-blue-400">⚡ Quick Answer</span>
                      </div>
                      {renderMessageWithCitations(message.quickSummary, message.citations)}
                    </div>
                  )}
                  
                  {/* Detailed Answer - show with header only if Quick Answer is also shown */}
                  {message.content && (
                    <>
                      {message.quickSummary && message.content.length > message.quickSummary.length * 1.5 && (
                        <div className="flex items-center gap-1.5 mb-1.5">
                          <span className="text-xs font-semibold text-gray-500 dark:text-gray-400">📝 Detailed Answer</span>
                        </div>
                      )}
                      {renderMessageWithCitations(message.content, message.citations)}
                    </>
                  )}
                  
                  {message.citations && message.citations.length > 0 && (
                    <CitationList citations={message.citations} onViewSource={handleViewSource} />
                  )}
                  {message.web_sources && message.web_sources.length > 0 && (
                    <div className="mt-2 pt-2 border-t border-gray-200 dark:border-gray-700">
                      <p className="text-xs font-semibold text-gray-600 dark:text-gray-400 mb-1.5">Web Sources:</p>
                      <div className="space-y-2">
                        {message.web_sources.map((source, idx) => (
                          <a
                            key={idx}
                            href={source.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="block p-2 bg-blue-50 dark:bg-blue-900/20 rounded text-xs hover:bg-blue-100 dark:hover:bg-blue-900/30 transition-colors"
                          >
                            <div className="flex items-start gap-2">
                              <span className="text-base">🌐</span>
                              <div className="flex-1">
                                <p className="font-medium text-blue-700 dark:text-blue-400">{source.title}</p>
                                <p className="text-gray-600 dark:text-gray-400 mt-1">{source.snippet}</p>
                                <p className="text-gray-500 dark:text-gray-500 mt-1 truncate">{source.url}</p>
                              </div>
                            </div>
                          </a>
                        ))}
                      </div>
                    </div>
                  )}
                  {message.follow_up_questions && message.follow_up_questions.length > 0 && (
                    <div className="mt-2 pt-2 border-t border-gray-200 dark:border-gray-700">
                      <p className="text-xs font-semibold text-gray-600 dark:text-gray-400 mb-1.5">💡 Follow-up Questions:</p>
                      <div className="flex flex-wrap gap-1.5">
                        {message.follow_up_questions.map((question, idx) => (
                          <button
                            key={idx}
                            onClick={() => handleFollowUpQuestion(question)}
                            className="text-xs px-2.5 py-1 bg-purple-100 dark:bg-purple-800/40 text-purple-800 dark:text-purple-200 rounded-full hover:bg-purple-200 dark:hover:bg-purple-700/50 transition-colors border border-purple-300 dark:border-purple-600"
                          >
                            {question}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                  {message.lowConfidenceQuery && (
                    <div className="mt-3 pt-2 border-t border-gray-200 dark:border-gray-700">
                      <div className="flex gap-2">
                        <button
                          onClick={() => {
                            if (onOpenWebSearch) {
                              onOpenWebSearch(message.lowConfidenceQuery);
                            }
                          }}
                          className="px-3 py-1.5 bg-blue-600 text-white text-xs rounded-lg hover:bg-blue-700 transition-colors font-medium"
                        >
                          Yes, search the web
                        </button>
                        <button
                          onClick={() => {
                            // Remove the low confidence prompt from this message
                            setMessages(prev => prev.map(m => 
                              m === message ? { ...m, lowConfidenceQuery: undefined, content: m.content.replace(/\n\n---\n\n\*\*I found limited information.*$/, '') } : m
                            ));
                          }}
                          className="px-3 py-1.5 bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 text-xs rounded-lg hover:bg-gray-300 dark:hover:bg-gray-600 transition-colors font-medium"
                        >
                          No, continue
                        </button>
                      </div>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        ))}

        {/* Old loading indicator removed - now using statusMessage in the message bubble */}

        <div ref={messagesEndRef} />
      </div>

      {/* Input area */}
      <div className="border-t dark:border-gray-700 p-4 bg-white dark:bg-gray-800 flex-shrink-0">
        <form onSubmit={handleSubmit}>
          <div className="flex gap-2 items-center">
            {/* Quick/Deep Toggle - Rabbit vs Brain */}
            <div 
              className="relative flex items-center bg-gray-100 dark:bg-gray-700 rounded-full p-0.5"
              title={deepThink ? "Deep Think: Thorough analysis (slower)" : "Quick: Fast, concise responses"}
            >
              <button
                type="button"
                onClick={() => setDeepThink(false)}
                disabled={!notebookId || loading}
                className={`px-2.5 py-1.5 rounded-full text-lg transition-all ${
                  !deepThink
                    ? 'bg-white dark:bg-gray-600 shadow-sm'
                    : 'hover:bg-gray-200 dark:hover:bg-gray-600'
                } disabled:opacity-50`}
              >
                🐇
              </button>
              <button
                type="button"
                onClick={() => setDeepThink(true)}
                disabled={!notebookId || loading}
                className={`px-2.5 py-1.5 rounded-full text-lg transition-all ${
                  deepThink
                    ? 'bg-purple-500 shadow-sm'
                    : 'hover:bg-gray-200 dark:hover:bg-gray-600'
                } disabled:opacity-50`}
              >
                🧠
              </button>
            </div>
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={notebookId ? "Ask a question..." : "Select a notebook first"}
              title={deepThink ? "Deep Think mode: AI will analyze step-by-step for thorough answers" : "Quick mode: Fast, concise responses"}
              disabled={!notebookId || loading}
              className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100 dark:disabled:bg-gray-700 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
            />
            <Button
              type="submit"
              disabled={!input.trim() || !notebookId || loading}
            >
              Send
            </Button>
          </div>
        </form>
      </div>

      {/* Source Viewer Modal */}
      {sourceViewerOpen && selectedSource && notebookId && (
        <SourceNotesViewer
          notebookId={notebookId}
          sourceId={selectedSource.sourceId}
          sourceName={selectedSource.sourceName}
          initialSearchTerm={selectedSource.searchTerm}
          onClose={() => {
            setSourceViewerOpen(false);
            setSelectedSource(null);
          }}
        />
      )}

    </div>
  );
};
