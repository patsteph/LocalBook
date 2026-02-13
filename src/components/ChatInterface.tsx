import React, { useState, useRef, useEffect, useCallback } from 'react';
import { chatService } from '../services/chat';
import { explorationService } from '../services/exploration';
import { voiceService } from '../services/voice';
import { visualService } from '../services/visual';
import { findingsService } from '../services/findings';
import { ChatMessage, Citation as CitationType, InlineVisualData } from '../types';
import { API_BASE_URL } from '../services/api';
import { Button } from './shared/Button';
// LoadingSpinner removed - now using statusMessage in message bubble
import { ErrorMessage } from './shared/ErrorMessage';
import { Citation, CitationList } from './Citation';
import { SourceNotesViewer } from './SourceNotesViewer';
import { MermaidRenderer } from './shared/MermaidRenderer';
import { InlineVisual } from './visual';
import { BookmarkButton } from './shared/BookmarkButton';

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
  const [, setActiveDeepThink] = useState(false);  // Actual mode being used (may differ from toggle due to auto-upgrade)
  const messagesEndRef = useRef<HTMLDivElement>(null);
  
  // Voice input state
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);

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

  // Strip leading grouped citation block like "[1] [2] [3] [4] [5] [6]\n"
  // that LLM sometimes outputs before the actual answer
  const stripLeadingCitationBlock = (content: string): string => {
    // Match a line at the start that is ONLY citation numbers like [1] [2] [3]...
    // Followed by a newline, then the real content
    return content.replace(/^(\s*(\[\d+\]\s*)+)\n+/, '');
  };

  // Render message content with inline clickable citations and mermaid diagrams
  const renderMessageWithCitations = (content: string, citations?: CitationType[]) => {
    // First, detect and extract mermaid code blocks
    const mermaidBlockRegex = /```mermaid\s*([\s\S]*?)```/g;
    const segments: { type: 'text' | 'mermaid'; content: string }[] = [];
    let lastIndex = 0;
    let mermaidMatch;

    while ((mermaidMatch = mermaidBlockRegex.exec(content)) !== null) {
      // Add text before the mermaid block
      if (mermaidMatch.index > lastIndex) {
        segments.push({ type: 'text', content: content.substring(lastIndex, mermaidMatch.index) });
      }
      // Add the mermaid block
      segments.push({ type: 'mermaid', content: mermaidMatch[1].trim() });
      lastIndex = mermaidMatch.index + mermaidMatch[0].length;
    }

    // Add remaining text
    if (lastIndex < content.length) {
      segments.push({ type: 'text', content: content.substring(lastIndex) });
    }

    // If no mermaid blocks and no citations, return simple text
    if (segments.length === 1 && segments[0].type === 'text' && (!citations || citations.length === 0)) {
      return <p className="text-sm whitespace-pre-wrap">{content}</p>;
    }

    // Render each segment
    return (
      <div className="space-y-3">
        {segments.map((segment, segIndex) => {
          if (segment.type === 'mermaid') {
            return (
              <div key={segIndex} className="my-3">
                <MermaidRenderer code={segment.content} className="border border-gray-200 dark:border-gray-600 rounded-lg" />
              </div>
            );
          }

          // For text segments, handle citations
          const textContent = segment.content;
          if (!citations || citations.length === 0) {
            return textContent.trim() ? (
              <p key={segIndex} className="text-sm whitespace-pre-wrap">{textContent}</p>
            ) : null;
          }

          // Parse citations within text
          const parts: (string | React.ReactElement)[] = [];
          let textLastIndex = 0;
          const citationRegex = /\[(\d+)\]/g;
          let match;

          while ((match = citationRegex.exec(textContent)) !== null) {
            const citationNumber = parseInt(match[1], 10);
            const citation = citations.find(c => c.number === citationNumber);

            if (match.index > textLastIndex) {
              parts.push(textContent.substring(textLastIndex, match.index));
            }

            if (citation) {
              parts.push(<Citation key={`cite-${segIndex}-${citationNumber}-${match.index}`} citation={citation} onViewSource={handleViewSource} />);
            } else {
              parts.push(match[0]);
            }

            textLastIndex = match.index + match[0].length;
          }

          if (textLastIndex < textContent.length) {
            parts.push(textContent.substring(textLastIndex));
          }

          return parts.length > 0 ? (
            <p key={segIndex} className="text-sm whitespace-pre-wrap">
              {parts.map((part, i) =>
                typeof part === 'string' ? <span key={i}>{part}</span> : part
              )}
            </p>
          ) : null;
        })}
      </div>
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
          onRetrievalStart: (queryAnalysis) => {
            // v1.1.0: Show what the system understood from the query
            const entities = queryAnalysis.entities?.length > 0 
              ? `Looking for: ${queryAnalysis.entities.slice(0, 3).join(', ')}` 
              : '';
            if (entities) {
              setMessages((prev) => {
                const updated = [...prev];
                const lastIdx = updated.length - 1;
                if (updated[lastIdx]?.role === 'assistant') {
                  updated[lastIdx] = {
                    ...updated[lastIdx],
                    statusMessage: `🔍 ${entities}...`,
                  };
                }
                return updated;
              });
            }
          },
          onRetrievalProgress: (progress) => {
            // v1.1.0: Show retrieval progress with strategy info
            const strategyLabel = progress.strategies_tried.length > 1 
              ? `(tried ${progress.strategies_tried.length} strategies)` 
              : '';
            setMessages((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              if (updated[lastIdx]?.role === 'assistant') {
                updated[lastIdx] = {
                  ...updated[lastIdx],
                  statusMessage: `📄 Found ${progress.chunks_found} relevant sections ${strategyLabel}`,
                };
              }
              return updated;
            });
          },
          onCitations: (citations, _sources, lowConfidence) => {
            currentCitations = citations;
            isLowConfidence = lowConfidence;
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
                // Strip leading grouped citation block and trailing reference lists
                let finalContent = stripLeadingCitationBlock(stripTrailingCitations(currentContent));
                let lowConfidenceQuery: string | undefined;
                
                // Detect low confidence from flag OR from response content patterns
                const contentLower = finalContent.toLowerCase();
                const hasNoAnswerPattern = 
                  contentLower.includes("couldn't find") ||
                  contentLower.includes("could not find") ||
                  contentLower.includes("can't find") ||
                  contentLower.includes("cannot find") ||
                  contentLower.includes("no relevant information") ||
                  contentLower.includes("don't have enough") ||
                  contentLower.includes("unable to find");
                
                const effectiveLowConfidence = isLowConfidence || hasNoAnswerPattern;
                
                if (effectiveLowConfidence) {
                  // Don't duplicate the message if it's already there
                  if (!contentLower.includes("would you like me to search")) {
                    finalContent += "\n\n---\n\n**Would you like me to search the web for more information?**";
                  }
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
            // Use the effective low confidence check from the message update above
            const contentForCheck = stripTrailingCitations(currentContent).toLowerCase();
            const effectiveIsLow = isLowConfidence || 
              contentForCheck.includes("couldn't find") ||
              contentForCheck.includes("could not find") ||
              contentForCheck.includes("can't find") ||
              contentForCheck.includes("no relevant information");
            const confidence = effectiveIsLow ? 0.3 : 0.7;
            explorationService.recordQuery(
              notebookId!,
              currentQuestion,
              topics.slice(0, 5),
              sourceIds,
              confidence,
              currentContent.slice(0, 200)
            ).catch(err => console.error('Failed to record query:', err));

            // Curator overwatch: check for cross-notebook insights (fire and forget)
            if (notebookId && currentContent.length > 50) {
              fetch(`${API_BASE_URL}/curator/overwatch`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  query: currentQuestion,
                  answer: currentContent.slice(0, 500),
                  notebook_id: notebookId,
                }),
              })
                .then(r => r.ok ? r.json() : null)
                .then(data => {
                  if (data?.aside) {
                    setMessages(prev => {
                      const updated = [...prev];
                      // Find the last assistant message (the one we just finalized)
                      for (let i = updated.length - 1; i >= 0; i--) {
                        if (updated[i].role === 'assistant' && updated[i].content) {
                          updated[i] = {
                            ...updated[i],
                            curatorAside: data.aside,
                            curatorName: data.curator_name || 'Curator',
                          };
                          break;
                        }
                      }
                      return updated;
                    });
                  }
                })
                .catch(() => {});
            }
            
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

  // Voice recording handlers
  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;
      audioChunksRef.current = [];

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };

      mediaRecorder.onstop = async () => {
        const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/webm' });
        stream.getTracks().forEach(track => track.stop());
        
        // Transcribe the audio
        setIsTranscribing(true);
        try {
          const result = await voiceService.transcribe(
            new File([audioBlob], 'recording.webm', { type: 'audio/webm' }),
            notebookId || '',
            undefined,
            false // Don't add as source, just transcribe
          );
          // Set the transcribed text as input
          setInput(prev => prev + (prev ? ' ' : '') + result.text);
        } catch (err) {
          console.error('Transcription failed:', err);
          setError('Failed to transcribe audio. Is Whisper running?');
        } finally {
          setIsTranscribing(false);
        }
      };

      mediaRecorder.start();
      setIsRecording(true);
    } catch (err) {
      console.error('Failed to start recording:', err);
      setError('Microphone access denied');
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
    }
  };

  // Generate inline visual for a message (with optional guidance for refinement, optional palette)
  const generateInlineVisual = useCallback(async (messageIndex: number, content: string, guidance?: string, palette?: string) => {
    if (!notebookId) return;
    
    // Mark as loading and clear previous alternatives
    const loadingMsg = guidance ? 'Analyzing your guidance...' : palette ? 'Applying new colors...' : 'Creating visual...';
    setMessages(prev => prev.map((m, i) => 
      i === messageIndex ? { ...m, visualLoading: true, visualLoadingMessage: loadingMsg, alternativeVisuals: [] } : m
    ));

    try {
      // Use streaming API for visual generation
      // Send FULL answer content - don't truncate on frontend
      // Backend handles extraction limits; pre-cache should have structure from full answer
      // For research use cases, answers can be very long with themes spread throughout
      await visualService.generateSmartStream(
        notebookId,
        content,  // Full content - backend decides what to extract
        palette || 'auto', // colorTheme - use selected palette or auto
        // onPrimary
        (diagram) => {
          // Convert to InlineVisualData format
          const visual: InlineVisualData = {
            id: `inline-${messageIndex}-${Date.now()}`,
            type: diagram.svg ? 'svg' : 'mermaid',
            code: diagram.svg || diagram.code || '',
            title: diagram.title || 'Visual',
            template_id: diagram.template_id,
            pattern: diagram.diagram_type,
            tagline: diagram.tagline,  // Editable summary line
          };
          
          setMessages(prev => prev.map((m, i) => 
            i === messageIndex ? { ...m, inlineVisual: visual, visualLoading: false } : m
          ));
        },
        // onAlternative - collect alternative visuals
        (diagram) => {
          const altVisual: InlineVisualData = {
            id: `alt-${messageIndex}-${Date.now()}-${Math.random().toString(36).substr(2, 5)}`,
            type: diagram.svg ? 'svg' : 'mermaid',
            code: diagram.svg || diagram.code || '',
            title: diagram.title || 'Alternative',
            template_id: diagram.template_id,
            pattern: diagram.diagram_type,
            tagline: diagram.tagline,  // Editable summary line
          };
          
          setMessages(prev => prev.map((m, i) => 
            i === messageIndex 
              ? { ...m, alternativeVisuals: [...(m.alternativeVisuals || []), altVisual].slice(0, 3) }
              : m
          ));
        },
        // onDone
        () => {
          setMessages(prev => prev.map((m, i) => 
            i === messageIndex ? { ...m, visualLoading: false } : m
          ));
        },
        // onError
        (err: string) => {
          console.error('Inline visual generation failed:', err);
          setMessages(prev => prev.map((m, i) => 
            i === messageIndex ? { ...m, visualLoading: false } : m
          ));
        },
        undefined, // templateId
        guidance   // user refinement guidance
      );
    } catch (err) {
      console.error('Failed to generate inline visual:', err);
      setMessages(prev => prev.map((m, i) => 
        i === messageIndex ? { ...m, visualLoading: false } : m
      ));
    }
  }, [notebookId]);

  // Open visual in Studio for full editing
  const openVisualInStudio = useCallback((content: string) => {
    sessionStorage.setItem('visualContent', content.substring(0, 2000));
    window.dispatchEvent(new CustomEvent('openStudioVisual', { 
      detail: { content: content.substring(0, 2000) } 
    }));
  }, []);

  // Save visual to Findings
  const saveVisualToFindings = useCallback(async (visual: InlineVisualData) => {
    if (!notebookId || !visual) return;
    
    try {
      await findingsService.saveVisual(
        notebookId,
        visual.title || 'Saved Visual',
        {
          type: visual.type,
          code: visual.code,
          template_id: visual.template_id,
        }
      );
      console.log('[Chat] Visual saved to Findings');
      // Dispatch event to refresh Findings panel
      window.dispatchEvent(new CustomEvent('findingsUpdated'));
    } catch (err) {
      console.error('Failed to save visual:', err);
    }
  }, [notebookId]);

  // Export visual as PNG or SVG
  const exportVisual = useCallback(async (visual: InlineVisualData, format: 'png' | 'svg') => {
    if (!visual || !visual.code) return;

    const filename = `${visual.title || 'visual'}-${Date.now()}`;

    if (format === 'svg') {
      // For SVG: Get the rendered SVG from the DOM or use the code directly
      let svgContent = visual.code;
      
      // If it's mermaid, we need to get the rendered SVG from the DOM
      if (visual.type === 'mermaid') {
        const svgElement = document.querySelector('.mermaid svg');
        if (svgElement) {
          svgContent = svgElement.outerHTML;
        }
      }
      
      // Create blob and download
      const blob = new Blob([svgContent], { type: 'image/svg+xml' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${filename}.svg`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      console.log('[Chat] Exported visual as SVG');
    } else {
      // For PNG: Render SVG to canvas then export
      let svgContent = visual.code;
      
      if (visual.type === 'mermaid') {
        const svgElement = document.querySelector('.mermaid svg');
        if (svgElement) {
          svgContent = svgElement.outerHTML;
        }
      }

      // Create an image from SVG
      const svgBlob = new Blob([svgContent], { type: 'image/svg+xml;charset=utf-8' });
      const url = URL.createObjectURL(svgBlob);
      const img = new Image();
      
      img.onload = () => {
        // Create canvas with proper dimensions
        const canvas = document.createElement('canvas');
        const scale = 2; // Higher resolution
        canvas.width = img.width * scale;
        canvas.height = img.height * scale;
        
        const ctx = canvas.getContext('2d');
        if (ctx) {
          // White background
          ctx.fillStyle = '#1e293b';
          ctx.fillRect(0, 0, canvas.width, canvas.height);
          ctx.scale(scale, scale);
          ctx.drawImage(img, 0, 0);
          
          // Export as PNG
          canvas.toBlob((blob) => {
            if (blob) {
              const pngUrl = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = pngUrl;
              a.download = `${filename}.png`;
              document.body.appendChild(a);
              a.click();
              document.body.removeChild(a);
              URL.revokeObjectURL(pngUrl);
              console.log('[Chat] Exported visual as PNG');
            }
          }, 'image/png');
        }
        URL.revokeObjectURL(url);
      };
      
      img.onerror = () => {
        console.error('Failed to load SVG for PNG export');
        URL.revokeObjectURL(url);
      };
      
      img.src = url;
    }
  }, []);

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
                  : 'bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-gray-100'
              }`}
            >
              {message.role === 'user' ? (
                <p className="text-sm whitespace-pre-wrap">{message.content}</p>
              ) : (
                <>
                  {/* Status message - shown while processing (Phase 1.2) */}
                  {message.statusMessage && !message.content && (
                    <div className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
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
                  
                  {/* Answer */}
                  {message.content && (
                    renderMessageWithCitations(message.content, message.citations)
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
                                <p className="text-gray-500 dark:text-gray-400 mt-1 truncate">{source.url}</p>
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
                  {/* Inline Visual - Canvas feature */}
                  {(message.inlineVisual || message.visualLoading) && (
                    <InlineVisual
                      visual={message.inlineVisual ? {
                        id: message.inlineVisual.id,
                        type: message.inlineVisual.type,
                        code: message.inlineVisual.code,
                        title: message.inlineVisual.title,
                        template_id: message.inlineVisual.template_id,
                        pattern: message.inlineVisual.pattern,
                        tagline: message.inlineVisual.tagline,
                      } : null}
                      alternatives={(message.alternativeVisuals || []).map(alt => ({
                        id: alt.id,
                        type: alt.type,
                        code: alt.code,
                        title: alt.title,
                        template_id: alt.template_id,
                        pattern: alt.pattern,
                      }))}
                      loading={message.visualLoading}
                      loadingMessage={message.visualLoadingMessage || 'Creating visual...'}
                      onSaveToFindings={message.inlineVisual ? () => saveVisualToFindings(message.inlineVisual!) : undefined}
                      onOpenInStudio={() => openVisualInStudio(message.content)}
                      onExport={message.inlineVisual ? (format) => exportVisual(message.inlineVisual!, format) : undefined}
                      onRegenerate={() => generateInlineVisual(index, message.content)}
                      onRegenerateWithGuidance={(guidance) => generateInlineVisual(index, message.content, guidance)}
                      onRegenerateWithPalette={(palette) => generateInlineVisual(index, message.content, undefined, palette)}
                      onSelectAlternative={(alt) => {
                        // Swap: move current primary to alternatives, make selected alt the primary
                        const currentPrimary = message.inlineVisual;
                        const remainingAlts = (message.alternativeVisuals || []).filter(a => a.id !== alt.id);
                        const newAlts = currentPrimary ? [currentPrimary, ...remainingAlts].slice(0, 3) : remainingAlts;
                        
                        setMessages(prev => prev.map((m, i) => 
                          i === index 
                            ? { 
                                ...m, 
                                inlineVisual: { 
                                  id: alt.id, 
                                  type: alt.type as 'svg' | 'mermaid', 
                                  code: alt.code, 
                                  title: alt.title, 
                                  template_id: alt.template_id, 
                                  pattern: alt.pattern 
                                },
                                alternativeVisuals: newAlts.map(a => ({
                                  id: a.id,
                                  type: a.type as 'svg' | 'mermaid',
                                  code: a.code,
                                  title: a.title,
                                  template_id: a.template_id,
                                  pattern: a.pattern,
                                }))
                              }
                            : m
                        ));
                      }}
                      onTaglineChange={(newTagline) => {
                        // Update tagline in message state
                        setMessages(prev => prev.map((m, i) => 
                          i === index && m.inlineVisual
                            ? { 
                                ...m, 
                                inlineVisual: { ...m.inlineVisual, tagline: newTagline }
                              }
                            : m
                        ));
                      }}
                    />
                  )}
                  
                  {/* Action buttons row - Create Visual & Bookmark */}
                  {message.content && message.content.length > 50 && (
                    <div className="mt-2 pt-2 border-t border-gray-200 dark:border-gray-700 flex items-center gap-2">
                      {/* Create Visual button - shows when no inline visual exists */}
                      {message.content.length > 100 && !message.inlineVisual && !message.visualLoading && (
                        <button
                          onClick={() => generateInlineVisual(index, message.content)}
                          className="text-xs px-2.5 py-1 bg-green-100 dark:bg-green-800/40 text-green-800 dark:text-green-200 rounded-full hover:bg-green-200 dark:hover:bg-green-700/50 transition-colors border border-green-300 dark:border-green-600 flex items-center gap-1"
                        >
                          🎨 Create Visual
                        </button>
                      )}
                      {/* Bookmark answer to Findings */}
                      {notebookId && (
                        <BookmarkButton
                          notebookId={notebookId}
                          type="answer"
                          title={message.content.substring(0, 60) + (message.content.length > 60 ? '...' : '')}
                          content={{
                            question: messages[index - 1]?.content || 'Research question',
                            answer: message.content,
                            citations: message.citations,
                          }}
                        />
                      )}
                    </div>
                  )}
                  {/* Curator Overwatch Aside */}
                  {message.curatorAside && (
                    <div className="mt-3 p-2.5 rounded-lg bg-indigo-50 dark:bg-indigo-900/20 border-l-3 border-indigo-500 dark:border-indigo-400">
                      <div className="flex items-center gap-1.5 mb-1">
                        <div className="w-4 h-4 rounded-full bg-indigo-600 dark:bg-indigo-500 flex items-center justify-center text-white text-[7px] font-bold flex-shrink-0">
                          {(message.curatorName || 'C').charAt(0).toUpperCase()}
                        </div>
                        <span className="text-[10px] font-semibold text-indigo-600 dark:text-indigo-400 uppercase tracking-wide">
                          {message.curatorName || 'Curator'}
                        </span>
                      </div>
                      <p className="text-xs text-indigo-800 dark:text-indigo-200 leading-relaxed">
                        {message.curatorAside}
                      </p>
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
                              m === message ? { ...m, lowConfidenceQuery: undefined, content: m.content.replace(/\n\n---\n\n\*\*(Would you like me to search|I found limited information).*$/s, '') } : m
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
              placeholder={notebookId ? (isTranscribing ? "Transcribing..." : "Ask a question...") : "Select a notebook first"}
              title={deepThink ? "Deep Think mode: AI will analyze step-by-step for thorough answers" : "Quick mode: Fast, concise responses"}
              disabled={!notebookId || loading || isTranscribing}
              className="flex-1 px-4 py-2 border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100 dark:disabled:bg-gray-700 bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
            />
            {/* Mic button for voice input */}
            <button
              type="button"
              onClick={isRecording ? stopRecording : startRecording}
              disabled={!notebookId || loading || isTranscribing}
              title={isRecording ? "Stop recording" : "Voice input (Whisper)"}
              className={`p-2 rounded-lg transition-colors ${
                isRecording 
                  ? 'bg-red-500 text-white animate-pulse' 
                  : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-600'
              } disabled:opacity-50`}
            >
              {isTranscribing ? (
                <svg className="w-5 h-5 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
              ) : (
                <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M7 4a3 3 0 016 0v4a3 3 0 11-6 0V4zm4 10.93A7.001 7.001 0 0017 8a1 1 0 10-2 0A5 5 0 015 8a1 1 0 00-2 0 7.001 7.001 0 006 6.93V17H6a1 1 0 100 2h8a1 1 0 100-2h-3v-2.07z" clipRule="evenodd" />
                </svg>
              )}
            </button>
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
