import React, { useState, useCallback } from 'react';
import { Sparkles, Expand, Shrink, CheckCheck, PenLine, BookOpen, ArrowRight, X, Loader2 } from 'lucide-react';
import { writingService } from '../services/writing';

interface WritingAction {
  id: string;
  label: string;
  icon: React.ReactNode;
  task: string;
  desc: string;
}

const ACTIONS: WritingAction[] = [
  { id: 'improve', label: 'Improve', icon: <Sparkles className="w-3 h-3" />, task: 'improve', desc: 'Enhance clarity & flow' },
  { id: 'expand', label: 'Expand', icon: <Expand className="w-3 h-3" />, task: 'expand', desc: 'Add detail' },
  { id: 'shorten', label: 'Shorten', icon: <Shrink className="w-3 h-3" />, task: 'summarize', desc: 'Condense' },
  { id: 'proofread', label: 'Fix', icon: <CheckCheck className="w-3 h-3" />, task: 'proofread', desc: 'Grammar & spelling' },
  { id: 'rewrite', label: 'Rewrite', icon: <PenLine className="w-3 h-3" />, task: 'rewrite', desc: 'Rephrase entirely' },
  { id: 'simplify', label: 'Simplify', icon: <BookOpen className="w-3 h-3" />, task: 'simplify', desc: 'Plain language' },
];

interface WritingAssistBarProps {
  /** Current full text of the textarea */
  text: string;
  /** Selected portion of the text (if any) */
  selectedText?: string;
  /** Callback to replace the text (full or selected portion) */
  onReplace: (newText: string, replaceSelection: boolean) => void;
  /** Optional: continue writing from end */
  onContinue?: (continuation: string) => void;
  /** Compact mode for smaller textareas like chat input */
  compact?: boolean;
  /** Optional class */
  className?: string;
}

export const WritingAssistBar: React.FC<WritingAssistBarProps> = ({
  text,
  selectedText,
  onReplace,
  onContinue,
  compact = false,
  className = '',
}) => {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState<string | null>(null);
  const [preview, setPreview] = useState<{ text: string; action: string; isSelection: boolean } | null>(null);

  const handleAction = useCallback(async (action: WritingAction) => {
    const targetText = selectedText?.trim() || text.trim();
    if (!targetText) return;

    setLoading(action.id);
    try {
      const result = await writingService.transformText(
        targetText,
        action.task,
        'professional',
        Math.max(50, Math.min(2000, Math.round(targetText.split(/\s+/).length * (action.task === 'expand' ? 2 : action.task === 'summarize' ? 0.5 : 1.2))))
      );
      if (result.content) {
        setPreview({
          text: result.content,
          action: action.label,
          isSelection: !!selectedText?.trim(),
        });
      }
    } catch (err) {
      console.error('Writing assist failed:', err);
    }
    setLoading(null);
  }, [text, selectedText]);

  const handleContinue = useCallback(async () => {
    if (!text.trim() || !onContinue) return;
    setLoading('continue');
    try {
      const result = await writingService.transformText(
        `Continue writing from where this text ends. Maintain the same voice, style, and topic:\n\n${text}`,
        'expand',
        'professional',
        200
      );
      if (result.content) {
        onContinue(result.content);
      }
    } catch (err) {
      console.error('Continue writing failed:', err);
    }
    setLoading(null);
  }, [text, onContinue]);

  const applyPreview = () => {
    if (!preview) return;
    onReplace(preview.text, preview.isSelection);
    setPreview(null);
    setOpen(false);
  };

  const hasContent = text.trim().length > 0;
  if (!hasContent && !selectedText?.trim()) return null;

  // Preview mode — show the suggested replacement
  if (preview) {
    return (
      <div className={`bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg p-2.5 space-y-2 animate-slide-up ${className}`}>
        <div className="flex items-center justify-between">
          <span className="text-[11px] font-medium text-blue-600 dark:text-blue-400">{preview.action} suggestion{preview.isSelection ? ' (selection)' : ''}</span>
          <button onClick={() => setPreview(null)} className="p-0.5 text-gray-400 hover:text-gray-600"><X className="w-3 h-3" /></button>
        </div>
        <div className="text-xs text-gray-700 dark:text-gray-300 max-h-32 overflow-y-auto whitespace-pre-wrap leading-relaxed bg-white dark:bg-gray-800 rounded-md p-2 border border-gray-200 dark:border-gray-700">
          {preview.text}
        </div>
        <div className="flex items-center gap-1.5">
          <button
            onClick={applyPreview}
            className="flex-1 flex items-center justify-center gap-1 px-2.5 py-1.5 text-xs font-medium bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors"
          >
            <CheckCheck className="w-3 h-3" /> Apply
          </button>
          <button
            onClick={() => setPreview(null)}
            className="px-2.5 py-1.5 text-xs font-medium text-gray-600 dark:text-gray-400 bg-gray-100 dark:bg-gray-700 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
          >
            Discard
          </button>
        </div>
      </div>
    );
  }

  // Trigger button (collapsed state)
  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className={`inline-flex items-center gap-1 text-[11px] font-medium text-purple-600 dark:text-purple-400 hover:text-purple-700 dark:hover:text-purple-300 transition-colors ${className}`}
        title="AI writing assistance"
      >
        <Sparkles className="w-3 h-3" />
        {!compact && 'Writing assist'}
      </button>
    );
  }

  // Action bar (expanded state)
  return (
    <div className={`flex items-center gap-1 animate-slide-up ${compact ? 'flex-wrap' : ''} ${className}`}>
      {ACTIONS.map(action => (
        <button
          key={action.id}
          onClick={() => handleAction(action)}
          disabled={!!loading}
          title={action.desc}
          className={`flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium transition-colors ${
            loading === action.id
              ? 'bg-purple-100 dark:bg-purple-900/30 text-purple-600 dark:text-purple-400'
              : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400 hover:bg-purple-50 dark:hover:bg-purple-900/20 hover:text-purple-600 dark:hover:text-purple-400'
          } disabled:opacity-50`}
        >
          {loading === action.id ? <Loader2 className="w-3 h-3 animate-spin" /> : action.icon}
          {action.label}
        </button>
      ))}
      {onContinue && (
        <button
          onClick={handleContinue}
          disabled={!!loading}
          title="Continue writing"
          className={`flex items-center gap-1 px-2 py-1 rounded-md text-[11px] font-medium transition-colors ${
            loading === 'continue'
              ? 'bg-purple-100 dark:bg-purple-900/30 text-purple-600 dark:text-purple-400'
              : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-400 hover:bg-purple-50 dark:hover:bg-purple-900/20 hover:text-purple-600 dark:hover:text-purple-400'
          } disabled:opacity-50`}
        >
          {loading === 'continue' ? <Loader2 className="w-3 h-3 animate-spin" /> : <ArrowRight className="w-3 h-3" />}
          Continue
        </button>
      )}
      <button
        onClick={() => setOpen(false)}
        className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 rounded-md"
      >
        <X className="w-3 h-3" />
      </button>
    </div>
  );
};
