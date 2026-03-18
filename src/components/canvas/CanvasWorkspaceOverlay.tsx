import React, { useState, useRef, useEffect, useMemo } from 'react';
import DOMPurify from 'dompurify';
import {
  FileText, Palette, Target, Mic, Video, MessageSquare, PenLine,
  Presentation, Download, Search, Sparkles, Brain, GitBranch,
  CalendarDays, Network, BarChart3, BookOpen, MessageCircle
} from 'lucide-react';
import { useCanvas } from './CanvasContext';
import { CanvasItem } from './types';
import ReactMarkdown from 'react-markdown';
import { MermaidRenderer } from '../shared/MermaidRenderer';
import { SVGRenderer } from '../shared/SVGRenderer';
import { FeynmanQuizBlock, FeynmanAudioBlock, isFeynmanBlock } from '../shared/FeynmanBlocks';
import { CanvasActionPopover } from './CanvasActionPopover';
import { contentService } from '../../services/content';
import { visualService } from '../../services/visual';
import { quizService } from '../../services/quiz';
import { audioService } from '../../services/audio';
import { chatService } from '../../services/chat';
import { curatorService } from '../../services/curatorApi';
import { skillsService } from '../../services/skills';
import { writingService, FormatOption } from '../../services/writing';
import { exportService } from '../../services/export';
import { Skill } from '../../types';
import { WritingAssistBar } from '../WritingAssistBar';
import { AudioCanvasPlayer } from '../chat/AudioCanvasPlayer';
import { RichNoteEditor } from '../RichNoteEditor';

// Icons for canvas item types
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

// Note Editor — uses RichNoteEditor (imported above)

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
          <RichNoteEditor item={item} />
        ) : (
        <div className="px-6 py-4">
          {/* Generating placeholder */}
          {item.status === 'generating' && !item.content && item.type !== 'audio' && (
            <div className="flex items-center gap-3 py-4">
              <div className="flex gap-1">
                <div className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '0ms' }} />
                <div className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '150ms' }} />
                <div className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-bounce" style={{ animationDelay: '300ms' }} />
              </div>
              <span className="text-xs text-gray-500 dark:text-gray-400">
                Generating {item.type === 'document' ? 'document' : item.type === 'quiz' ? 'quiz' : item.type === 'visual' ? 'visual' : 'content'}…
              </span>
            </div>
          )}
          {/* Error state */}
          {item.status === 'error' && !item.content && item.type !== 'audio' && (
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
                  // Render ```mermaid blocks as interactive diagrams
                  if (/language-mermaid/.test(className || '')) {
                    return (
                      <div className="not-prose my-4">
                        <MermaidRenderer code={raw} className="border border-gray-200 dark:border-gray-600 rounded-lg" />
                      </div>
                    );
                  }
                  // Render ```feynman-quiz blocks as interactive quiz buttons
                  if (/language-feynman-quiz/.test(className || '')) {
                    return <FeynmanQuizBlock json={raw} docTitle={item.title} />;
                  }
                  // Render ```feynman-audio blocks as interactive audio buttons
                  if (/language-feynman-audio/.test(className || '')) {
                    return <FeynmanAudioBlock json={raw} />;
                  }
                  // Render ```feynman-knowledge-map blocks as inline SVG
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
                  // Unwrap <pre> for custom-rendered blocks (mermaid, feynman-quiz, feynman-audio)
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
            ) : item.status !== 'generating' && item.status !== 'error' ? (
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
          {item.type === 'chat-response' && (
            <div className="prose prose-sm dark:prose-invert max-w-none prose-p:my-2 prose-headings:mt-3 prose-headings:mb-1">
              <ReactMarkdown>{item.content}</ReactMarkdown>
            </div>
          )}

          {/* Source attribution bar */}
          {item.sourceNames && item.sourceNames.length > 0 && (
            <details className="mt-4 border-t border-gray-100 dark:border-gray-700/50 pt-3">
              <summary className="text-[11px] font-medium text-gray-500 dark:text-gray-400 cursor-pointer hover:text-gray-700 dark:hover:text-gray-300 select-none">
                <BookOpen className="w-3 h-3 inline mr-1" />{item.sourceNames.length} source{item.sourceNames.length !== 1 ? 's' : ''} used
              </summary>
              <div className="mt-2 space-y-1.5">
                {item.sourceNames.map((name, idx) => {
                  // Find relevance score by matching source name (scores keyed by ID, but we display names)
                  const scores = Object.values(item.relevanceScores || {});
                  const score = scores[idx] ?? null;
                  const pct = score !== null ? Math.round(score * 100) : null;
                  return (
                    <div key={idx} className="flex items-center gap-2">
                      <span className="text-[11px] text-gray-600 dark:text-gray-400 truncate flex-1 min-w-0" title={name}>
                        {name}
                      </span>
                      {pct !== null && (
                        <div className="flex items-center gap-1.5 flex-shrink-0">
                          <div className="w-16 h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
                            <div
                              className={`h-full rounded-full ${pct >= 70 ? 'bg-green-500' : pct >= 40 ? 'bg-yellow-500' : 'bg-gray-400'}`}
                              style={{ width: `${Math.max(8, pct)}%` }}
                            />
                          </div>
                          <span className="text-[10px] text-gray-400 dark:text-gray-500 w-7 text-right">{pct}%</span>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </details>
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
  { id: 'docs', icon: <FileText className={iconSm} />, label: 'Generate Document', shortLabel: 'Docs', enabled: (_items, nb) => !!nb },
  { id: 'visual', icon: <Palette className={iconSm} />, label: 'Create Visual', shortLabel: 'Visual', enabled: (items, nb) => !!nb && items.some(i => i.type === 'document' || i.type === 'chat-response' || i.type === 'note') },
  { id: 'audio', icon: <Mic className={iconSm} />, label: 'Generate Audio', shortLabel: 'Audio', enabled: (items, nb) => !!nb && items.some(i => i.type === 'document' || i.type === 'note') },
  { id: 'quiz', icon: <Target className={iconSm} />, label: 'Create Quiz', shortLabel: 'Quiz', enabled: (items, nb) => !!nb && items.some(i => i.type === 'document' || i.type === 'note') },
  { id: 'pptx', icon: <Presentation className={iconSm} />, label: 'Create Slides', shortLabel: 'PPTX', enabled: (items, nb) => !!nb && items.some(i => i.type === 'document' || i.type === 'note') },
  { id: 'pdf', icon: <Download className={iconSm} />, label: 'Download PDF', shortLabel: 'PDF', enabled: (items) => items.some(i => i.type === 'document' || i.type === 'note') },
  { id: 'crossnb', icon: <Search className={iconSm} />, label: 'Cross-Notebook Discovery', shortLabel: 'Discover', enabled: (_items, nb) => !!nb },
];

export const CanvasWorkspaceOverlay: React.FC = () => {
  const ctx = useCanvas();
  const [chatInput, setChatInput] = useState('');
  const [chatLoading, setChatLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [showCanvasHint, setShowCanvasHint] = useState(() => !localStorage.getItem('lb-canvas-hint-seen'));
  const chatInputRef = useRef<HTMLTextAreaElement>(null);
  const contentAreaRef = useRef<HTMLDivElement>(null);

  // === Popover state ===
  const [activePopover, setActivePopover] = useState<string | null>(null);

  // Quiz popover config (remembered via localStorage)
  const [quizCount, setQuizCount] = useState(() => parseInt(localStorage.getItem('lb-canvas-quiz-count') || '5'));
  const [quizDifficulty, setQuizDifficulty] = useState(() => localStorage.getItem('lb-canvas-quiz-diff') || 'medium');
  const [quizTopic, setQuizTopic] = useState('');

  // Audio popover config
  const [audioSkill, setAudioSkill] = useState(() => localStorage.getItem('lb-canvas-audio-skill') || 'podcast_script');
  const [audioDuration, setAudioDuration] = useState(() => parseInt(localStorage.getItem('lb-canvas-audio-dur') || '15'));
  const [audioVoices, setAudioVoices] = useState(() => localStorage.getItem('lb-canvas-audio-voices') || 'mf');
  const [audioAccent, setAudioAccent] = useState(() => localStorage.getItem('lb-canvas-audio-accent') || 'us');

  // Visual popover config
  type DiagramType = 'auto' | 'mindmap' | 'flowchart' | 'timeline' | 'classDiagram' | 'quadrant';
  const [visualType, setVisualType] = useState<DiagramType>(() => (localStorage.getItem('lb-canvas-visual-type') as DiagramType) || 'auto');

  // Docs popover config
  const [skills, setSkills] = useState<Skill[]>([]);
  const [styleFormats, setStyleFormats] = useState<FormatOption[]>([]);
  const [docsSkill, setDocsSkill] = useState(() => localStorage.getItem('lb-canvas-docs-skill') || 'summary');
  const [docsStyle, setDocsStyle] = useState(() => localStorage.getItem('lb-canvas-docs-style') || 'professional');
  const [docsTopic, setDocsTopic] = useState('');

  // Strip @chat qualifier from topic strings before sending to API
  const stripAtChat = (s: string) => s.replace(/\s*@chat\s*/gi, '').trim();

  // @chat qualifier detection — visual feedback when user types @chat in topic fields
  const hasAtChatDocs = useMemo(() => /\b@chat\b/i.test(docsTopic), [docsTopic]);
  const hasAtChatQuiz = useMemo(() => /\b@chat\b/i.test(quizTopic), [quizTopic]);

  // PPTX popover config (theme can be built-in id OR custom template id prefixed with 'tpl:')
  const [pptxTheme, setPptxTheme] = useState(() => localStorage.getItem('lb-canvas-pptx-theme') || 'light');
  const [customTemplates, setCustomTemplates] = useState<{ id: string; name: string }[]>([]);

  // PDF popover config
  type PdfLayout = 'clean' | 'academic' | 'report';
  const [pdfLayout, setPdfLayout] = useState<PdfLayout>(() => (localStorage.getItem('lb-canvas-pdf-layout') as PdfLayout) || 'clean');

  // Discover popover config
  const [discoverQuery, setDiscoverQuery] = useState('');

  // Load skills, style formats, and custom templates for popovers
  useEffect(() => {
    skillsService.list().then(setSkills).catch(() => {});
    writingService.getFormats().then(setStyleFormats).catch(() => {});
    exportService.listTemplates().then((t: { id: string; name: string }[]) => setCustomTemplates(t.map((x: { id: string; name: string }) => ({ id: x.id, name: x.name })))).catch(() => {});
  }, []);

  // Save popover settings to localStorage on change
  useEffect(() => { localStorage.setItem('lb-canvas-quiz-count', String(quizCount)); }, [quizCount]);
  useEffect(() => { localStorage.setItem('lb-canvas-quiz-diff', quizDifficulty); }, [quizDifficulty]);
  useEffect(() => { localStorage.setItem('lb-canvas-audio-skill', audioSkill); }, [audioSkill]);
  useEffect(() => { localStorage.setItem('lb-canvas-audio-dur', String(audioDuration)); }, [audioDuration]);
  useEffect(() => { localStorage.setItem('lb-canvas-audio-voices', audioVoices); }, [audioVoices]);
  useEffect(() => { localStorage.setItem('lb-canvas-audio-accent', audioAccent); }, [audioAccent]);
  useEffect(() => { localStorage.setItem('lb-canvas-visual-type', visualType); }, [visualType]);
  useEffect(() => { localStorage.setItem('lb-canvas-docs-skill', docsSkill); }, [docsSkill]);
  useEffect(() => { localStorage.setItem('lb-canvas-docs-style', docsStyle); }, [docsStyle]);
  useEffect(() => { localStorage.setItem('lb-canvas-pptx-theme', pptxTheme); }, [pptxTheme]);
  useEffect(() => { localStorage.setItem('lb-canvas-pdf-layout', pdfLayout); }, [pdfLayout]);

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

  // Scroll to show the newest item when items are added
  const prevItemCount = useRef(ctx.canvasItems.length);
  useEffect(() => {
    if (ctx.canvasItems.length > prevItemCount.current && contentAreaRef.current) {
      const timer = setTimeout(() => {
        // Find the last item's DOM element and scroll it into view at the top
        const container = contentAreaRef.current;
        if (!container) return;
        const lastChild = container.lastElementChild as HTMLElement | null;
        if (lastChild) {
          lastChild.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
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
    ctx.setGenerationStatus('generating');
    try {
      const content = getPrimaryContent();
      const title = getPrimaryTitle();
      await visualService.generateSmartStream(
        ctx.selectedNotebookId,
        content,
        visualType,
        // onPrimary — add visual to canvas
        (diagram) => {
          ctx.addCanvasItem({
            type: 'visual',
            title: diagram.title || `Visual: ${title}`,
            content: diagram.svg || diagram.code || '',
            collapsed: true,
            metadata: { notebookId: ctx.selectedNotebookId },
          });
          setActionLoading(null);
          ctx.setGenerationStatus('complete');
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
          ctx.setGenerationStatus('error');
        }
      );
    } catch (err) {
      console.error('Canvas visual failed:', err);
      setActionLoading(null);
      ctx.setGenerationStatus('error');
    }
  };

  const handleCreateAudio = async () => {
    if (!ctx.selectedNotebookId) return;
    setActionLoading('audio');
    ctx.setGenerationStatus('generating');
    const itemId = `audio-${Date.now()}`;
    const audioFormatLabels: Record<string, string> = {
      podcast_script: 'Conversation', debate: 'Debate Format', interview: 'Interview Format',
      storytelling: 'Story Format', feynman_curriculum: 'Feynman Lesson',
    };
    const formatLabel = audioFormatLabels[audioSkill] || 'Audio';
    // Compute topic early so we can use it in the title
    const content = getPrimaryContent();
    const topic = content.substring(0, 200).replace(/[#*_\n]/g, ' ').trim();
    const shortTopic = topic.length > 50 ? topic.substring(0, 47).trim() + '…' : topic;
    // Add placeholder immediately
    ctx.addCanvasItem({
      id: itemId,
      type: 'audio',
      title: shortTopic ? `${formatLabel}: ${shortTopic}` : `Podcast: ${formatLabel}`,
      content: '',
      collapsed: true,
      status: 'generating',
      metadata: { notebookId: ctx.selectedNotebookId },
    });
    try {
      const voiceMap: Record<string, [string, string]> = { mf: ['male', 'female'], fm: ['female', 'male'], mm: ['male', 'male'], ff: ['female', 'female'] };
      const [h1, h2] = voiceMap[audioVoices] || ['male', 'female'];
      const result = await audioService.generate({
        notebook_id: ctx.selectedNotebookId,
        topic,
        duration_minutes: audioDuration,
        skill_id: audioSkill,
        host1_gender: h1,
        host2_gender: h2,
        accent: audioAccent,
        ...(ctx.chatContext ? { chat_context: ctx.chatContext } : {}),
      });
      // Update with real audio_id so the inline player can poll
      ctx.updateCanvasItem(itemId, {
        status: 'processing',
        metadata: { audioId: result.audio_id, notebookId: ctx.selectedNotebookId },
      });
      ctx.setGenerationStatus('complete');
      window.dispatchEvent(new CustomEvent('studioAudioRefresh'));
    } catch (err: any) {
      console.error('Canvas audio failed:', err);
      ctx.updateCanvasItem(itemId, {
        status: 'error',
        metadata: { notebookId: ctx.selectedNotebookId, errorMessage: err.message || 'Audio generation failed' },
      });
      ctx.addToast({ type: 'error', title: 'Audio generation failed', message: err.message || 'Unknown error' });
      ctx.setGenerationStatus('error');
    }
    setActionLoading(null);
  };

  const handleCreateQuiz = async () => {
    if (!ctx.selectedNotebookId) return;
    setActionLoading('quiz');
    ctx.setGenerationStatus('generating');
    const itemId = `quiz-${Date.now()}`;
    ctx.addCanvasItem({
      id: itemId,
      type: 'quiz',
      title: 'Quiz',
      content: '',
      collapsed: true,
      status: 'generating',
      metadata: { notebookId: ctx.selectedNotebookId },
    });
    try {
      const content = getPrimaryContent();
      const topic = stripAtChat(quizTopic) || content.substring(0, 300).replace(/[#*_\n]/g, ' ').trim();
      const quiz = await quizService.generate(ctx.selectedNotebookId, quizCount, quizDifficulty, topic, ctx.chatContext || undefined);
      const quizHtml = quiz.questions.map((q, i) => {
        const optionsHtml = q.options
          ? q.options.map((opt, j) => `<li>${String.fromCharCode(65 + j)}. ${opt}</li>`).join('')
          : '';
        return `<div class="mb-4"><p><strong>Q${i + 1}.</strong> ${q.question}</p>${optionsHtml ? `<ul>${optionsHtml}</ul>` : ''}<details class="mt-1"><summary class="text-sm text-blue-600 cursor-pointer">Show Answer</summary><p class="text-sm text-green-700 dark:text-green-400 mt-1"><strong>Answer:</strong> ${q.answer}</p><p class="text-sm text-gray-600 dark:text-gray-400">${q.explanation}</p></details></div>`;
      }).join('');
      ctx.updateCanvasItem(itemId, {
        title: `Quiz: ${quiz.topic || getPrimaryTitle()}`,
        content: quizHtml,
        status: 'complete',
      });
      ctx.setGenerationStatus('complete');
    } catch (err: any) {
      console.error('Canvas quiz failed:', err);
      ctx.updateCanvasItem(itemId, { status: 'error', content: '' });
      ctx.addToast({ type: 'error', title: 'Quiz generation failed', message: err.message || 'Unknown error' });
      ctx.setGenerationStatus('error');
    }
    setActionLoading(null);
  };

  const handleGenerateDocs = async () => {
    if (!ctx.selectedNotebookId) return;
    setActionLoading('docs');
    ctx.setGenerationStatus('generating');
    const skillName = skills.find(s => s.skill_id === docsSkill)?.name || 'Document';
    const itemId = `doc-${Date.now()}`;
    ctx.addCanvasItem({
      id: itemId,
      type: 'document',
      title: `Document: ${skillName}`,
      content: '',
      collapsed: true,
      status: 'generating',
      metadata: { notebookId: ctx.selectedNotebookId },
    });
    try {
      const result = await contentService.generate({
        notebook_id: ctx.selectedNotebookId,
        skill_id: docsSkill,
        topic: stripAtChat(docsTopic) || undefined,
        style: docsStyle,
        ...(ctx.chatContext ? { chat_context: ctx.chatContext } : {}),
      });
      // Replace placeholder with final content (need full replace to set sourceNames/relevanceScores)
      ctx.removeCanvasItem(itemId);
      ctx.addCanvasItem({
        type: 'document',
        title: `Document: ${result.skill_name || skillName}`,
        content: result.content,
        collapsed: true,
        sourceNames: result.source_names || [],
        relevanceScores: result.relevance_scores || {},
        status: 'complete',
        metadata: { notebookId: ctx.selectedNotebookId },
      });
      ctx.setGenerationStatus('complete');
    } catch (err: any) {
      console.error('Canvas docs generation failed:', err);
      ctx.updateCanvasItem(itemId, { status: 'error', content: '' });
      ctx.addToast({ type: 'error', title: 'Document generation failed', message: err.message || 'Unknown error' });
      ctx.setGenerationStatus('error');
    }
    setActionLoading(null);
  };

  const handleExportPPTX = () => {
    // Dispatch event to open ExportModal with content pre-loaded and theme/template pre-selected
    const isCustom = pptxTheme.startsWith('tpl:');
    const detail: Record<string, string> = {
      content: getPrimaryContent(),
      title: getPrimaryTitle(),
    };
    if (isCustom) {
      detail.customTemplateId = pptxTheme.slice(4);
      detail.theme = 'light'; // fallback theme colors for custom templates
    } else {
      detail.theme = pptxTheme;
    }
    window.dispatchEvent(new CustomEvent('openExportModal', { detail }));
  };

  const handleExportPDF = async () => {
    setActionLoading('pdf');
    try {
      const content = getPrimaryContent();
      const title = getPrimaryTitle();
      await contentService.downloadAsPDF(content, title, title.toLowerCase().replace(/\s+/g, '-'), pdfLayout);
    } catch (err) {
      console.error('PDF download failed:', err);
      ctx.addToast({ type: 'error', title: 'PDF download failed' });
    }
    setActionLoading(null);
  };

  const handleCrossNotebook = async () => {
    if (!ctx.selectedNotebookId) return;
    setActionLoading('crossnb');
    ctx.setGenerationStatus('generating');
    try {
      const content = getPrimaryContent();
      // Use explicit discover query if provided, otherwise extract from content
      const query = discoverQuery.trim() || content.substring(0, 500).replace(/[#*_\n]/g, ' ').trim();
      if (!query) {
        ctx.addToast({ type: 'info', title: 'Nothing to discover', message: 'Type a query or add content first' });
        setActionLoading(null);
        ctx.setGenerationStatus('idle');
        return;
      }
      const result = await curatorService.chat(
        `Find connections and insights across all my notebooks related to: ${query}`,
        ctx.selectedNotebookId
      );
      if (result?.response) {
        ctx.addCanvasItem({
          type: 'chat-response',
          title: 'Cross-Notebook Discovery',
          content: result.response,
          collapsed: true,
          metadata: { notebookId: ctx.selectedNotebookId },
        });
      }
      ctx.setGenerationStatus('complete');
    } catch (err: any) {
      console.error('Cross-notebook discovery failed:', err);
      ctx.addToast({ type: 'error', title: 'Cross-notebook search failed', message: err.message || 'Unknown error' });
      ctx.setGenerationStatus('error');
    }
    setActionLoading(null);
  };

  const ACTION_HANDLERS: Record<string, () => void | Promise<void>> = {
    docs: handleGenerateDocs,
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
        collapsed: true,
        metadata: { notebookId: ctx.selectedNotebookId },
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
        {/* Onboarding hint — shown once */}
        {showCanvasHint && (
          <div className="px-3 pt-2 pb-1 animate-slide-up">
            <div className="flex items-start gap-2.5 p-2.5 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800/40 rounded-lg">
              <span className="text-blue-500 dark:text-blue-400 mt-0.5 flex-shrink-0">
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
              </span>
              <div className="flex-1 min-w-0">
                <p className="text-xs font-medium text-blue-800 dark:text-blue-200">Canvas Workspace</p>
                <p className="text-[11px] text-blue-600 dark:text-blue-300 mt-0.5">Use the pills below to create visuals, audio, quizzes, or slides from your content. Chat to refine or ask follow-ups.</p>
              </div>
              <button
                onClick={() => { setShowCanvasHint(false); localStorage.setItem('lb-canvas-hint-seen', '1'); }}
                className="text-blue-400 hover:text-blue-600 dark:hover:text-blue-200 text-xs flex-shrink-0 p-0.5"
              >
                &#x2715;
              </button>
            </div>
          </div>
        )}
        {/* Popover panels — rendered above the pill row */}
        <div className="relative">
          {/* Docs popover */}
          <CanvasActionPopover
            isOpen={activePopover === 'docs'}
            onClose={() => setActivePopover(null)}
            title="Generate Document"
            generateLabel="Generate"
            generating={actionLoading === 'docs'}
            onGenerate={() => { setActivePopover(null); handleGenerateDocs(); }}
          >
            <div className="space-y-2.5">
              <div>
                <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Content Type</label>
                <select value={docsSkill} onChange={e => setDocsSkill(e.target.value)} className="w-full px-2.5 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white">
                  {skills.filter(s => !['podcast_script', 'debate', 'interview', 'storytelling'].includes(s.skill_id)).map(s => (
                    <option key={s.skill_id} value={s.skill_id}>{s.name}</option>
                  ))}
                </select>
                {skills.find(s => s.skill_id === docsSkill)?.description && (
                  <p className="mt-1 text-[11px] text-gray-400 dark:text-gray-500 leading-snug">{skills.find(s => s.skill_id === docsSkill)!.description}</p>
                )}
              </div>
              {styleFormats.length > 0 && (
                <div>
                  <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Style</label>
                  <div className="flex flex-wrap gap-1">
                    {styleFormats.map(f => (
                      <button key={f.value} onClick={() => setDocsStyle(f.value)} className={`px-2 py-1 text-[11px] rounded-lg border transition-colors ${docsStyle === f.value ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300' : 'border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'}`}>{f.label}</button>
                    ))}
                  </div>
                </div>
              )}
              <div>
                <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Topic <span className="text-gray-400">(optional)</span></label>
                <input type="text" value={docsTopic} onChange={e => setDocsTopic(e.target.value)} placeholder={ctx.chatContext ? 'e.g., @chat transformers attention' : 'e.g., AI use cases in healthcare'} className={`w-full px-2.5 py-1.5 text-xs border rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400 ${hasAtChatDocs ? 'border-purple-400 dark:border-purple-500 ring-1 ring-purple-300 dark:ring-purple-600' : 'border-gray-300 dark:border-gray-600'}`} />
                {hasAtChatDocs && (
                  <p className="mt-1 text-[10px] text-purple-600 dark:text-purple-400 flex items-center gap-1">
                    <MessageCircle className="w-3 h-3" />
                    Chat context will focus this generation
                  </p>
                )}
              </div>
              {ctx.chatContext && !hasAtChatDocs && (
                <div className="flex items-center gap-1.5 px-2 py-1 bg-purple-50 dark:bg-purple-900/20 border border-purple-200 dark:border-purple-800 rounded-lg">
                  <MessageCircle className="w-3 h-3 text-purple-500 dark:text-purple-400" />
                  <span className="text-[10px] text-purple-700 dark:text-purple-300">Using chat context</span>
                </div>
              )}
            </div>
          </CanvasActionPopover>

          {/* Visual popover */}
          <CanvasActionPopover
            isOpen={activePopover === 'visual'}
            onClose={() => setActivePopover(null)}
            title="Create Visual"
            generateLabel="Generate"
            generating={actionLoading === 'visual'}
            onGenerate={() => { setActivePopover(null); handleCreateVisual(); }}
          >
            <div>
              <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1.5">Visual Type</label>
              <div className="grid grid-cols-3 gap-1.5">
                {([
                  { type: 'auto' as const, icon: <Sparkles className="w-3.5 h-3.5" />, label: 'Auto' },
                  { type: 'mindmap' as const, icon: <Brain className="w-3.5 h-3.5" />, label: 'Mindmap' },
                  { type: 'flowchart' as const, icon: <GitBranch className="w-3.5 h-3.5" />, label: 'Flow' },
                  { type: 'timeline' as const, icon: <CalendarDays className="w-3.5 h-3.5" />, label: 'Timeline' },
                  { type: 'classDiagram' as const, icon: <Network className="w-3.5 h-3.5" />, label: 'Hierarchy' },
                  { type: 'quadrant' as const, icon: <BarChart3 className="w-3.5 h-3.5" />, label: 'Compare' },
                ]).map(opt => (
                  <button key={opt.type} onClick={() => setVisualType(opt.type)} className={`flex items-center justify-center gap-1 px-2 py-1.5 text-[11px] rounded-lg border transition-colors ${visualType === opt.type ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300' : 'border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'}`}>
                    {opt.icon}
                    <span>{opt.label}</span>
                  </button>
                ))}
              </div>
            </div>
          </CanvasActionPopover>

          {/* Audio popover */}
          <CanvasActionPopover
            isOpen={activePopover === 'audio'}
            onClose={() => setActivePopover(null)}
            title="Generate Audio"
            generateLabel="Generate"
            generating={actionLoading === 'audio'}
            onGenerate={() => { setActivePopover(null); handleCreateAudio(); }}
          >
            <div className="space-y-2.5">
              <div>
                <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Style</label>
                <select value={audioSkill} onChange={e => setAudioSkill(e.target.value)} className="w-full px-2.5 py-1.5 text-xs border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white">
                  {skills.filter(s => ['podcast_script', 'debate', 'interview', 'storytelling', 'feynman_curriculum'].includes(s.skill_id)).map(s => (
                    <option key={s.skill_id} value={s.skill_id}>{s.name}</option>
                  ))}
                </select>
                {audioSkill === 'feynman_curriculum' && (
                  <p className="mt-1 text-[11px] text-blue-500 dark:text-blue-400 leading-snug">4-part progressive teaching: Foundation → Building → First Principles → Mastery (recommended: 30-45 min)</p>
                )}
              </div>
              <div>
                <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Duration: {audioDuration} min</label>
                <input type="range" min="5" max={audioSkill === 'feynman_curriculum' ? 45 : 30} value={audioDuration} onChange={e => setAudioDuration(parseInt(e.target.value))} className="w-full h-1.5 accent-blue-600" />
              </div>
              <div>
                <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Voices</label>
                <div className="flex gap-1">
                  {([['mf', 'M / F'], ['fm', 'F / M'], ['mm', 'M / M'], ['ff', 'F / F']] as const).map(([val, label]) => (
                    <button key={val} onClick={() => setAudioVoices(val)} className={`flex-1 px-2 py-1 text-[11px] rounded-lg border transition-colors text-center ${audioVoices === val ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300' : 'border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'}`}>{label}</button>
                  ))}
                </div>
              </div>
              <div>
                <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Accent</label>
                <div className="flex gap-1">
                  {([['us', 'American'], ['uk', 'British']] as const).map(([val, label]) => (
                    <button key={val} onClick={() => setAudioAccent(val)} className={`flex-1 px-2 py-1 text-[11px] rounded-lg border transition-colors text-center ${audioAccent === val ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300' : 'border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'}`}>{label}</button>
                  ))}
                </div>
              </div>
            </div>
          </CanvasActionPopover>

          {/* Quiz popover */}
          <CanvasActionPopover
            isOpen={activePopover === 'quiz'}
            onClose={() => setActivePopover(null)}
            title="Create Quiz"
            generateLabel="Generate"
            generating={actionLoading === 'quiz'}
            onGenerate={() => { setActivePopover(null); handleCreateQuiz(); }}
          >
            <div className="space-y-2.5">
              <div>
                <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Questions: {quizCount}</label>
                <input type="range" min="3" max="10" value={quizCount} onChange={e => setQuizCount(parseInt(e.target.value))} className="w-full h-1.5 accent-blue-600" />
              </div>
              <div>
                <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Difficulty</label>
                <div className="flex gap-1">
                  {(['easy', 'medium', 'hard'] as const).map(d => (
                    <button key={d} onClick={() => setQuizDifficulty(d)} className={`flex-1 px-2 py-1.5 text-[11px] rounded-lg border transition-colors capitalize ${quizDifficulty === d ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300' : 'border-gray-200 dark:border-gray-600 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'}`}>{d}</button>
                  ))}
                </div>
              </div>
              <div>
                <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1">Topic <span className="text-gray-400">(optional)</span></label>
                <input type="text" value={quizTopic} onChange={e => setQuizTopic(e.target.value)} placeholder={ctx.chatContext ? 'e.g., @chat transformers attention' : 'Auto-detected from content'} className={`w-full px-2.5 py-1.5 text-xs border rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400 ${hasAtChatQuiz ? 'border-purple-400 dark:border-purple-500 ring-1 ring-purple-300 dark:ring-purple-600' : 'border-gray-300 dark:border-gray-600'}`} />
                {hasAtChatQuiz && (
                  <p className="mt-1 text-[10px] text-purple-600 dark:text-purple-400 flex items-center gap-1">
                    <MessageCircle className="w-3 h-3" />
                    Chat context will focus this generation
                  </p>
                )}
              </div>
              {ctx.chatContext && !hasAtChatQuiz && (
                <div className="flex items-center gap-1.5 px-2 py-1 bg-purple-50 dark:bg-purple-900/20 border border-purple-200 dark:border-purple-800 rounded-lg">
                  <MessageCircle className="w-3 h-3 text-purple-500 dark:text-purple-400" />
                  <span className="text-[10px] text-purple-700 dark:text-purple-300">Using chat context</span>
                </div>
              )}
            </div>
          </CanvasActionPopover>

          {/* PPTX popover */}
          <CanvasActionPopover
            isOpen={activePopover === 'pptx'}
            onClose={() => setActivePopover(null)}
            title="Create Slides"
            generateLabel="Build Slides"
            onGenerate={() => { setActivePopover(null); handleExportPPTX(); }}
          >
            <div className="space-y-2.5">
              <div>
                <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1.5">Theme</label>
                <div className="grid grid-cols-2 gap-1.5">
                  {([
                    { id: 'light', label: 'Light', cls: 'bg-white border-gray-300 text-gray-800' },
                    { id: 'dark', label: 'Dark', cls: 'bg-gray-800 border-gray-600 text-gray-100' },
                    { id: 'corporate', label: 'Corporate', cls: 'bg-blue-50 border-blue-300 text-blue-900' },
                    { id: 'academic', label: 'Academic', cls: 'bg-amber-50 border-amber-300 text-amber-900' },
                  ]).map(t => (
                    <button key={t.id} onClick={() => setPptxTheme(t.id)} className={`px-2.5 py-1.5 text-[11px] font-medium rounded-lg border transition-all ${t.cls} ${pptxTheme === t.id ? 'ring-2 ring-blue-500 ring-offset-1' : 'opacity-60 hover:opacity-100'}`}>{t.label}</button>
                  ))}
                </div>
              </div>
              {customTemplates.length > 0 && (
                <div>
                  <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1.5">Custom Templates</label>
                  <div className="grid grid-cols-2 gap-1.5">
                    {customTemplates.map(t => (
                      <button key={t.id} onClick={() => setPptxTheme(`tpl:${t.id}`)} className={`px-2.5 py-1.5 text-[11px] font-medium rounded-lg border transition-all border-purple-300 dark:border-purple-600 text-purple-800 dark:text-purple-200 bg-purple-50 dark:bg-purple-900/20 ${pptxTheme === `tpl:${t.id}` ? 'ring-2 ring-purple-500 ring-offset-1' : 'opacity-60 hover:opacity-100'}`}>{t.name}</button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </CanvasActionPopover>

          {/* PDF popover */}
          <CanvasActionPopover
            isOpen={activePopover === 'pdf'}
            onClose={() => setActivePopover(null)}
            title="Download PDF"
            generateLabel="Download"
            generating={actionLoading === 'pdf'}
            onGenerate={() => { setActivePopover(null); handleExportPDF(); }}
          >
            <div>
              <label className="block text-xs text-gray-500 dark:text-gray-400 mb-1.5">Layout</label>
              <div className="grid grid-cols-3 gap-1.5">
                {([
                  { id: 'clean' as const, label: 'Clean', desc: 'Minimal, modern' },
                  { id: 'academic' as const, label: 'Academic', desc: 'Serif, numbered' },
                  { id: 'report' as const, label: 'Report', desc: 'Cover page, TOC' },
                ]).map(l => (
                  <button key={l.id} onClick={() => setPdfLayout(l.id)} className={`px-2 py-2 text-center rounded-lg border transition-colors ${pdfLayout === l.id ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20' : 'border-gray-200 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700'}`}>
                    <span className={`block text-[11px] font-medium ${pdfLayout === l.id ? 'text-blue-700 dark:text-blue-300' : 'text-gray-700 dark:text-gray-300'}`}>{l.label}</span>
                    <span className="block text-[10px] text-gray-400 mt-0.5">{l.desc}</span>
                  </button>
                ))}
              </div>
            </div>
          </CanvasActionPopover>

          {/* Discover popover */}
          <CanvasActionPopover
            isOpen={activePopover === 'crossnb'}
            onClose={() => setActivePopover(null)}
            title="Cross-Notebook Discovery"
            generateLabel="Discover"
            generating={actionLoading === 'crossnb'}
            onGenerate={() => { setActivePopover(null); handleCrossNotebook(); }}
          >
            <div className="space-y-2">
              <p className="text-[11px] text-gray-500 dark:text-gray-400">Search across all your notebooks for connections, patterns, and related insights.</p>
              <input
                type="text"
                value={discoverQuery}
                onChange={e => setDiscoverQuery(e.target.value)}
                placeholder="What do you want to explore? (or leave blank to use current content)"
                className="w-full px-2.5 py-1.5 text-xs border border-gray-200 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-blue-400"
                onKeyDown={e => { if (e.key === 'Enter') { setActivePopover(null); handleCrossNotebook(); } }}
              />
            </div>
          </CanvasActionPopover>
        </div>

        {/* Action pill row */}
        <div className="flex items-center gap-1.5 px-3 py-2 overflow-x-auto scrollbar-hide">
          {CANVAS_ACTIONS.map(action => {
            const enabled = action.enabled(ctx.canvasItems, ctx.selectedNotebookId);
            const loading = actionLoading === action.id;
            const isActive = activePopover === action.id;
            const CANVAS_TYPE_MAP: Record<string, string> = { docs: 'document', audio: 'audio', video: 'video', visual: 'visual', quiz: 'quiz' };
            const canvasType = CANVAS_TYPE_MAP[action.id];
            const working = loading || (canvasType && ctx.canvasItems.some(
              item => item.type === canvasType && (item.status === 'generating' || item.status === 'processing')
            ));
            // Actions that need a popover vs direct-fire
            const hasPopover = ['docs', 'visual', 'audio', 'quiz', 'pptx', 'pdf', 'crossnb'].includes(action.id);
            return (
              <button
                key={action.id}
                onClick={() => {
                  if (loading || !enabled) return;
                  if (hasPopover) {
                    setActivePopover(isActive ? null : action.id);
                  } else {
                    setActivePopover(null);
                    ACTION_HANDLERS[action.id]?.();
                  }
                }}
                disabled={!enabled || (!!actionLoading && !isActive)}
                className={`flex items-center gap-1 px-2.5 py-1.5 rounded-full text-xs font-medium whitespace-nowrap transition-all ${
                  (() => {
                    const ACTION_COLORS: Record<string, { working: string; active: string }> = {
                      docs:    { working: 'bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400',    active: 'bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 ring-1 ring-blue-400' },
                      audio:   { working: 'bg-violet-100 dark:bg-violet-900/30 text-violet-600 dark:text-violet-400', active: 'bg-violet-100 dark:bg-violet-900/30 text-violet-600 dark:text-violet-400 ring-1 ring-violet-400' },
                      video:   { working: 'bg-rose-100 dark:bg-rose-900/30 text-rose-600 dark:text-rose-400',    active: 'bg-rose-100 dark:bg-rose-900/30 text-rose-600 dark:text-rose-400 ring-1 ring-rose-400' },
                      visual:  { working: 'bg-amber-100 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400', active: 'bg-amber-100 dark:bg-amber-900/30 text-amber-600 dark:text-amber-400 ring-1 ring-amber-400' },
                      quiz:    { working: 'bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400', active: 'bg-emerald-100 dark:bg-emerald-900/30 text-emerald-600 dark:text-emerald-400 ring-1 ring-emerald-400' },
                      pptx:    { working: 'bg-indigo-100 dark:bg-indigo-900/30 text-indigo-600 dark:text-indigo-400', active: 'bg-indigo-100 dark:bg-indigo-900/30 text-indigo-600 dark:text-indigo-400 ring-1 ring-indigo-400' },
                      pdf:     { working: 'bg-gray-200 dark:bg-gray-700 text-gray-600 dark:text-gray-300',       active: 'bg-gray-200 dark:bg-gray-700 text-gray-600 dark:text-gray-300 ring-1 ring-gray-400' },
                      crossnb: { working: 'bg-teal-100 dark:bg-teal-900/30 text-teal-600 dark:text-teal-400',    active: 'bg-teal-100 dark:bg-teal-900/30 text-teal-600 dark:text-teal-400 ring-1 ring-teal-400' },
                    };
                    const c = ACTION_COLORS[action.id] || ACTION_COLORS.docs;
                    if (working) return `${c.working} animate-pulse`;
                    if (isActive) return c.active;
                    if (enabled && !actionLoading) return 'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600 hover:shadow-sm';
                    return 'bg-gray-50 dark:bg-gray-800 text-gray-400 dark:text-gray-600 cursor-not-allowed';
                  })()
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
          {chatInput.trim().length > 20 && (
            <WritingAssistBar
              text={chatInput}
              onReplace={(newText) => setChatInput(newText)}
              compact
              className="mb-1.5"
            />
          )}
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
