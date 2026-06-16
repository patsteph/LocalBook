import React, { useState, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import DOMPurify from 'dompurify';
import {
  FileText, Palette, Target, Layers, Mic, MessageSquare, PenLine,
  BookOpen, ChevronDown, X, Video, MoreHorizontal, Download, Presentation, Code, GitCompare,
} from 'lucide-react';
import { useCanvas } from '../canvas/CanvasContext';
import { CanvasItem } from '../canvas/types';
import { contentService } from '../../services/content';
import { exportService, canvasItemToArtifact } from '../../services/export';
import { VisualHeroOverlay, OverlayPosition } from '../shared/VisualHeroOverlay';
import { VisualRegenerateButton } from '../shared/VisualRegenerateButton';
import { VisualEditRegenerateButton } from '../shared/VisualEditRegenerateButton';
import { StudioQuizBlock } from '../shared/FeynmanBlocks';
import { AudioCanvasPlayer } from './AudioCanvasPlayer';
import { FlashcardsCanvasTile } from './FlashcardsCanvasTile';
import { API_BASE_URL } from '../../services/api';
import { RichNoteEditor } from '../RichNoteEditor';
import { FeedbackThumbs } from '../shared/FeedbackThumbs';
import { VisualCriticBadge, VisualFeedbackBar } from '../shared/VisualCriticBadge';
import { VisualIdiomSwap } from '../shared/VisualIdiomSwap';
import { ArtifactRender } from '../artifact/RendererRegistry';

// ─── Type icons ────────────────────────────────────────────────────────────
const iconSm = 'w-3.5 h-3.5';
const TYPE_ICONS: Record<CanvasItem['type'], React.ReactNode> = {
  'document': <FileText className={iconSm} />,
  'visual': <Palette className={iconSm} />,
  'quiz': <Target className={iconSm} />,
  'flashcards': <Layers className={iconSm} />,
  'audio': <Mic className={iconSm} />,
  'video': <Video className={iconSm} />,
  'chat-response': <MessageSquare className={iconSm} />,
  'note': <PenLine className={iconSm} />,
  'html': <Code className={iconSm} />,
  'comparison': <GitCompare className={iconSm} />,
};

// ═══════════════════════════════════════════════════════════════════════════
// Note Editor — now uses RichNoteEditor (imported above)
// ═══════════════════════════════════════════════════════════════════════════

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
  'flashcards': 'border-l-fuchsia-500',
  'audio': 'border-l-green-500',
  'video': 'border-l-rose-500',
  'chat-response': 'border-l-gray-400',
  'note': 'border-l-indigo-400',
  'html': 'border-l-emerald-500',
  'comparison': 'border-l-cyan-500',
};

const TYPE_LABELS: Record<CanvasItem['type'], string> = {
  'document': 'Document',
  'visual': 'Visual',
  'quiz': 'Quiz',
  'flashcards': 'Flash Cards',
  'audio': 'Audio',
  'video': 'Video',
  'chat-response': 'Response',
  'note': 'Note',
  'html': 'HTML',
  'comparison': 'Comparison',
};

// VisualChatInlineContent — SVG renderer + thumbs row + critic badge for v2
// visuals shown in the chat-area canvas card. Mirrors the canvas-overlay
// surface so both paths feel identical.
const VisualChatInlineContent: React.FC<{
  item: CanvasItem;
  downSubmitted: boolean;
  onThumbsDown: () => void;
}> = ({ item, downSubmitted, onThumbsDown }) => {
  const criticScore = item.metadata?.criticScore;
  const templateId = item.metadata?.templateId;
  const v2Path = item.metadata?.v2Path;
  const v2GenerationMs = item.metadata?.v2GenerationMs;
  const notebookId = item.metadata?.notebookId || '';

  // Klein full-bleed visuals get the user-controllable hero overlay:
  // toggle, position, edit text. All other visuals use the bare SVG
  // renderer (the structural skeletons already have their own header band
  // baked into the SVG so they don't need an overlay).
  const isHeroFullBleed = templateId === 'full_bleed_hero';

  // Both Klein full-bleed AND user-directed SVG are prompt-driven and
  // benefit from the Regenerate ↻ and Edit ✎ affordances. (Structural
  // skeletons are content-driven — re-running them would produce the
  // same template, so they don't expose these.)
  const isRegenerable = isHeroFullBleed || templateId === 'user_directed_svg';

  return (
    <div className="space-y-2">
      {isHeroFullBleed ? (
        <VisualHeroOverlay
          itemId={item.id}
          svg={item.content}
          defaultTitle={item.title || ''}
          defaultSubtitle={(item.metadata as any)?.heroSubtitle || ''}
          initialEnabled={(item.metadata as any)?.overlayEnabled}
          initialPosition={(item.metadata as any)?.overlayPosition as OverlayPosition | undefined}
          suggestedPosition={(item.metadata as any)?.suggestedOverlayPosition as OverlayPosition | undefined}
          initialTitle={(item.metadata as any)?.overlayTitle}
          initialSubtitle={(item.metadata as any)?.overlaySubtitle}
        />
      ) : (
        <ArtifactRender
          artifact={{ id: item.id, type: 'svg', payload: item.content }}
          context="chat-inline"
          className="border border-gray-200 dark:border-gray-600 rounded-lg"
        />
      )}
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2 flex-wrap">
          {criticScore && <VisualCriticBadge score={criticScore} />}
          {v2Path && (
            <span className="text-[10px] text-gray-400 dark:text-gray-500 font-mono">
              {v2Path}{templateId ? ` · ${templateId}` : ''}
              {v2GenerationMs ? ` · ${Math.round(v2GenerationMs / 1000)}s` : ''}
            </span>
          )}
          <VisualIdiomSwap
            currentIdiom={templateId}
            notebookId={notebookId}
            originalPrompt={item.metadata?.originalPrompt}
          />
          {/* Prompt-driven visuals (Klein full-bleed AND user-directed SVG)
             get two regeneration affordances:
             - "Regenerate ↻" — same prompt, different seed / generation
             - "Edit ✎" — expands a panel with refinement chips + editable
               textarea, then regenerates with the modified prompt.
             Hidden for structural skeletons (content-driven). */}
          {isRegenerable && (
            <>
              <VisualRegenerateButton
                itemId={item.id}
                notebookId={notebookId}
                originalPrompt={item.metadata?.originalPrompt}
              />
              <VisualEditRegenerateButton
                itemId={item.id}
                notebookId={notebookId}
                originalPrompt={item.metadata?.originalPrompt}
              />
            </>
          )}
        </div>
        <FeedbackThumbs
          kind="curator_feature"
          subjectType="studio_visual"
          subjectId={item.id}
          notebookId={notebookId}
          payload={{
            skill_id: 'visual',
            template_id: templateId,
            v2_path: v2Path,
            critic_overall: criticScore?.overall,
          }}
          size="sm"
          onFeedback={(response) => { if (response === 'down') onThumbsDown(); }}
        />
      </div>
      <VisualFeedbackBar
        visible={downSubmitted}
        notebookId={notebookId}
        subjectId={item.id}
        templateId={templateId}
        originalPrompt={item.metadata?.originalPrompt}
      />
    </div>
  );
};

interface CanvasItemCardProps {
  item: CanvasItem;
  isOnly?: boolean;
}

// ExportMenu — small ⋯ menu for document/note/chat-response cards.
// Replaces the old ChatActionBar's standalone PDF + PPTX pills with a
// per-item affordance: the user is already looking at what they want
// to export. Click outside or pick an item to close.
const ExportMenu: React.FC<{ item: CanvasItem }> = ({ item }) => {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', close);
    return () => document.removeEventListener('mousedown', close);
  }, [open]);

  const title = item.title || 'document';
  const filename = title.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'document';

  // Phase 5: try the backend Playwright path first (works for any Artifact-
  // renderable canvas item). Fall back to the legacy jsPDF path for
  // markdown-y types if the artifact mapping fails (defensive).
  const artifact = canvasItemToArtifact(item);

  const handlePdf = async () => {
    setBusy(true);
    try {
      if (artifact) {
        await exportService.downloadArtifact(artifact, 'pdf', filename);
      } else if (item.content) {
        await contentService.downloadAsPDF(item.content, title, filename, 'clean');
      }
    } catch (err) {
      console.error('PDF download failed:', err);
    } finally {
      setBusy(false);
      setOpen(false);
    }
  };

  const handlePng = async () => {
    if (!artifact) return;
    setBusy(true);
    try {
      await exportService.downloadArtifact(artifact, 'png', filename);
    } catch (err) {
      console.error('PNG download failed:', err);
    } finally {
      setBusy(false);
      setOpen(false);
    }
  };

  const handlePptx = () => {
    if (!item.content) return;
    window.dispatchEvent(new CustomEvent('openExportModal', {
      detail: { content: item.content, title, theme: 'light' },
    }));
    setOpen(false);
  };

  return (
    <div ref={ref} className="relative" onClick={(e) => e.stopPropagation()}>
      <button
        onClick={(e) => { e.stopPropagation(); setOpen(o => !o); }}
        className="p-1 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
        title="Export"
        disabled={busy || !item.content}
      >
        <MoreHorizontal className="w-3.5 h-3.5" />
      </button>
      {open && (
        <div className="absolute right-0 top-full mt-1 z-50 w-44 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-lg py-1">
          <button
            onClick={handlePdf}
            disabled={busy}
            className="w-full flex items-center gap-2 px-3 py-1.5 text-[12px] text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 disabled:cursor-wait"
          >
            <Download className="w-3.5 h-3.5" />
            {busy ? 'Saving…' : 'Download PDF'}
          </button>
          {artifact && (
            <button
              onClick={handlePng}
              disabled={busy}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-[12px] text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50 disabled:cursor-wait"
            >
              <Download className="w-3.5 h-3.5" />
              Download PNG
            </button>
          )}
          {(item.type === 'document' || item.type === 'chat-response' || item.type === 'note') && (
            <button
              onClick={handlePptx}
              disabled={busy}
              className="w-full flex items-center gap-2 px-3 py-1.5 text-[12px] text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-700 disabled:opacity-50"
            >
              <Presentation className="w-3.5 h-3.5" />
              Export to Slides
            </button>
          )}
        </div>
      )}
    </div>
  );
};

// ─── InteractiveQuizModal ─────────────────────────────────────────────────────
// 2026-06-16: interactive HTML quizzes need real width to be usable. The
// chat column is too narrow — questions wrap heavily, options scroll,
// the Check button + feedback land off-screen. Quiz items now render as
// a compact tile in chat and open a centered modal portal when expanded.
const InteractiveQuizModal: React.FC<{
  title: string;
  html: string;
  onClose: () => void;
}> = ({ title, html, onClose }) => {
  // Esc-to-close. Backdrop click also closes via the outer div onClick.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  return createPortal(
    <div
      onClick={onClose}
      className="fixed inset-0 z-[1000] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="bg-white dark:bg-gray-900 rounded-xl shadow-2xl w-full max-w-3xl max-h-[90vh] flex flex-col overflow-hidden"
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700 flex-shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            <Target className="w-4 h-4 text-amber-500 flex-shrink-0" />
            <h2 className="text-sm font-semibold text-gray-800 dark:text-gray-200 truncate">{title}</h2>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors flex-shrink-0"
            title="Close (Esc)"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto bg-white dark:bg-gray-900">
          <ArtifactRender
            artifact={{
              id: 'quiz-modal',
              type: 'interactive-html',
              payload: html,
              title,
            }}
            context="canvas-full"
          />
        </div>
      </div>
    </div>,
    document.body,
  );
};

export const CanvasItemCard: React.FC<CanvasItemCardProps> = ({ item }) => {
  const ctx = useCanvas();
  const [visualThumbsDown, setVisualThumbsDown] = useState(false);
  const hasUnsavedNote = item.type === 'note' && item.content.trim().length > 0;
  // Phase 5: extended export coverage. Anything Artifact-renderable
  // (html, comparison, visual on top of the legacy doc / note / chat-
  // response) gets the ⋮ menu so users can grab PDF / PNG via the
  // unified backend pipeline.
  const isExportable =
    (item.type === 'document' || item.type === 'note' || item.type === 'chat-response' || item.type === 'html' || item.type === 'comparison' || item.type === 'visual')
    && item.status === 'complete'
    && (!!item.content || item.type === 'comparison');

  const handleRemove = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (hasUnsavedNote && !window.confirm('This note has unsaved content. Remove it?')) return;
    ctx.removeCanvasItem(item.id);
  };

  const handleToggle = () => {
    ctx.toggleCanvasItemCollapse(item.id);
  };

  // 2026-06-16: interactive HTML quizzes render in a portal modal instead
  // of inline. Chat-column width was too narrow for the quiz UI to function.
  const isInteractiveQuiz =
    item.type === 'quiz' && !!item.metadata?.interactive_html;

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
  // 2026-06-16: interactive quizzes ALWAYS render as a tombstone in chat
  // and overlay a modal when "expanded" — so the quiz UI gets real width.
  if ((item.collapsed || isInteractiveQuiz) && item.type !== 'note') {
    return (
      <>
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
      {isInteractiveQuiz && !item.collapsed && (
        <InteractiveQuizModal
          title={item.title || 'Quiz'}
          html={item.metadata!.interactive_html as string}
          onClose={handleToggle}
        />
      )}
      </>
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
            {/* Fix #6 (2026-05-23): thumbs on inline canvas items. Only
                shown for completed outputs (not while generating/processing).
                Mirrors the Studio-panel thumbs so chat-only users can give
                feedback without navigating to Studio. Click is contained
                so it doesn't trigger the header collapse. */}
            {item.status === 'complete' && (item.type === 'document' || item.type === 'visual' || item.type === 'quiz' || item.type === 'audio' || item.type === 'video') && (
              <div onClick={(e) => e.stopPropagation()}>
                <FeedbackThumbs
                  kind="curator_feature"
                  subjectType={`studio_${item.type === 'document' ? 'doc' : item.type}`}
                  subjectId={item.metadata?.audioId || item.metadata?.videoId || item.id}
                  notebookId={item.metadata?.notebookId || null}
                  payload={{
                    skill_id: item.type,
                    entry_point: 'canvas',
                    title: item.title,
                  }}
                />
              </div>
            )}
            <span className="text-[10px] text-gray-400 dark:text-gray-500 opacity-0 group-hover:opacity-100 transition-opacity">
              Collapse
            </span>
            {isExportable && <ExportMenu item={item} />}
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
          <RichNoteEditor item={item} compact />
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
          {/* Phase 12 — Perspectives synthesis HTML. Uses the strict
              HtmlArtifactRenderer; lives on metadata.synthesis_html so
              it doesn't collide with Phase 11's metadata.interactive_html. */}
          {item.type === 'document' && item.metadata?.synthesis_html && (
            <ArtifactRender
              artifact={{
                id: item.id,
                type: 'html',
                payload: item.metadata.synthesis_html as string,
                title: item.title,
              }}
              context="canvas-full"
            />
          )}
          {item.type === 'document' && !item.metadata?.synthesis_html && item.content && (
            <ArtifactRender
              artifact={{
                id: item.id,
                type: 'markdown',
                payload: item.content,
                title: item.title,
              }}
              context="canvas-full"
            />
          )}
          {item.type === 'html' && item.content && (
            <ArtifactRender
              artifact={{
                id: item.id,
                type: 'html',
                payload: item.content,
                title: item.title,
              }}
              context="canvas-full"
            />
          )}
          {item.type === 'comparison' && item.metadata?.comparison && (
            <ArtifactRender
              artifact={{
                id: item.id,
                type: 'json:comparison',
                payload: item.metadata.comparison,
                title: item.title,
              }}
              context="canvas-full"
            />
          )}
          {item.type === 'visual' && (
            item.content ? (
              // v2 produces native SVG; legacy template path produces Mermaid.
              // Detect by content shape so each renderer gets the right input.
              item.content.trimStart().startsWith('<svg') ? (
                <VisualChatInlineContent
                  item={item}
                  downSubmitted={visualThumbsDown}
                  onThumbsDown={() => setVisualThumbsDown(true)}
                />
              ) : (
                <ArtifactRender
                  artifact={{ id: item.id, type: 'mermaid', payload: item.content }}
                  context="chat-inline"
                  className="border border-gray-200 dark:border-gray-600 rounded-lg"
                />
              )
            ) : !isGenerating && !isError ? (
              <p className="text-gray-400 text-sm">No visual content</p>
            ) : null
          )}
          {/* Phase 11 — when an interactive HTML composition is present
              (Studio drawer "Render as interactive HTML" toggle), dispatch
              via the sandboxed-iframe renderer. Otherwise fall back to the
              existing StudioQuizBlock path. */}
          {item.type === 'quiz' && item.metadata?.interactive_html && (
            <ArtifactRender
              artifact={{
                id: item.id,
                type: 'interactive-html',
                payload: item.metadata.interactive_html as string,
                title: item.title,
              }}
              context="canvas-full"
            />
          )}
          {item.type === 'quiz' && !item.metadata?.interactive_html && item.content && (
            item.content.trimStart().startsWith('[')
              ? <StudioQuizBlock json={item.content} />
              : <div className="prose dark:prose-invert max-w-none" dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(item.content) }} />
          )}
          {item.type === 'flashcards' && (
            item.metadata?.notebookId ? (
              <FlashcardsCanvasTile
                itemId={item.id}
                notebookId={item.metadata.notebookId}
                topic={item.metadata.topic || ''}
                difficulty={(item.metadata.difficulty as any) || 'medium'}
                count={item.metadata.count || 10}
                chatContext={item.metadata.chatContext}
                tutorGender={item.metadata.tutorGender ?? 'female'}
                tutorAccent={item.metadata.tutorAccent ?? 'us'}
                tutorAutoplay={item.metadata.tutorAutoplay ?? true}
                includeVisuals={item.metadata.includeVisuals ?? false}
                parentStatus={item.status}
                parentError={item.metadata?.errorMessage}
                onStatusChange={(status, errorMessage) => {
                  ctx.updateCanvasItem(item.id, {
                    status,
                    metadata: {
                      ...item.metadata,
                      ...(errorMessage ? { errorMessage } : { errorMessage: null }),
                    },
                  });
                }}
              />
            ) : (
              <div className="flex items-center gap-3 p-3 bg-gray-50 dark:bg-gray-900/40 rounded-lg">
                <div className="flex-shrink-0 w-9 h-9 rounded-full bg-fuchsia-100 dark:bg-fuchsia-900/30 text-fuchsia-500 flex items-center justify-center">
                  <Layers className="w-4 h-4 animate-pulse" />
                </div>
                <div>
                  <p className="text-sm font-medium text-gray-900 dark:text-white">{item.title}</p>
                  <p className="text-xs text-gray-500 dark:text-gray-400">Missing notebook context</p>
                </div>
              </div>
            )
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
            <ArtifactRender
              artifact={{
                id: item.id,
                type: 'markdown',
                payload: item.content,
                title: item.title,
              }}
              context="chat-inline"
            />
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
