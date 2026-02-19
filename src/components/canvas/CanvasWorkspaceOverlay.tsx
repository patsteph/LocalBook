import React, { useState, useRef, useEffect, useCallback } from 'react';
import DOMPurify from 'dompurify';
import {
  FileText, Palette, Target, Mic, MessageSquare, PenLine,
  Presentation, Download, Search
} from 'lucide-react';
import { useCanvas } from './CanvasContext';
import { CanvasItem } from './types';
import ReactMarkdown from 'react-markdown';
import { MermaidRenderer } from '../shared/MermaidRenderer';
import { contentService } from '../../services/content';
import { visualService } from '../../services/visual';
import { quizService } from '../../services/quiz';
import { audioService } from '../../services/audio';
import { chatService } from '../../services/chat';
import { curatorService } from '../../services/curatorApi';
import { sourceService } from '../../services/sources';
import { voiceService } from '../../services/voice';
import { settingsService } from '../../services/settings';

// Icons for canvas item types
const iconSm = 'w-3.5 h-3.5';
const TYPE_ICONS: Record<CanvasItem['type'], React.ReactNode> = {
  'document': <FileText className={iconSm} />,
  'visual': <Palette className={iconSm} />,
  'quiz': <Target className={iconSm} />,
  'audio': <Mic className={iconSm} />,
  'chat-response': <MessageSquare className={iconSm} />,
  'note': <PenLine className={iconSm} />,
};

// === Note Editor Component ===
interface NoteEditorProps {
  item: CanvasItem;
}

const NoteCanvasEditor: React.FC<NoteEditorProps> = ({ item }) => {
  const ctx = useCanvas();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [saving, setSaving] = useState(false);
  const [isRecording, setIsRecording] = useState(false);

  // Undo/redo history
  const undoStackRef = useRef<string[]>([]);
  const redoStackRef = useRef<string[]>([]);
  const snapshotTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const MAX_UNDO = 50;

  // Take a snapshot for undo (debounced — called on content change)
  const scheduleSnapshot = useCallback((currentContent: string) => {
    if (snapshotTimerRef.current) clearTimeout(snapshotTimerRef.current);
    snapshotTimerRef.current = setTimeout(() => {
      const stack = undoStackRef.current;
      if (stack.length === 0 || stack[stack.length - 1] !== currentContent) {
        stack.push(currentContent);
        if (stack.length > MAX_UNDO) stack.shift();
        redoStackRef.current = []; // new edit clears redo
      }
    }, 1000);
  }, []);

  // Seed initial snapshot
  useEffect(() => {
    if (item.content) {
      undoStackRef.current = [item.content];
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
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

  useEffect(() => {
    // For new notes: fetch user name and set default title, then focus
    if (!item.content && !item.title) {
      settingsService.getUserProfile()
        .then(profile => {
          const name = profile.name?.trim();
          ctx.updateCanvasItem(item.id, {
            title: name ? `${name}'s Notes` : "User's Notes",
          });
        })
        .catch(() => {
          ctx.updateCanvasItem(item.id, { title: "User's Notes" });
        });
      setTimeout(() => textareaRef.current?.focus(), 200);
    }
  }, []);

  const handleTitleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    ctx.updateCanvasItem(item.id, { title: e.target.value });
  };

  const handleContentChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newContent = e.target.value;
    scheduleSnapshot(item.content); // snapshot the PREVIOUS content before updating
    ctx.updateCanvasItem(item.id, { content: newContent });
    // Auto-resize textarea
    const target = e.target;
    target.style.height = 'auto';
    target.style.height = Math.max(target.scrollHeight, 200) + 'px';
  };

  const handleUndo = () => {
    const stack = undoStackRef.current;
    if (stack.length === 0) return;
    const prev = stack.pop()!;
    redoStackRef.current.push(item.content);
    ctx.updateCanvasItem(item.id, { content: prev });
  };

  const handleRedo = () => {
    const stack = redoStackRef.current;
    if (stack.length === 0) return;
    const next = stack.pop()!;
    undoStackRef.current.push(item.content);
    ctx.updateCanvasItem(item.id, { content: next });
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'z') {
      e.preventDefault();
      if (e.shiftKey) {
        handleRedo();
      } else {
        handleUndo();
      }
    }
  };

  const handleSaveAsSource = async () => {
    if (!ctx.selectedNotebookId || !item.content.trim()) return;
    setSaving(true);
    try {
      await sourceService.createNote(
        ctx.selectedNotebookId,
        item.title.trim() || 'Untitled Note',
        item.content.trim()
      );
      ctx.addToast({ type: 'success', title: 'Note saved as source', message: item.title || 'Untitled Note' });
      ctx.triggerSourcesRefresh();
    } catch (err: any) {
      console.error('Save note failed:', err);
      ctx.addToast({ type: 'error', title: 'Failed to save note', message: err.message || 'Unknown error' });
    }
    setSaving(false);
  };

  // Dictation — same pattern as ChatInterface
  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;
      audioChunksRef.current = [];

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) audioChunksRef.current.push(event.data);
      };

      mediaRecorder.onstop = async () => {
        const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/webm' });
        stream.getTracks().forEach(track => track.stop());
        setIsTranscribing(true);
        try {
          const result = await voiceService.transcribe(
            new File([audioBlob], 'recording.webm', { type: 'audio/webm' }),
            ctx.selectedNotebookId || '',
            undefined,
            false
          );
          const newContent = item.content + (item.content ? '\n\n' : '') + result.text;
          ctx.updateCanvasItem(item.id, { content: newContent });
        } catch (err) {
          console.error('Transcription failed:', err);
          ctx.addToast({ type: 'error', title: 'Transcription failed', message: 'Is Whisper running?' });
        } finally {
          setIsTranscribing(false);
        }
      };

      mediaRecorder.start();
      setIsRecording(true);
    } catch (err) {
      console.error('Mic access denied:', err);
      ctx.addToast({ type: 'error', title: 'Microphone access denied' });
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
    }
  };

  const wordCount = item.content.trim().split(/\s+/).filter(Boolean).length;

  return (
    <div className="px-5 py-4 space-y-3">
      {/* Title */}
      <input
        type="text"
        value={item.title}
        onChange={handleTitleChange}
        placeholder="Note title..."
        className="w-full text-lg font-semibold bg-transparent border-none outline-none text-gray-900 dark:text-white placeholder-gray-400 dark:placeholder-gray-500"
      />

      {/* Textarea editor */}
      <textarea
        ref={textareaRef}
        value={item.content}
        onChange={handleContentChange}
        onKeyDown={handleKeyDown}
        placeholder="Start writing your note... Speak or type. Use AI actions below to expand, refine, or generate from your notes."
        className="w-full min-h-[200px] resize-none bg-gray-50 dark:bg-gray-900/40 border border-gray-200 dark:border-gray-700 rounded-lg px-4 py-3 text-sm text-gray-800 dark:text-gray-200 placeholder-gray-400 dark:placeholder-gray-500 font-mono leading-relaxed outline-none focus:border-blue-400 dark:focus:border-blue-600 transition-colors"
      />

      {/* Note toolbar */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {/* Mic / dictation button */}
          <button
            onClick={isRecording ? stopRecording : startRecording}
            disabled={isTranscribing}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
              isRecording
                ? 'bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400 animate-pulse'
                : isTranscribing
                  ? 'bg-yellow-100 dark:bg-yellow-900/30 text-yellow-600 dark:text-yellow-400 animate-pulse'
                  : 'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600'
            }`}
          >
            {isRecording ? (
              <><svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2" /></svg> Stop</>
            ) : isTranscribing ? (
              <>⏳ Transcribing...</>
            ) : (
              <><svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 11a7 7 0 01-7 7m0 0a7 7 0 01-7-7m7 7v4m0 0H8m4 0h4M12 15a3 3 0 003-3V5a3 3 0 00-6 0v7a3 3 0 003 3z" /></svg> Dictate</>
            )}
          </button>

          {/* Word count */}
          <span className="text-xs text-gray-400 dark:text-gray-500">
            {wordCount.toLocaleString()} words · {item.content.length.toLocaleString()} chars
          </span>
        </div>

        {/* Save as Source */}
        <button
          onClick={handleSaveAsSource}
          disabled={saving || !item.content.trim() || !ctx.selectedNotebookId}
          className="flex items-center gap-1.5 px-4 py-1.5 text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {saving ? '💾 Saving...' : '💾 Save as Source'}
        </button>
      </div>
    </div>
  );
};

interface CanvasItemRendererProps {
  item: CanvasItem;
  onToggleCollapse: (id: string) => void;
  onRemove: (id: string) => void;
  isOnly: boolean; // true if this is the only item (don't show collapse)
}

const CanvasItemRenderer: React.FC<CanvasItemRendererProps> = ({ item, onToggleCollapse, onRemove, isOnly }) => {
  return (
    <div className="border-b border-gray-100 dark:border-gray-700/50 last:border-b-0">
      {/* Item header — only show if there are multiple items */}
      {!isOnly && (
        <div
          className="flex items-center justify-between px-4 py-2 bg-gray-50/50 dark:bg-gray-900/30 cursor-pointer hover:bg-gray-100/50 dark:hover:bg-gray-800/50 transition-colors"
          onClick={() => onToggleCollapse(item.id)}
        >
          <div className="flex items-center gap-2">
            <span className="text-xs">{TYPE_ICONS[item.type]}</span>
            <span className="text-xs font-medium text-gray-700 dark:text-gray-300">{item.title}</span>
          </div>
          <div className="flex items-center gap-1">
            <button
              onClick={(e) => { e.stopPropagation(); onRemove(item.id); }}
              className="p-0.5 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
            >
              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
            <svg
              className={`w-3 h-3 text-gray-400 transition-transform ${item.collapsed ? '' : 'rotate-180'}`}
              fill="none" stroke="currentColor" viewBox="0 0 24 24"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </div>
        </div>
      )}

      {/* Item content */}
      {!item.collapsed && (
        item.type === 'note' ? (
          <NoteCanvasEditor item={item} />
        ) : (
        <div className="px-6 py-4">
          {item.type === 'document' && (
            <div className="prose prose-sm dark:prose-invert max-w-none prose-p:my-2 prose-headings:mt-4 prose-headings:mb-1 prose-ul:my-2 prose-li:my-0 prose-hr:my-4">
              <ReactMarkdown>{item.content}</ReactMarkdown>
            </div>
          )}
          {item.type === 'visual' && (
            item.content ? (
              <MermaidRenderer code={item.content} className="border border-gray-200 dark:border-gray-600 rounded-lg" />
            ) : (
              <p className="text-gray-400 text-sm">No visual content</p>
            )
          )}
          {item.type === 'quiz' && (
            <div className="prose dark:prose-invert max-w-none" dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(item.content) }} />
          )}
          {item.type === 'audio' && (
            <div className="flex items-center gap-3 p-3 bg-gray-50 dark:bg-gray-900/40 rounded-lg">
              <span className="text-2xl">🎙️</span>
              <div>
                <p className="text-sm font-medium text-gray-900 dark:text-white">{item.title}</p>
                <p className="text-xs text-gray-500 dark:text-gray-400">Audio generation started — check Audio tab when complete</p>
              </div>
            </div>
          )}
          {item.type === 'chat-response' && (
            <div className="prose prose-sm dark:prose-invert max-w-none prose-p:my-2 prose-headings:mt-3 prose-headings:mb-1">
              <ReactMarkdown>{item.content}</ReactMarkdown>
            </div>
          )}
        </div>
        )
      )}
    </div>
  );
};

// Canvas action definitions
interface CanvasAction {
  id: string;
  icon: React.ReactNode;
  label: string;
  shortLabel: string;
  enabled: (items: CanvasItem[], notebookId: string | null) => boolean;
}

const CANVAS_ACTIONS: CanvasAction[] = [
  { id: 'visual', icon: <Palette className={iconSm} />, label: 'Create Visual', shortLabel: 'Visual', enabled: (items, nb) => !!nb && items.some(i => i.type === 'document' || i.type === 'chat-response' || i.type === 'note') },
  { id: 'audio', icon: <Mic className={iconSm} />, label: 'Generate Audio', shortLabel: 'Audio', enabled: (items, nb) => !!nb && items.some(i => i.type === 'document' || i.type === 'note') },
  { id: 'quiz', icon: <Target className={iconSm} />, label: 'Create Quiz', shortLabel: 'Quiz', enabled: (items, nb) => !!nb && items.some(i => i.type === 'document' || i.type === 'note') },
  { id: 'pptx', icon: <Presentation className={iconSm} />, label: 'Create Slides', shortLabel: 'PPTX', enabled: (items, nb) => !!nb && items.some(i => i.type === 'document' || i.type === 'note') },
  { id: 'pdf', icon: <Download className={iconSm} />, label: 'Download PDF', shortLabel: 'PDF', enabled: (items) => items.some(i => i.type === 'document' || i.type === 'note') },
  { id: 'crossnb', icon: <Search className={iconSm} />, label: 'Cross-Notebook', shortLabel: 'Discover', enabled: (items, nb) => !!nb && items.length > 0 },
];

export const CanvasWorkspaceOverlay: React.FC = () => {
  const ctx = useCanvas();
  const [chatInput, setChatInput] = useState('');
  const [chatLoading, setChatLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const chatInputRef = useRef<HTMLTextAreaElement>(null);
  const contentAreaRef = useRef<HTMLDivElement>(null);

  // Keep a ref to the latest canvas items to avoid stale reads during streaming
  const canvasItemsRef = useRef(ctx.canvasItems);
  canvasItemsRef.current = ctx.canvasItems;

  // Auto-focus chat input when canvas opens
  useEffect(() => {
    const timer = setTimeout(() => chatInputRef.current?.focus(), 200);
    return () => clearTimeout(timer);
  }, []);

  // Guard: confirm before closing if unsaved note content exists
  const hasUnsavedNotes = ctx.canvasItems.some(i => i.type === 'note' && i.content.trim().length > 0);

  const handleCloseCanvas = () => {
    if (hasUnsavedNotes) {
      if (!window.confirm('You have unsaved note content. Close canvas and discard?')) return;
    }
    ctx.clearCanvas();
  };

  const handleRemoveItem = (id: string) => {
    const item = ctx.canvasItems.find(i => i.id === id);
    if (item?.type === 'note' && item.content.trim().length > 0) {
      if (!window.confirm('This note has unsaved content. Remove it?')) return;
    }
    ctx.removeCanvasItem(id);
  };

  // Scroll to bottom when new items are added (delayed for DOM render)
  const prevItemCount = useRef(ctx.canvasItems.length);
  useEffect(() => {
    if (ctx.canvasItems.length > prevItemCount.current && contentAreaRef.current) {
      const timer = setTimeout(() => {
        contentAreaRef.current?.scrollTo({
          top: contentAreaRef.current.scrollHeight,
          behavior: 'smooth',
        });
      }, 100);
      return () => clearTimeout(timer);
    }
    prevItemCount.current = ctx.canvasItems.length;
  }, [ctx.canvasItems.length]);

  // Get the primary document/note content from canvas items (uses ref for freshness)
  const getPrimaryContent = (): string => {
    const items = canvasItemsRef.current;
    const doc = items.find(i => i.type === 'document');
    if (doc) return doc.content;
    const note = items.find(i => i.type === 'note');
    if (note) return note.content;
    const chatResp = items.find(i => i.type === 'chat-response');
    if (chatResp) return chatResp.content;
    return items.map(i => i.content).join('\n\n');
  };

  const getPrimaryTitle = (): string => {
    const items = canvasItemsRef.current;
    const doc = items.find(i => i.type === 'document');
    if (doc) return doc.title;
    const note = items.find(i => i.type === 'note');
    if (note) return note.title;
    return items[0]?.title || 'Document';
  };

  // === Action handlers ===

  const handleCreateVisual = async () => {
    if (!ctx.selectedNotebookId) return;
    setActionLoading('visual');
    try {
      const content = getPrimaryContent();
      const title = getPrimaryTitle();
      await visualService.generateSmartStream(
        ctx.selectedNotebookId,
        content,
        'auto',
        // onPrimary — add visual to canvas
        (diagram) => {
          ctx.addCanvasItem({
            type: 'visual',
            title: diagram.title || `Visual: ${title}`,
            content: diagram.svg || diagram.code || '',
            collapsed: false,
          });
          setActionLoading(null);
        },
        // onAlternative — ignore for canvas (only show primary)
        () => {},
        // onDone
        () => setActionLoading(null),
        // onError
        (err) => {
          console.error('Canvas visual generation failed:', err);
          ctx.addToast({ type: 'error', title: 'Visual generation failed', message: err });
          setActionLoading(null);
        }
      );
    } catch (err) {
      console.error('Canvas visual failed:', err);
      setActionLoading(null);
    }
  };

  const handleCreateAudio = async () => {
    if (!ctx.selectedNotebookId) return;
    setActionLoading('audio');
    try {
      const title = getPrimaryTitle();
      const content = getPrimaryContent();
      // Extract topic from content (first 200 chars as topic hint)
      const topic = content.substring(0, 200).replace(/[#*_\n]/g, ' ').trim();
      await audioService.generate({
        notebook_id: ctx.selectedNotebookId,
        topic,
        duration_minutes: 15,
        skill_id: undefined,
        host1_gender: 'male',
        host2_gender: 'female',
        accent: 'us',
      });
      ctx.addCanvasItem({
        type: 'audio',
        title: `Podcast: ${title}`,
        content: '',
        collapsed: false,
      });
      ctx.addToast({ type: 'success', title: 'Audio generation started', message: 'Check the Audio tab in Studio when complete' });
    } catch (err: any) {
      console.error('Canvas audio failed:', err);
      ctx.addToast({ type: 'error', title: 'Audio generation failed', message: err.message || 'Unknown error' });
    }
    setActionLoading(null);
  };

  const handleCreateQuiz = async () => {
    if (!ctx.selectedNotebookId) return;
    setActionLoading('quiz');
    try {
      const content = getPrimaryContent();
      const topic = content.substring(0, 300).replace(/[#*_\n]/g, ' ').trim();
      const quiz = await quizService.generate(ctx.selectedNotebookId, 5, 'medium', topic);
      // Format quiz as readable HTML
      const quizHtml = quiz.questions.map((q, i) => {
        const optionsHtml = q.options
          ? q.options.map((opt, j) => `<li>${String.fromCharCode(65 + j)}. ${opt}</li>`).join('')
          : '';
        return `<div class="mb-4"><p><strong>Q${i + 1}.</strong> ${q.question}</p>${optionsHtml ? `<ul>${optionsHtml}</ul>` : ''}<details class="mt-1"><summary class="text-sm text-blue-600 cursor-pointer">Show Answer</summary><p class="text-sm text-green-700 dark:text-green-400 mt-1"><strong>Answer:</strong> ${q.answer}</p><p class="text-sm text-gray-600 dark:text-gray-400">${q.explanation}</p></details></div>`;
      }).join('');
      ctx.addCanvasItem({
        type: 'quiz',
        title: `Quiz: ${quiz.topic || getPrimaryTitle()}`,
        content: quizHtml,
        collapsed: false,
      });
    } catch (err: any) {
      console.error('Canvas quiz failed:', err);
      ctx.addToast({ type: 'error', title: 'Quiz generation failed', message: err.message || 'Unknown error' });
    }
    setActionLoading(null);
  };

  const handleExportPPTX = () => {
    // Dispatch event to open ExportModal with content pre-loaded
    window.dispatchEvent(new CustomEvent('openExportModal', {
      detail: { content: getPrimaryContent(), title: getPrimaryTitle() },
    }));
  };

  const handleExportPDF = async () => {
    setActionLoading('pdf');
    try {
      const content = getPrimaryContent();
      const title = getPrimaryTitle();
      await contentService.downloadAsPDF(content, title, title.toLowerCase().replace(/\s+/g, '-'));
    } catch (err) {
      console.error('PDF download failed:', err);
      ctx.addToast({ type: 'error', title: 'PDF download failed' });
    }
    setActionLoading(null);
  };

  const handleCrossNotebook = async () => {
    if (!ctx.selectedNotebookId) return;
    setActionLoading('crossnb');
    try {
      const content = getPrimaryContent();
      // Use first 500 chars as the cross-notebook query
      const query = content.substring(0, 500).replace(/[#*_\n]/g, ' ').trim();
      const result = await curatorService.chat(
        `Find connections and insights across all my notebooks related to: ${query}`,
        ctx.selectedNotebookId
      );
      if (result?.response) {
        ctx.addCanvasItem({
          type: 'chat-response',
          title: 'Cross-Notebook Discovery',
          content: result.response,
          collapsed: false,
        });
      }
    } catch (err: any) {
      console.error('Cross-notebook discovery failed:', err);
      ctx.addToast({ type: 'error', title: 'Cross-notebook search failed', message: err.message || 'Unknown error' });
    }
    setActionLoading(null);
  };

  const ACTION_HANDLERS: Record<string, () => void | Promise<void>> = {
    visual: handleCreateVisual,
    audio: handleCreateAudio,
    quiz: handleCreateQuiz,
    pptx: handleExportPPTX,
    pdf: handleExportPDF,
    crossnb: handleCrossNotebook,
  };

  // === Canvas chat (streaming, single threaded conversation) ===
  const streamingItemIdRef = useRef<string | null>(null);
  const CONVO_ITEM_ID = 'canvas-conversation';

  const handleCanvasChat = async () => {
    if (!chatInput.trim() || !ctx.selectedNotebookId || chatLoading) return;
    const query = chatInput.trim();
    setChatInput('');
    setChatLoading(true);

    // Find existing conversation item, or create one
    const existingConvo = ctx.canvasItems.find(i => i.id === CONVO_ITEM_ID);
    const previousContent = existingConvo?.content || '';

    if (!existingConvo) {
      ctx.addCanvasItem({
        id: CONVO_ITEM_ID,
        type: 'chat-response',
        title: 'Conversation',
        content: '',
        collapsed: false,
      });
    }

    // Build the new turn prefix: separator + question header
    const turnPrefix = previousContent
      ? `${previousContent}\n\n---\n\n**Q: ${query}**\n\n`
      : `**Q: ${query}**\n\n`;

    streamingItemIdRef.current = CONVO_ITEM_ID;

    // Prefix the query with canvas content context
    const canvasContent = getPrimaryContent();
    const contextPrefix = canvasContent
      ? `[Context — the user is viewing this content in the canvas:\n${canvasContent.substring(0, 4000)}\n]\n\nUser question: `
      : '';

    let streamedTokens = '';
    let lastUpdateTime = 0;
    const UPDATE_INTERVAL = 50;

    try {
      await chatService.queryStream(
        {
          notebook_id: ctx.selectedNotebookId,
          question: contextPrefix + query,
          llm_provider: ctx.selectedLLMProvider,
        },
        {
          onToken: (token) => {
            streamedTokens += token;
            const now = Date.now();
            if (now - lastUpdateTime >= UPDATE_INTERVAL) {
              lastUpdateTime = now;
              ctx.updateCanvasItem(CONVO_ITEM_ID, { content: turnPrefix + streamedTokens });
            }
          },
          onReplaceAnswer: (content) => {
            streamedTokens = content;
            ctx.updateCanvasItem(CONVO_ITEM_ID, { content: turnPrefix + streamedTokens });
          },
          onDone: () => {
            if (streamedTokens) {
              ctx.updateCanvasItem(CONVO_ITEM_ID, { content: turnPrefix + streamedTokens });
            }
            streamingItemIdRef.current = null;
            setChatLoading(false);
          },
          onError: (err) => {
            console.error('Canvas chat error:', err);
            // Restore previous content on error
            ctx.updateCanvasItem(CONVO_ITEM_ID, { content: previousContent });
            ctx.addToast({ type: 'error', title: 'Chat failed', message: err });
            streamingItemIdRef.current = null;
            setChatLoading(false);
          },
        }
      );
    } catch (err) {
      console.error('Canvas chat failed:', err);
      ctx.updateCanvasItem(CONVO_ITEM_ID, { content: previousContent });
      streamingItemIdRef.current = null;
      setChatLoading(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleCanvasChat();
    }
  };

  return (
    <div className="absolute inset-0 bg-white dark:bg-gray-800 z-20 flex flex-col animate-slide-up">
      {/* Minimal header — just title + close */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-200 dark:border-gray-700 bg-gray-50/80 dark:bg-gray-900/60 flex-shrink-0">
        <div className="flex items-center gap-2">
          <div
            className="h-1.5 w-1.5 rounded-full bg-green-500 animate-pulse"
            title="Canvas active"
          />
          <h2 className="text-sm font-semibold text-gray-900 dark:text-white flex items-center gap-1.5">
            {ctx.canvasItems.length === 1
              ? <>{TYPE_ICONS[ctx.canvasItems[0].type]} {ctx.canvasItems[0].title}</>
              : `Canvas · ${ctx.canvasItems.length} items`
            }
          </h2>
        </div>
        <button
          onClick={handleCloseCanvas}
          className="p-1.5 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 transition-colors"
          title="Close canvas and return to chat"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* Scrollable stacked content area */}
      <div ref={contentAreaRef} className="flex-1 overflow-y-auto">
        {ctx.canvasItems.map(item => (
          <CanvasItemRenderer
            key={item.id}
            item={item}
            onToggleCollapse={ctx.toggleCanvasItemCollapse}
            onRemove={handleRemoveItem}
            isOnly={ctx.canvasItems.length === 1}
          />
        ))}

        {/* Loading indicator for chat */}
        {chatLoading && (
          <div className="px-6 py-4 flex items-center gap-2 text-gray-500 dark:text-gray-400">
            <div className="flex gap-1">
              <div className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '0ms' }} />
              <div className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '150ms' }} />
              <div className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '300ms' }} />
            </div>
            <span className="text-xs">Thinking...</span>
          </div>
        )}
      </div>

      {/* Bottom control surface — action bar + chat input */}
      <div className="border-t border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 flex-shrink-0">
        {/* Action pill row */}
        <div className="flex items-center gap-1.5 px-3 py-2 overflow-x-auto scrollbar-hide">
          {CANVAS_ACTIONS.map(action => {
            const enabled = action.enabled(ctx.canvasItems, ctx.selectedNotebookId);
            const loading = actionLoading === action.id;
            return (
              <button
                key={action.id}
                onClick={() => !loading && enabled && ACTION_HANDLERS[action.id]?.()}
                disabled={!enabled || !!actionLoading}
                className={`flex items-center gap-1 px-2.5 py-1.5 rounded-full text-xs font-medium whitespace-nowrap transition-all ${
                  loading
                    ? 'bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 animate-pulse'
                    : enabled && !actionLoading
                      ? 'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600 hover:shadow-sm'
                      : 'bg-gray-50 dark:bg-gray-800 text-gray-400 dark:text-gray-600 cursor-not-allowed'
                }`}
                title={action.label}
              >
                <span className="text-xs">{action.icon}</span>
                <span>{loading ? '...' : action.shortLabel}</span>
              </button>
            );
          })}
        </div>

        {/* Chat input */}
        <div className="px-3 pb-3">
          <div className="flex items-end gap-2 bg-gray-50 dark:bg-gray-900/60 rounded-lg border border-gray-200 dark:border-gray-700 focus-within:border-blue-400 dark:focus-within:border-blue-600 transition-colors">
            <textarea
              ref={chatInputRef}
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask about this content, refine, dig deeper..."
              rows={1}
              className="flex-1 px-3 py-2.5 bg-transparent text-sm text-gray-900 dark:text-white placeholder-gray-400 dark:placeholder-gray-500 resize-none outline-none min-h-[38px] max-h-[120px]"
              style={{ height: 'auto', overflow: 'hidden' }}
              onInput={(e) => {
                const target = e.target as HTMLTextAreaElement;
                target.style.height = 'auto';
                target.style.height = Math.min(target.scrollHeight, 120) + 'px';
              }}
            />
            <button
              onClick={handleCanvasChat}
              disabled={!chatInput.trim() || chatLoading || !ctx.selectedNotebookId}
              className="p-2 mr-1 mb-0.5 rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:bg-gray-300 dark:disabled:bg-gray-700 disabled:cursor-not-allowed transition-colors flex-shrink-0"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19V5m0 0l-7 7m7-7l7 7" />
              </svg>
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
