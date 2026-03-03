import React, { useState, useRef, useEffect, useCallback } from 'react';
import DOMPurify from 'dompurify';
import {
  FileText, Palette, Target, Mic, MessageSquare, PenLine,
  BookOpen, ChevronDown, X, Video,
} from 'lucide-react';
import { useCanvas } from '../canvas/CanvasContext';
import { CanvasItem } from '../canvas/types';
import ReactMarkdown from 'react-markdown';
import { MermaidRenderer } from '../shared/MermaidRenderer';
import { SVGRenderer } from '../shared/SVGRenderer';
import { FeynmanQuizBlock, FeynmanAudioBlock, isFeynmanBlock } from '../shared/FeynmanBlocks';
import { WritingAssistBar } from '../WritingAssistBar';
import { sourceService } from '../../services/sources';
import { voiceService } from '../../services/voice';
import { settingsService } from '../../services/settings';
import { AudioCanvasPlayer } from './AudioCanvasPlayer';
import { API_BASE_URL } from '../../services/api';

// ─── Type icons ────────────────────────────────────────────────────────────
const iconSm = 'w-3.5 h-3.5';
const TYPE_ICONS: Record<CanvasItem['type'], React.ReactNode> = {
  'document': <FileText className={iconSm} />,
  'visual': <Palette className={iconSm} />,
  'quiz': <Target className={iconSm} />,
  'audio': <Mic className={iconSm} />,
  'video': <Video className={iconSm} />,
  'chat-response': <MessageSquare className={iconSm} />,
  'note': <PenLine className={iconSm} />,
};

// ═══════════════════════════════════════════════════════════════════════════
// Note Editor — inline editable note (adapted from NoteCanvasEditor)
// ═══════════════════════════════════════════════════════════════════════════
const NoteEditor: React.FC<{ item: CanvasItem }> = ({ item }) => {
  const ctx = useCanvas();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [saving, setSaving] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [selectedText, setSelectedText] = useState('');
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);

  const undoStackRef = useRef<string[]>([]);
  const redoStackRef = useRef<string[]>([]);
  const snapshotTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const scheduleSnapshot = useCallback((currentContent: string) => {
    if (snapshotTimerRef.current) clearTimeout(snapshotTimerRef.current);
    snapshotTimerRef.current = setTimeout(() => {
      const stack = undoStackRef.current;
      if (stack.length === 0 || stack[stack.length - 1] !== currentContent) {
        stack.push(currentContent);
        if (stack.length > 50) stack.shift();
        redoStackRef.current = [];
      }
    }, 1000);
  }, []);

  useEffect(() => {
    if (item.content) undoStackRef.current = [item.content];
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    return () => {
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        mediaRecorderRef.current.stream.getTracks().forEach(track => track.stop());
        mediaRecorderRef.current.stop();
      }
    };
  }, []);

  useEffect(() => {
    if (!item.content && !item.title) {
      const dateStr = new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
      settingsService.getUserProfile()
        .then(profile => {
          const name = profile.name?.trim();
          ctx.updateCanvasItem(item.id, { title: name ? `${name}'s Note — ${dateStr}` : `Note — ${dateStr}` });
        })
        .catch(() => ctx.updateCanvasItem(item.id, { title: `Note — ${dateStr}` }));
      setTimeout(() => textareaRef.current?.focus(), 200);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleTextSelect = useCallback(() => {
    const ta = textareaRef.current;
    if (!ta) return;
    setSelectedText(ta.value.substring(ta.selectionStart, ta.selectionEnd));
  }, []);

  const handleWritingReplace = useCallback((newText: string, replaceSelection: boolean) => {
    const ta = textareaRef.current;
    if (!ta) return;
    if (replaceSelection && selectedText) {
      const start = ta.selectionStart;
      const end = ta.selectionEnd;
      const updated = item.content.substring(0, start) + newText + item.content.substring(end);
      ctx.updateCanvasItem(item.id, { content: updated });
      setSelectedText('');
    } else {
      ctx.updateCanvasItem(item.id, { content: newText });
    }
  }, [item.id, item.content, selectedText, ctx]);

  const handleWritingContinue = useCallback((continuation: string) => {
    const separator = item.content.endsWith('\n') ? '' : '\n\n';
    ctx.updateCanvasItem(item.id, { content: item.content + separator + continuation });
  }, [item.id, item.content, ctx]);

  const handleContentChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    scheduleSnapshot(item.content);
    ctx.updateCanvasItem(item.id, { content: e.target.value });
    const target = e.target;
    target.style.height = 'auto';
    target.style.height = Math.max(target.scrollHeight, 200) + 'px';
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'z') {
      e.preventDefault();
      if (e.shiftKey) {
        const stack = redoStackRef.current;
        if (stack.length === 0) return;
        undoStackRef.current.push(item.content);
        ctx.updateCanvasItem(item.id, { content: stack.pop()! });
      } else {
        const stack = undoStackRef.current;
        if (stack.length === 0) return;
        redoStackRef.current.push(item.content);
        ctx.updateCanvasItem(item.id, { content: stack.pop()! });
      }
    }
  };

  const handleSaveAsSource = async () => {
    if (!ctx.selectedNotebookId || !item.content.trim()) return;
    setSaving(true);
    try {
      await sourceService.createNote(ctx.selectedNotebookId, item.title.trim() || 'Untitled Note', item.content.trim());
      ctx.addToast({ type: 'success', title: 'Note saved as source', message: item.title || 'Untitled Note' });
      ctx.triggerSourcesRefresh();
    } catch (err: any) {
      ctx.addToast({ type: 'error', title: 'Failed to save note', message: err.message || 'Unknown error' });
    }
    setSaving(false);
  };

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;
      audioChunksRef.current = [];
      mediaRecorder.ondataavailable = (event) => { if (event.data.size > 0) audioChunksRef.current.push(event.data); };
      mediaRecorder.onstop = async () => {
        const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/webm' });
        stream.getTracks().forEach(track => track.stop());
        setIsTranscribing(true);
        try {
          const result = await voiceService.transcribe(new File([audioBlob], 'recording.webm', { type: 'audio/webm' }), ctx.selectedNotebookId || '', undefined, false);
          ctx.updateCanvasItem(item.id, { content: item.content + (item.content ? '\n\n' : '') + result.text });
        } catch { ctx.addToast({ type: 'error', title: 'Transcription failed', message: 'Is Whisper running?' }); }
        finally { setIsTranscribing(false); }
      };
      mediaRecorder.start();
      setIsRecording(true);
    } catch { ctx.addToast({ type: 'error', title: 'Microphone access denied' }); }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && isRecording) { mediaRecorderRef.current.stop(); setIsRecording(false); }
  };

  const wordCount = item.content.trim().split(/\s+/).filter(Boolean).length;

  return (
    <div className="px-4 py-3 space-y-2.5">
      <input
        type="text"
        value={item.title}
        onChange={e => ctx.updateCanvasItem(item.id, { title: e.target.value })}
        placeholder="Note title..."
        className="w-full text-sm font-semibold bg-transparent border-none outline-none text-gray-900 dark:text-white placeholder-gray-400 dark:placeholder-gray-500"
      />
      <textarea
        ref={textareaRef}
        value={item.content}
        onChange={handleContentChange}
        onKeyDown={handleKeyDown}
        onSelect={handleTextSelect}
        onMouseUp={handleTextSelect}
        placeholder="Start writing your note... Speak or type."
        className="w-full min-h-[160px] resize-none bg-gray-50 dark:bg-gray-900/40 border border-gray-200 dark:border-gray-700 rounded-lg px-3 py-2.5 text-sm text-gray-800 dark:text-gray-200 placeholder-gray-400 dark:placeholder-gray-500 font-mono leading-relaxed outline-none focus:border-blue-400 dark:focus:border-blue-600 transition-colors"
      />
      <WritingAssistBar text={item.content} selectedText={selectedText} onReplace={handleWritingReplace} onContinue={handleWritingContinue} />
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <button
            onClick={isRecording ? stopRecording : startRecording}
            disabled={isTranscribing}
            className={`flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${isRecording ? 'bg-red-100 dark:bg-red-900/30 text-red-600 animate-pulse' : isTranscribing ? 'bg-yellow-100 dark:bg-yellow-900/30 text-yellow-600 animate-pulse' : 'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600'}`}
          >
            {isRecording ? 'Stop' : isTranscribing ? 'Transcribing...' : 'Dictate'}
          </button>
          <span className="text-[10px] text-gray-400">{wordCount} words</span>
        </div>
        <button
          onClick={handleSaveAsSource}
          disabled={saving || !item.content.trim() || !ctx.selectedNotebookId}
          className="flex items-center gap-1 px-3 py-1 text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {saving ? 'Saving...' : 'Save as Source'}
        </button>
      </div>
    </div>
  );
};

// ═══════════════════════════════════════════════════════════════════════════
// CanvasItemCard — inline artifact card with tombstone/unfurl pattern
//
// COLLAPSED (tombstone): Compact card — icon, title, status, word count.
//   Click anywhere on card to expand. X to dismiss.
// EXPANDED (unfurled): Full content rendered inline — no height cap.
//   Click header to collapse. X to dismiss.
// ═══════════════════════════════════════════════════════════════════════════

// Type-specific accent colors for the left border stripe
const TYPE_ACCENTS: Record<CanvasItem['type'], string> = {
  'document': 'border-l-blue-500',
  'visual': 'border-l-purple-500',
  'quiz': 'border-l-amber-500',
  'audio': 'border-l-green-500',
  'video': 'border-l-rose-500',
  'chat-response': 'border-l-gray-400',
  'note': 'border-l-indigo-400',
};

const TYPE_LABELS: Record<CanvasItem['type'], string> = {
  'document': 'Document',
  'visual': 'Visual',
  'quiz': 'Quiz',
  'audio': 'Audio',
  'video': 'Video',
  'chat-response': 'Response',
  'note': 'Note',
};

interface CanvasItemCardProps {
  item: CanvasItem;
  isOnly?: boolean;
}

export const CanvasItemCard: React.FC<CanvasItemCardProps> = ({ item }) => {
  const ctx = useCanvas();
  const hasUnsavedNote = item.type === 'note' && item.content.trim().length > 0;

  const handleRemove = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (hasUnsavedNote && !window.confirm('This note has unsaved content. Remove it?')) return;
    ctx.removeCanvasItem(item.id);
  };

  const handleToggle = () => {
    ctx.toggleCanvasItemCollapse(item.id);
  };

  // Compute word count for tombstone subtitle
  const wordCount = item.content ? item.content.trim().split(/\s+/).filter(Boolean).length : 0;
  const isGenerating = item.status === 'generating';
  const isError = item.status === 'error';
  const isComplete = !!item.content && !isGenerating;

  // Status text for tombstone
  const statusText = isGenerating
    ? `Generating ${TYPE_LABELS[item.type].toLowerCase()}…`
    : isError && !item.content
    ? (item.metadata?.errorMessage || 'Generation failed')
    : item.type === 'audio' && item.metadata?.audioId
    ? 'Ready to play'
    : item.type === 'video' && item.metadata?.videoId
    ? (item.status === 'complete' ? 'Ready to watch' : (item.metadata?.errorMessage || 'Processing video…'))
    : wordCount > 0
    ? `${wordCount.toLocaleString()} words`
    : '';

  // ─── COLLAPSED: Tombstone mode ────────────────────────────────────────
  if (item.collapsed && item.type !== 'note') {
    return (
      <div
        onClick={handleToggle}
        className={`mx-1 my-2 rounded-xl border border-l-[3px] ${TYPE_ACCENTS[item.type]} border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-sm cursor-pointer hover:shadow-md hover:bg-gray-50 dark:hover:bg-gray-750 transition-all group`}
      >
        <div className="flex items-center gap-3 px-3.5 py-2.5">
          {/* Type icon */}
          <div className="flex-shrink-0 text-gray-400 dark:text-gray-500 group-hover:text-gray-600 dark:group-hover:text-gray-300 transition-colors">
            {TYPE_ICONS[item.type]}
          </div>

          {/* Title + status */}
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-gray-800 dark:text-gray-200 truncate">{item.title}</p>
            <div className="flex items-center gap-2 mt-0.5">
              {isGenerating && (
                <div className="flex gap-0.5">
                  <div className="w-1 h-1 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '0ms' }} />
                  <div className="w-1 h-1 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '150ms' }} />
                  <div className="w-1 h-1 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '300ms' }} />
                </div>
              )}
              {isError && !item.content && (
                <span className="w-1.5 h-1.5 rounded-full bg-red-500 flex-shrink-0" />
              )}
              <span className={`text-[11px] ${isError && !item.content ? 'text-red-500 dark:text-red-400' : 'text-gray-400 dark:text-gray-500'}`}>
                {statusText}
              </span>
            </div>
          </div>

          {/* Expand hint + dismiss */}
          <div className="flex items-center gap-1.5 flex-shrink-0">
            {isComplete && (
              <span className="text-[10px] text-gray-400 dark:text-gray-500 opacity-0 group-hover:opacity-100 transition-opacity">
                Click to read
              </span>
            )}
            <button
              onClick={handleRemove}
              className="p-1 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors opacity-0 group-hover:opacity-100"
              title="Dismiss"
            >
              <X className="w-3.5 h-3.5" />
            </button>
            <ChevronDown className="w-3.5 h-3.5 text-gray-400 -rotate-90 group-hover:text-gray-500 transition-colors" />
          </div>
        </div>
      </div>
    );
  }

  // ─── EXPANDED: Full content mode ──────────────────────────────────────
  return (
    <div className={`mx-1 my-2 rounded-xl border border-l-[3px] ${TYPE_ACCENTS[item.type]} border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-sm overflow-hidden transition-all`}>
      {/* Clickable header to collapse back */}
      {item.type !== 'note' && (
        <div
          onClick={handleToggle}
          className="flex items-center justify-between px-3.5 py-2 bg-gray-50/80 dark:bg-gray-900/40 cursor-pointer hover:bg-gray-100/80 dark:hover:bg-gray-800/60 transition-colors border-b border-gray-100 dark:border-gray-700/50 group"
        >
          <div className="flex items-center gap-2.5">
            <span className="text-gray-500 dark:text-gray-400">{TYPE_ICONS[item.type]}</span>
            <span className="text-xs font-semibold text-gray-700 dark:text-gray-300 truncate max-w-[400px]">{item.title}</span>
            {wordCount > 0 && (
              <span className="text-[10px] text-gray-400 dark:text-gray-500">{wordCount.toLocaleString()} words</span>
            )}
          </div>
          <div className="flex items-center gap-1.5">
            <span className="text-[10px] text-gray-400 dark:text-gray-500 opacity-0 group-hover:opacity-100 transition-opacity">
              Collapse
            </span>
            <button
              onClick={handleRemove}
              className="p-1 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
              title="Dismiss"
            >
              <X className="w-3.5 h-3.5" />
            </button>
            <ChevronDown className="w-3.5 h-3.5 text-gray-400 transition-transform" />
          </div>
        </div>
      )}

      {/* Note: always expanded, has its own header via NoteEditor */}
      {item.type === 'note' ? (
        <div className="relative">
          <button
            onClick={handleRemove}
            className="absolute top-2 right-2 z-10 p-1 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
            title="Dismiss"
          >
            <X className="w-3.5 h-3.5" />
          </button>
          <NoteEditor item={item} />
        </div>
      ) : (
        <div className="px-4 py-3">
          {/* Generating placeholder */}
          {isGenerating && !item.content && item.type !== 'audio' && (
            <div className="flex items-center gap-3 py-4">
              <div className="flex gap-1">
                <div className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '0ms' }} />
                <div className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '150ms' }} />
                <div className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '300ms' }} />
              </div>
              <span className="text-xs text-gray-500 dark:text-gray-400">
                Generating {TYPE_LABELS[item.type].toLowerCase()}…
              </span>
            </div>
          )}
          {/* Error state */}
          {isError && !item.content && item.type !== 'audio' && (
            <div className="flex items-center gap-2 py-3 text-red-500 dark:text-red-400">
              <svg className="w-4 h-4 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-2.5L13.732 4c-.77-.833-1.964-.833-2.732 0L4.082 16.5c-.77.833.192 2.5 1.732 2.5z" />
              </svg>
              <span className="text-xs">Generation failed — try again</span>
            </div>
          )}
          {item.type === 'document' && item.content && (
            <div className="prose prose-sm dark:prose-invert max-w-none prose-p:my-2 prose-headings:mt-4 prose-headings:mb-1 prose-ul:my-2 prose-li:my-0 prose-hr:my-4">
              <ReactMarkdown components={{
                a: ({ href, children, ...props }) => {
                  // Intercept Feynman quiz links: #feynman-quiz:difficulty:label
                  if (href?.startsWith('#feynman-quiz:')) {
                    const parts = href.replace('#feynman-quiz:', '').split(':');
                    const difficulty = parts[0] || 'medium';
                    const label = parts.slice(1).join(':') || 'Quiz';
                    return (
                      <button
                        onClick={(e) => {
                          e.preventDefault();
                          const topic = item.title?.replace(/^Document:\s*/i, '').replace(/^Feynman.*?:\s*/i, '') || '';
                          window.dispatchEvent(new CustomEvent('feynmanQuizNav', {
                            detail: { topic: `${label}: ${topic}`.trim(), difficulty }
                          }));
                        }}
                        className="inline-flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg no-underline cursor-pointer transition-colors bg-purple-100 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300 hover:bg-purple-200 dark:hover:bg-purple-800/50 border border-purple-300 dark:border-purple-700"
                      >
                        <Target className="w-4 h-4" />
                        {children}
                      </button>
                    );
                  }
                  return <a href={href} {...props}>{children}</a>;
                },
                code: ({ className, children, ...props }) => {
                  const raw = String(children).replace(/\n$/, '');
                  if (/language-mermaid/.test(className || '')) {
                    return (
                      <div className="not-prose my-4">
                        <MermaidRenderer code={raw} className="border border-gray-200 dark:border-gray-600 rounded-lg" />
                      </div>
                    );
                  }
                  if (/language-feynman-quiz/.test(className || '')) {
                    return <FeynmanQuizBlock json={raw} docTitle={item.title} />;
                  }
                  if (/language-feynman-audio/.test(className || '')) {
                    return <FeynmanAudioBlock json={raw} />;
                  }
                  if (/language-feynman-knowledge-map/.test(className || '')) {
                    return (
                      <div className="not-prose my-4">
                        <SVGRenderer svg={raw} className="border border-gray-200 dark:border-gray-600 rounded-lg" />
                      </div>
                    );
                  }
                  return <code className={className} {...props}>{children}</code>;
                },
                pre: ({ children, ...props }) => {
                  const child = children as any;
                  if (child?.props?.className && (isFeynmanBlock(child.props.className) || /language-mermaid/.test(child.props.className) || /language-feynman-knowledge-map/.test(child.props.className))) {
                    return <>{children}</>;
                  }
                  return <pre {...props}>{children}</pre>;
                }
              }}>{item.content}</ReactMarkdown>
            </div>
          )}
          {item.type === 'visual' && (
            item.content ? (
              <MermaidRenderer code={item.content} className="border border-gray-200 dark:border-gray-600 rounded-lg" />
            ) : !isGenerating && !isError ? (
              <p className="text-gray-400 text-sm">No visual content</p>
            ) : null
          )}
          {item.type === 'quiz' && item.content && (
            <div className="prose dark:prose-invert max-w-none" dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(item.content) }} />
          )}
          {item.type === 'audio' && (
            item.metadata?.audioId && item.metadata?.notebookId ? (
              <AudioCanvasPlayer
                audioId={item.metadata.audioId}
                notebookId={item.metadata.notebookId}
                title={item.title}
              />
            ) : (
              <div className="flex items-center gap-3 p-3 bg-gray-50 dark:bg-gray-900/40 rounded-lg">
                <div className="flex-shrink-0 w-9 h-9 rounded-full bg-blue-100 dark:bg-blue-900/30 text-blue-500 flex items-center justify-center">
                  <Mic className="w-4 h-4 animate-pulse" />
                </div>
                <div>
                  <p className="text-sm font-medium text-gray-900 dark:text-white">{item.title}</p>
                  <p className="text-xs text-gray-500 dark:text-gray-400">
                    {item.status === 'error'
                      ? (item.metadata?.errorMessage || 'Audio generation failed')
                      : 'Starting audio generation…'}
                  </p>
                </div>
              </div>
            )
          )}
          {item.type === 'video' && (
            item.metadata?.videoId ? (
              <div className="rounded-lg overflow-hidden bg-black">
                {item.status === 'complete' && item.metadata?.videoId ? (
                  <video
                    controls
                    className="w-full max-h-[480px]"
                    src={`${API_BASE_URL}/video/stream/${item.metadata.videoId}`}
                    preload="metadata"
                  >
                    Your browser does not support the video element.
                  </video>
                ) : (
                  <div className="flex items-center gap-3 p-4 bg-gray-50 dark:bg-gray-900/40">
                    <div className="flex-shrink-0 w-9 h-9 rounded-full bg-rose-100 dark:bg-rose-900/30 text-rose-500 flex items-center justify-center">
                      <Video className="w-4 h-4 animate-pulse" />
                    </div>
                    <div>
                      <p className="text-sm font-medium text-gray-900 dark:text-white">{item.title}</p>
                      <p className="text-xs text-gray-500 dark:text-gray-400">
                        {item.status === 'error'
                          ? (item.metadata?.errorMessage || 'Video generation failed')
                          : (item.metadata?.errorMessage || 'Generating video…')}
                      </p>
                    </div>
                  </div>
                )}
              </div>
            ) : (
              <div className="flex items-center gap-3 p-3 bg-gray-50 dark:bg-gray-900/40 rounded-lg">
                <div className="flex-shrink-0 w-9 h-9 rounded-full bg-rose-100 dark:bg-rose-900/30 text-rose-500 flex items-center justify-center">
                  <Video className="w-4 h-4 animate-pulse" />
                </div>
                <div>
                  <p className="text-sm font-medium text-gray-900 dark:text-white">{item.title}</p>
                  <p className="text-xs text-gray-500 dark:text-gray-400">Starting video generation…</p>
                </div>
              </div>
            )
          )}
          {item.type === 'chat-response' && item.content && (
            <div className="prose prose-sm dark:prose-invert max-w-none prose-p:my-2 prose-headings:mt-3 prose-headings:mb-1">
              <ReactMarkdown>{item.content}</ReactMarkdown>
            </div>
          )}

          {/* Source attribution */}
          {item.sourceNames && item.sourceNames.length > 0 && (
            <details className="mt-3 border-t border-gray-100 dark:border-gray-700/50 pt-2">
              <summary className="text-[11px] font-medium text-gray-500 dark:text-gray-400 cursor-pointer hover:text-gray-700 dark:hover:text-gray-300 select-none">
                <BookOpen className="w-3 h-3 inline mr-1" />{item.sourceNames.length} source{item.sourceNames.length !== 1 ? 's' : ''} used
              </summary>
              <div className="mt-1.5 space-y-1">
                {item.sourceNames.map((name, idx) => {
                  const scores = Object.values(item.relevanceScores || {});
                  const score = scores[idx] ?? null;
                  const pct = score !== null ? Math.round(score * 100) : null;
                  return (
                    <div key={idx} className="flex items-center gap-2">
                      <span className="text-[11px] text-gray-600 dark:text-gray-400 truncate flex-1 min-w-0" title={name}>{name}</span>
                      {pct !== null && (
                        <div className="flex items-center gap-1.5 flex-shrink-0">
                          <div className="w-14 h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                            <div className={`h-full rounded-full ${pct >= 70 ? 'bg-green-500' : pct >= 40 ? 'bg-yellow-500' : 'bg-gray-400'}`} style={{ width: `${Math.max(8, pct)}%` }} />
                          </div>
                          <span className="text-[10px] text-gray-400 w-7 text-right">{pct}%</span>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </details>
          )}
        </div>
      )}
    </div>
  );
};
