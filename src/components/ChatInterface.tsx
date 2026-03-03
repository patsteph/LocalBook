import React, { useState, useRef, useEffect, useMemo } from 'react';
import { Compass, Radio, Search, BookOpen, Upload, MessageCircle, Sparkles, Wand2 } from 'lucide-react';
import { chatService } from '../services/chat';
import { explorationService } from '../services/exploration';
import { voiceService } from '../services/voice';
import { ChatMessage, Citation as CitationType } from '../types';
import { curatorService } from '../services/curatorApi';
import { Button } from './shared/Button';
import { ErrorMessage } from './shared/ErrorMessage';
import { SourceNotesViewer } from './SourceNotesViewer';
import { ChatMessageBubble } from './chat/ChatMessageBubble';
import { useVisualActions } from './chat/useVisualActions';
import { WritingAssistBar } from './WritingAssistBar';
import { ChatActionBar } from './chat/ChatActionBar';
import { CanvasItemCard } from './chat/CanvasItemCard';
import { useCanvasItems, useAppShell } from './canvas/CanvasContext';
import { CanvasItem } from './canvas/types';

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
  const [showDeepThinkTip, setShowDeepThinkTip] = useState(() => !localStorage.getItem('lb-deepthink-tip-seen'));
  const [showWelcome, setShowWelcome] = useState(() => !localStorage.getItem('lb-welcome-seen'));
  const [showMentionMenu, setShowMentionMenu] = useState(false);
  const [mentionFilter, setMentionFilter] = useState('');
  const [mentionIndex, setMentionIndex] = useState(0);
  const [activeMention, setActiveMention] = useState<'curator' | 'collector' | 'research' | 'studio' | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  
  // Auto-dismiss welcome once a notebook is selected (user is no longer a first-timer)
  useEffect(() => {
    if (notebookId && showWelcome) {
      setShowWelcome(false);
      localStorage.setItem('lb-welcome-seen', '1');
    }
  }, [notebookId, showWelcome]);

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

  // Cleanup: stop MediaRecorder and release mic on unmount
  useEffect(() => {
    return () => {
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        mediaRecorderRef.current.stream.getTracks().forEach(track => track.stop());
        mediaRecorderRef.current.stop();
      }
    };
  }, []);

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
  // Per-notebook message cache — preserves chat history when switching between notebooks
  const messageCacheRef = useRef<Map<string, ChatMessage[]>>(new Map());
  const prevNotebookId = React.useRef(notebookId);
  useEffect(() => {
    if (notebookId && notebookId !== prevNotebookId.current) {
      // Save current notebook's messages before switching
      if (prevNotebookId.current && messages.length > 0) {
        messageCacheRef.current.set(prevNotebookId.current, messages);
      }
      // Restore target notebook's cached messages (or start fresh)
      const cached = messageCacheRef.current.get(notebookId);
      setMessages(cached || []);
      prevNotebookId.current = notebookId;
    }
  }, [notebookId]); // eslint-disable-line react-hooks/exhaustive-deps

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
      // Trailing --- followed by short stub (e.g. "---\n\nN." or "---\n1. Source")
      /\n\n---+\s*\n[\s\S]{0,40}$/,
      // Trailing --- with nothing after (or just whitespace)
      /\n\n---+\s*$/,
      // Standalone short reference stubs at end (e.g. "\n\nN." or "\n\n1.")
      /\n\n[A-Z0-9]\.\s*$/,
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

  // Old cycling loading messages removed - now using statusMessage from backend (Phase 1.2)

  // =========================================================================
  // @Mention Parser & Autocomplete
  // =========================================================================

  const MENTION_OPTIONS: { key: string; icon: React.ReactNode; desc: string }[] = [
    { key: 'curator', icon: <Compass className="w-3.5 h-3.5" />, desc: 'Cross-notebook synthesis' },
    { key: 'collector', icon: <Radio className="w-3.5 h-3.5" />, desc: 'Collection status & commands' },
    { key: 'research', icon: <Search className="w-3.5 h-3.5" />, desc: 'Web, site, or deep-dive research' },
    { key: 'studio', icon: <Wand2 className="w-3.5 h-3.5" />, desc: 'Create content from conversation' },
  ];

  const parseMention = (text: string): { target: string | null; cleanQuestion: string } => {
    // If activeMention is set, target comes from state — input is already clean
    if (activeMention) {
      return { target: activeMention, cleanQuestion: text.trim() };
    }
    // Fallback: parse manually typed @mention
    const match = text.match(/^@(curator|collector|research|studio)\s+([\s\S]*)$/i);
    if (match) {
      return { target: match[1].toLowerCase(), cleanQuestion: match[2].trim() };
    }
    return { target: null, cleanQuestion: text.trim() };
  };

  const handleInputChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    let val = e.target.value;

    // If user manually types @collector/@curator and space, promote to activeMention
    const manualMention = val.match(/^@(curator|collector|research|studio)\s+$/i);
    if (manualMention && !activeMention) {
      setActiveMention(manualMention[1].toLowerCase() as 'curator' | 'collector' | 'research' | 'studio');
      val = '';
    }

    setInput(val);

    // Auto-resize textarea
    const target = e.target;
    target.style.height = 'auto';
    target.style.height = Math.min(target.scrollHeight, 120) + 'px';

    // Show @mention menu when user types '@' at the start (and no activeMention)
    if (!activeMention && (val === '@' || val.match(/^@[a-z]*$/i))) {
      setShowMentionMenu(true);
      setMentionFilter(val.slice(1));
      setMentionIndex(0);
    } else {
      setShowMentionMenu(false);
    }
  };

  const handleInputKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (showMentionMenu) {
      const filtered = MENTION_OPTIONS.filter(opt => !mentionFilter || opt.key.includes(mentionFilter.toLowerCase()));
      if (e.key === 'Escape') {
        setShowMentionMenu(false);
        e.preventDefault();
        return;
      }
      if (e.key === 'ArrowDown') {
        setMentionIndex(prev => (prev + 1) % filtered.length);
        e.preventDefault();
        return;
      }
      if (e.key === 'ArrowUp') {
        setMentionIndex(prev => (prev - 1 + filtered.length) % filtered.length);
        e.preventDefault();
        return;
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        if (filtered.length > 0) {
          insertMention(filtered[mentionIndex]?.key || filtered[0].key);
        }
        e.preventDefault();
        return;
      }
    }
    // Backspace with empty input clears the activeMention pill
    if (e.key === 'Backspace' && !input && activeMention) {
      setActiveMention(null);
      e.preventDefault();
      return;
    }
    // Enter to submit, Shift+Enter for newline
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (input.trim() && notebookId && !loading) {
        handleSubmit(e as any);
      }
    }
  };

  const insertMention = (key: string) => {
    setActiveMention(key as 'curator' | 'collector' | 'research' | 'studio');
    setInput('');
    setShowMentionMenu(false);
    setMentionFilter('');
    inputRef.current?.focus();
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || !notebookId || loading) return;

    const { target, cleanQuestion } = parseMention(input);
    const currentQuestion = cleanQuestion;
    
    const userMessage: ChatMessage = {
      role: 'user',
      content: currentQuestion,
      timestamp: new Date(),
      ...(target ? { agentType: target as 'curator' | 'collector' | 'research' | 'studio' } : {}),
    };

    setMessages((prev) => [...prev, userMessage]);
    setInput('');
    setActiveMention(null);
    setShowMentionMenu(false);
    // Reset textarea height after clearing
    if (inputRef.current) inputRef.current.style.height = 'auto';
    setLoading(true);
    setError(null);

    // Create a placeholder for the streaming response
    const streamingMessage: ChatMessage = {
      role: 'assistant',
      content: '',
      citations: [],
      timestamp: new Date(),
      ...(target ? { agentType: target as 'curator' | 'collector' | 'research' | 'studio' } : {}),
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
          target: target || undefined,
          ...(target === 'studio' ? { chat_context: messages.slice(-8).map(m => `${m.role === 'user' ? 'User' : 'Assistant'}: ${m.content.slice(0, 400)}`).join('\n\n').slice(0, 3000) } : {}),
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
          onReplaceAnswer: (content) => {
            // CaRR retry: replace the entire answer with the verified version
            currentContent = content;
            tokenBuffer = '';
            setMessages((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              if (updated[lastIdx]?.role === 'assistant') {
                updated[lastIdx] = {
                  ...updated[lastIdx],
                  content,
                  citations: currentCitations,
                };
              }
              return updated;
            });
          },
          onResearchResults: (results) => {
            // @research: attach structured results for approval UI
            setMessages((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              if (updated[lastIdx]?.role === 'assistant') {
                updated[lastIdx] = {
                  ...updated[lastIdx],
                  researchResults: results,
                };
              }
              return updated;
            });
          },
          onFollowUpQuestions: (questions) => {
            // Show follow-up questions immediately (decoupled from CaRR/done)
            if (questions.length > 0) {
              setMessages((prev) => {
                const updated = [...prev];
                const lastIdx = updated.length - 1;
                if (updated[lastIdx]?.role === 'assistant') {
                  updated[lastIdx] = {
                    ...updated[lastIdx],
                    follow_up_questions: questions,
                  };
                }
                return updated;
              });
            }
          },
          onDone: (followUpQuestions, curatorName, agentName, agentType) => {
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
                  ...(curatorName ? { curatorName } : {}),
                  ...(agentName ? { agentName } : {}),
                  ...(agentType ? { agentType: agentType as 'curator' | 'collector' | 'research' | 'studio' } : {}),
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
              curatorService.overwatch(notebookId, currentQuestion, currentContent.slice(0, 500))
                .then(data => {
                  if (data?.aside) {
                    setMessages(prev => {
                      const updated = [...prev];
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

  // Visual actions (generate, save, export, open in studio)
  const { generateInlineVisual, openVisualInStudio, saveVisualToFindings, exportVisual } = useVisualActions(notebookId, setMessages);

  // Update shared chat context for "From Chat" mode in Studio
  const { setChatContext } = useAppShell();
  useEffect(() => {
    if (messages.length === 0) {
      setChatContext('');
      return;
    }
    // Extract last 8 turns as context (truncate to ~3000 chars)
    const recentMsgs = messages.slice(-8);
    const contextStr = recentMsgs
      .map(m => `${m.role === 'user' ? 'User' : 'Assistant'}: ${m.content.slice(0, 400)}`)
      .join('\n\n');
    setChatContext(contextStr.slice(0, 3000));
  }, [messages, setChatContext]);

  // Canvas items — scoped to current notebook
  const canvasCtx = useCanvasItems();
  const notebookCanvasItems = useMemo(() =>
    canvasCtx.canvasItems.filter(item =>
      !item.metadata?.notebookId || item.metadata.notebookId === notebookId
    ),
    [canvasCtx.canvasItems, notebookId]
  );
  // Auto-scroll when canvas items are added
  useEffect(() => {
    if (notebookCanvasItems.length > 0) scrollToBottom();
  }, [notebookCanvasItems.length]); // eslint-disable-line react-hooks/exhaustive-deps

  // Unified chronological timeline — interleave messages and canvas items by timestamp
  const timeline = useMemo(() => {
    const items: Array<{ type: 'message'; index: number; ts: number } | { type: 'canvas'; item: CanvasItem; ts: number }> = [];
    messages.forEach((msg, index) => {
      items.push({ type: 'message', index, ts: msg.timestamp ? msg.timestamp.getTime() : 0 });
    });
    notebookCanvasItems.forEach((item) => {
      items.push({ type: 'canvas', item, ts: item.timestamp || 0 });
    });
    items.sort((a, b) => a.ts - b.ts);
    return items;
  }, [messages, notebookCanvasItems]);
  const [actionBarExpanded, setActionBarExpanded] = useState(() => {
    const saved = localStorage.getItem('lb-action-bar-expanded');
    return saved !== null ? saved === '1' : true;
  });
  const toggleActionBar = () => {
    setActionBarExpanded(prev => {
      localStorage.setItem('lb-action-bar-expanded', !prev ? '1' : '0');
      return !prev;
    });
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
        {messages.length === 0 && notebookCanvasItems.length === 0 && (
          <div className="flex flex-col items-center justify-center text-center px-6 py-12 max-w-md mx-auto animate-fade-in">
            {!notebookId && showWelcome ? (
              <>
                <div className="w-12 h-12 rounded-full bg-blue-50 dark:bg-blue-900/30 flex items-center justify-center mb-4">
                  <BookOpen className="w-6 h-6 text-blue-500 dark:text-blue-400" />
                </div>
                <h3 className="text-base font-semibold text-gray-800 dark:text-white mb-2">Welcome to LocalBook</h3>
                <p className="text-sm text-gray-500 dark:text-gray-400 mb-5">Get started in three steps:</p>
                <div className="w-full space-y-3 text-left">
                  <div className="flex items-start gap-3 p-2.5 rounded-lg bg-gray-50 dark:bg-gray-800/60">
                    <span className="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400 text-xs font-bold flex items-center justify-center mt-0.5">1</span>
                    <div>
                      <p className="text-sm font-medium text-gray-700 dark:text-gray-300">Create a notebook</p>
                      <p className="text-xs text-gray-400">Use the sidebar to create your first notebook</p>
                    </div>
                  </div>
                  <div className="flex items-start gap-3 p-2.5 rounded-lg bg-gray-50 dark:bg-gray-800/60">
                    <span className="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400 text-xs font-bold flex items-center justify-center mt-0.5">2</span>
                    <div>
                      <p className="text-sm font-medium text-gray-700 dark:text-gray-300">Add sources</p>
                      <p className="text-xs text-gray-400">Upload PDFs, paste text, or use the Collector</p>
                    </div>
                  </div>
                  <div className="flex items-start gap-3 p-2.5 rounded-lg bg-gray-50 dark:bg-gray-800/60">
                    <span className="flex-shrink-0 w-5 h-5 rounded-full bg-blue-100 dark:bg-blue-900/40 text-blue-600 dark:text-blue-400 text-xs font-bold flex items-center justify-center mt-0.5">3</span>
                    <div>
                      <p className="text-sm font-medium text-gray-700 dark:text-gray-300">Start learning</p>
                      <p className="text-xs text-gray-400">Ask questions, generate visuals, or create study audio</p>
                    </div>
                  </div>
                </div>
                <button
                  onClick={() => { setShowWelcome(false); localStorage.setItem('lb-welcome-seen', '1'); }}
                  className="mt-5 text-xs text-blue-500 hover:text-blue-600 dark:text-blue-400 dark:hover:text-blue-300 font-medium"
                >
                  Dismiss
                </button>
              </>
            ) : !notebookId ? (
              <>
                <div className="w-12 h-12 rounded-full bg-gray-100 dark:bg-gray-700/60 flex items-center justify-center mb-4">
                  <BookOpen className="w-6 h-6 text-gray-400 dark:text-gray-500" />
                </div>
                <h3 className="text-base font-semibold text-gray-800 dark:text-white mb-1">Select a notebook</h3>
                <p className="text-sm text-gray-500 dark:text-gray-400">Choose a notebook from the sidebar to start chatting</p>
              </>
            ) : (
              <>
                <div className="w-12 h-12 rounded-full bg-gradient-to-br from-blue-50 to-indigo-50 dark:from-blue-900/30 dark:to-indigo-900/30 flex items-center justify-center mb-4">
                  <MessageCircle className="w-6 h-6 text-blue-500 dark:text-blue-400" />
                </div>
                <h3 className="text-base font-semibold text-gray-800 dark:text-white mb-1">Ready to chat</h3>
                <p className="text-sm text-gray-500 dark:text-gray-400 mb-5">Ask anything about your documents</p>
                <div className="flex flex-wrap justify-center gap-2">
                  <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-gray-100 dark:bg-gray-700/60 text-xs text-gray-600 dark:text-gray-400">
                    <Upload className="w-3 h-3" /> Upload sources in sidebar
                  </span>
                  <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-gray-100 dark:bg-gray-700/60 text-xs text-gray-600 dark:text-gray-400">
                    <Sparkles className="w-3 h-3" /> Try @curator or @collector
                  </span>
                </div>
              </>
            )}
          </div>
        )}

        {/* Unified chronological timeline — messages and canvas items interleaved by timestamp */}
        {timeline.map((entry) => {
          if (entry.type === 'canvas') {
            return <CanvasItemCard key={entry.item.id} item={entry.item} />;
          }
          const index = entry.index;
          const message = messages[index];
          if (!message) return null;
          return (
            <ChatMessageBubble
              key={`msg-${index}`}
              message={message}
              index={index}
              previousMessage={index > 0 ? messages[index - 1] : undefined}
              notebookId={notebookId}
              onFollowUp={handleFollowUpQuestion}
              onViewSource={handleViewSource}
              onGenerateVisual={generateInlineVisual}
              onSaveVisual={saveVisualToFindings}
              onOpenInStudio={openVisualInStudio}
              onExportVisual={exportVisual}
              onSelectAlternative={(idx, alt) => {
                const msg = messages[idx];
                const currentPrimary = msg.inlineVisual;
                const remainingAlts = (msg.alternativeVisuals || []).filter(a => a.id !== alt.id);
                const newAlts = currentPrimary ? [currentPrimary, ...remainingAlts].slice(0, 3) : remainingAlts;
                
                setMessages(prev => prev.map((m, mi) => 
                  mi === idx 
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
              onTaglineChange={(idx, newTagline) => {
                setMessages(prev => prev.map((m, mi) => 
                  mi === idx && m.inlineVisual
                    ? { ...m, inlineVisual: { ...m.inlineVisual, tagline: newTagline } }
                    : m
                ));
              }}
              onDismissLowConfidence={(msg) => {
                setMessages(prev => prev.map(m => 
                  m === msg ? { ...m, lowConfidenceQuery: undefined, content: m.content.replace(/\n\n---\n\n\*\*(Would you like me to search|I found limited information).*$/s, '') } : m
                ));
              }}
              onOpenWebSearch={onOpenWebSearch}
              onAddResearchResult={async (result) => {
                if (!notebookId) return;
                try {
                  const API_BASE = (await import('../services/api')).API_BASE_URL;
                  const resp = await fetch(`${API_BASE}/web/quick-add`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                      notebook_id: notebookId,
                      url: result.url,
                      title: result.title,
                    }),
                  });
                  if (resp.ok) {
                    setMessages(prev => [...prev, {
                      role: 'assistant',
                      content: `Added **[${result.title}](${result.url})** to your notebook sources.`,
                      agentType: 'research' as const,
                      agentName: 'Research',
                      timestamp: new Date(),
                    }]);
                  }
                } catch (err) {
                  console.error('Failed to add research result:', err);
                }
              }}
            />
          );
        })}

        <div ref={messagesEndRef} />
      </div>

      {/* Studio action bar — pills + popovers */}
      <ChatActionBar
        notebookId={notebookId}
        expanded={actionBarExpanded}
        onToggleExpand={toggleActionBar}
      />

      {/* Input area */}
      <div className="border-t dark:border-gray-700 px-2.5 py-1.5 bg-white dark:bg-gray-800 flex-shrink-0">
        <form onSubmit={handleSubmit}>
          <div className="flex gap-1.5 items-center">
            {/* Quick/Deep Toggle - Rabbit vs Brain */}
            <div 
              className="relative flex items-center bg-gray-100 dark:bg-gray-700 rounded-full p-0.5 h-9"
              title={deepThink ? "Deep Think: Thorough analysis (slower)" : "Quick: Fast, concise responses"}
            >
              {showDeepThinkTip && (
                <div className="absolute bottom-full left-0 mb-2 w-52 p-2.5 bg-gray-900 dark:bg-gray-700 text-white text-xs rounded-lg shadow-lg z-50 animate-slide-up">
                  <p className="font-medium mb-1">Response Mode</p>
                  <p className="text-gray-300"><span className="text-base">🐇</span> <strong>Quick</strong> — Fast, concise answers</p>
                  <p className="text-gray-300 mt-0.5"><span className="text-base">🧠</span> <strong>Deep Think</strong> — Step-by-step analysis</p>
                  <button
                    type="button"
                    onClick={() => { setShowDeepThinkTip(false); localStorage.setItem('lb-deepthink-tip-seen', '1'); }}
                    className="mt-1.5 text-blue-400 hover:text-blue-300 font-medium"
                  >
                    Got it
                  </button>
                  <div className="absolute top-full left-4 -mt-px border-4 border-transparent border-t-gray-900 dark:border-t-gray-700" />
                </div>
              )}
              <button
                type="button"
                onClick={() => setDeepThink(false)}
                disabled={!notebookId || loading}
                className={`px-2 py-1.5 rounded-full text-sm transition-all ${
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
                className={`px-2 py-1.5 rounded-full text-sm transition-all ${
                  deepThink
                    ? 'bg-purple-500 shadow-sm'
                    : 'hover:bg-gray-200 dark:hover:bg-gray-600'
                } disabled:opacity-50`}
              >
                🧠
              </button>
            </div>
            {input.trim().length > 20 && (
              <WritingAssistBar
                text={input}
                onReplace={(newText) => setInput(newText)}
                compact
                className="mr-2"
              />
            )}
            <div className="relative flex-1">
              {/* @mention active target pill */}
              {activeMention && (
                <span className={`absolute left-3 top-1/2 -translate-y-1/2 text-xs font-semibold px-1.5 py-0.5 rounded-lg z-10 flex items-center gap-1 cursor-pointer ${
                  activeMention === 'curator'
                    ? 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300'
                    : activeMention === 'research'
                      ? 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300'
                      : activeMention === 'studio'
                        ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'
                        : 'bg-teal-100 text-teal-700 dark:bg-teal-900/40 dark:text-teal-300'
                }`} onClick={() => setActiveMention(null)} title="Click or press Backspace to remove">
                  {activeMention === 'collector' ? <Radio className="w-3 h-3" /> : activeMention === 'research' ? <Search className="w-3 h-3" /> : activeMention === 'studio' ? <Wand2 className="w-3 h-3" /> : <Compass className="w-3 h-3" />}
                  @{activeMention}
                </span>
              )}
              <textarea
                ref={inputRef}
                value={input}
                onChange={handleInputChange}
                onKeyDown={handleInputKeyDown}
                placeholder={notebookId ? (isTranscribing ? "Transcribing..." : "Ask a question... (type @ for agents)") : "Select a notebook first"}
                title={deepThink ? "Deep Think mode: AI will analyze step-by-step for thorough answers" : "Quick mode: Fast, concise responses"}
                disabled={!notebookId || loading || isTranscribing}
                rows={1}
                className={`w-full h-9 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500 disabled:bg-gray-100 dark:disabled:bg-gray-700 bg-white dark:bg-gray-700 text-gray-900 dark:text-white resize-none max-h-[100px] ${
                  activeMention ? 'pl-[7.5rem] pr-4' : 'px-4'
                }`}
              />
              {/* @mention autocomplete dropdown */}
              {showMentionMenu && (
                <div className="absolute bottom-full left-0 mb-1 w-64 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg z-50 py-1 overflow-hidden">
                  <div className="px-3 py-1 text-xs font-semibold text-gray-400 uppercase tracking-wider">Agents</div>
                  {MENTION_OPTIONS
                    .filter(opt => !mentionFilter || opt.key.includes(mentionFilter.toLowerCase()))
                    .map((opt, idx) => (
                      <button
                        key={opt.key}
                        type="button"
                        onMouseDown={(e) => { e.preventDefault(); insertMention(opt.key); }}
                        onMouseEnter={() => setMentionIndex(idx)}
                        className={`w-full text-left px-3 py-2 text-sm flex items-center gap-2 transition-colors ${
                          idx === mentionIndex
                            ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300'
                            : 'hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300'
                        }`}
                      >
                        <span className="text-base">{opt.icon}</span>
                        <div>
                          <span className="font-medium">@{opt.key}</span>
                          <span className="text-xs text-gray-400 ml-2">{opt.desc}</span>
                        </div>
                      </button>
                    ))}
                </div>
              )}
            </div>
            {/* Mic button for voice input */}
            <button
              type="button"
              onClick={isRecording ? stopRecording : startRecording}
              disabled={!notebookId || loading || isTranscribing}
              title={isRecording ? "Stop recording" : "Voice input (Whisper)"}
              className={`h-9 w-9 flex items-center justify-center rounded-lg transition-colors ${
                isRecording 
                  ? 'bg-red-500 text-white animate-pulse' 
                  : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-200 dark:hover:bg-gray-600'
              } disabled:opacity-50`}
            >
              {isTranscribing ? (
                <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
              ) : (
                <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M7 4a3 3 0 016 0v4a3 3 0 11-6 0V4zm4 10.93A7.001 7.001 0 0017 8a1 1 0 10-2 0A5 5 0 015 8a1 1 0 00-2 0 7.001 7.001 0 006 6.93V17H6a1 1 0 100 2h8a1 1 0 100-2h-3v-2.07z" clipRule="evenodd" />
                </svg>
              )}
            </button>
            <Button
              type="submit"
              size="sm"
              disabled={!input.trim() || !notebookId || loading}
              className="h-9 px-4"
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
