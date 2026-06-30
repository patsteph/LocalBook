/**
 * VisualCriticBadge — compact critic-score chip with expandable axis breakdown
 * + thumbs-down feedback bar.
 *
 * Designed to render directly under a v2-generated visual on the canvas card.
 * Shows just the overall score as a small chip; click to expand the 5-axis
 * detail. If the user thumbs-down the visual via the existing FeedbackThumbs,
 * a small textarea appears for them to explain what was wrong — the message
 * is POSTed to /curator/engagement as a `thumbs_down_reason` payload so we
 * capture WHAT failed, not just that it did.
 */
import React, { useCallback, useState } from 'react';
import { emitEvent } from '../../lib/events';
import { localFetch, API_BASE_URL } from '../../services/api';

export interface CriticScoreData {
  overall: number;
  legibility: number;
  hierarchy: number;
  balance: number;
  color_harmony: number;
  message_clarity: number;
  strengths?: string[];
  weaknesses?: string[];
  suggestions?: string[];
}

interface VisualCriticBadgeProps {
  score: CriticScoreData;
  // Optional — when present, the thumbs-down feedback bar posts to engagement
  // with these identifiers so we can correlate the explanation to the visual.
  notebookId?: string;
  subjectId?: string;
}

const scoreColor = (v: number): string => {
  if (v >= 0.8) return 'text-emerald-600 dark:text-emerald-400';
  if (v >= 0.6) return 'text-indigo-600 dark:text-indigo-400';
  if (v >= 0.4) return 'text-amber-600 dark:text-amber-400';
  return 'text-red-600 dark:text-red-400';
};

const scoreBg = (v: number): string => {
  if (v >= 0.8) return 'bg-emerald-100 dark:bg-emerald-900/30';
  if (v >= 0.6) return 'bg-indigo-100 dark:bg-indigo-900/30';
  if (v >= 0.4) return 'bg-amber-100 dark:bg-amber-900/30';
  return 'bg-red-100 dark:bg-red-900/30';
};

const AXES: [keyof CriticScoreData, string][] = [
  ['legibility', 'Legibility'],
  ['hierarchy', 'Hierarchy'],
  ['balance', 'Balance'],
  ['color_harmony', 'Color harmony'],
  ['message_clarity', 'Message clarity'],
];

export const VisualCriticBadge: React.FC<VisualCriticBadgeProps> = ({ score }) => {
  const [expanded, setExpanded] = useState(false);
  // No score means the critic couldn't evaluate (e.g., JSON parse failure).
  // Don't render anything rather than show 0.00.
  if (!score || (score.overall === 0 && AXES.every(([k]) => score[k] === 0))) {
    return null;
  }
  return (
    <div className="flex items-center gap-2 text-xs">
      <button
        type="button"
        onClick={() => setExpanded((x) => !x)}
        className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full ${scoreBg(score.overall)} ${scoreColor(score.overall)} font-medium hover:opacity-80`}
        title="Click for per-axis breakdown"
      >
        <span>Critic {score.overall.toFixed(2)}</span>
        <span className="opacity-60">{expanded ? '▾' : '▸'}</span>
      </button>
      {expanded && (
        <div className="flex flex-wrap items-center gap-1">
          {AXES.map(([key, label]) => {
            const v = (score[key] as number) ?? 0;
            return (
              <span
                key={key}
                className={`text-[10px] px-1.5 py-0.5 rounded ${scoreBg(v)} ${scoreColor(v)}`}
                title={`${label}: ${v.toFixed(2)}`}
              >
                {label[0]}:{v.toFixed(2)}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
};

/**
 * VisualFeedbackBar — pairs with the existing FeedbackThumbs. When the user
 * thumbs-down the visual, surface this bar to collect a one-line "what was
 * wrong" message. Posts to the engagement endpoint so we get specific signal
 * back for tuning. Always rendered (collapsed when not active) so it doesn't
 * cause layout shift.
 */
interface VisualFeedbackBarProps {
  visible: boolean;          // True once the user thumbed-down the visual
  notebookId: string;
  subjectId: string;
  templateId?: string;       // Idiom that was picked, for correlation
  originalPrompt?: string;   // Enables the "Regenerate with my feedback" button
  onSubmitted?: () => void;
}

export const VisualFeedbackBar: React.FC<VisualFeedbackBarProps> = ({
  visible,
  notebookId,
  subjectId,
  templateId,
  originalPrompt,
  onSubmitted,
}) => {
  const [text, setText] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [regenerating, setRegenerating] = useState(false);

  const submit = useCallback(async () => {
    const trimmed = text.trim();
    if (!trimmed || submitting) return;
    setSubmitting(true);
    try {
      await localFetch(`${API_BASE_URL}/curator/engagement`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          notebook_id: notebookId,
          kind: 'curator_feature',
          signal: 'thumbs_down_reason',
          subject_type: 'studio_visual',
          subject_id: subjectId,
          payload: {
            skill_id: 'visual',
            template_id: templateId,
            reason: trimmed,
          },
        }),
      });
      setSubmitted(true);
      onSubmitted?.();
    } catch (err) {
      console.error('Failed to submit visual feedback:', err);
    } finally {
      setSubmitting(false);
    }
  }, [text, submitting, notebookId, subjectId, templateId, onSubmitted]);

  const regenerate = useCallback(() => {
    if (!originalPrompt || !notebookId || regenerating) return;
    setRegenerating(true);
    // Dispatch a global event — App.tsx / canvas listens and runs a new
    // generation with the user's reason appended as refinement directive.
    emitEvent('visualRegenerateWithFeedback', {
      notebookId,
      originalPrompt,
      reason: text.trim(),
      previousSubjectId: subjectId,
      previousTemplateId: templateId,
    });
  }, [originalPrompt, notebookId, regenerating, text, subjectId, templateId]);

  if (!visible) return null;
  if (submitted) {
    return (
      <div className="mt-1.5 flex items-center gap-2 flex-wrap">
        <span className="text-[11px] text-gray-500 dark:text-gray-400 italic">
          Thanks — feedback recorded.
        </span>
        {originalPrompt && (
          <button
            type="button"
            onClick={regenerate}
            disabled={regenerating}
            className="px-2.5 py-1 text-[11px] rounded bg-indigo-500 hover:bg-indigo-600 disabled:opacity-40 text-white font-medium"
            title="Re-run generation with your feedback applied"
          >
            {regenerating ? 'Regenerating…' : '✨ Regenerate with my feedback'}
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="mt-1.5 flex items-stretch gap-2">
      <input
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter') submit();
        }}
        placeholder="What was wrong with this visual? (e.g., 'too cluttered', 'wrong idiom', 'illegible labels')"
        className="flex-1 px-2.5 py-1 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-indigo-400"
        autoFocus
      />
      <button
        type="button"
        onClick={submit}
        disabled={submitting || !text.trim()}
        className="px-2.5 py-1 text-xs rounded bg-indigo-500 hover:bg-indigo-600 disabled:opacity-40 text-white font-medium"
      >
        Send
      </button>
    </div>
  );
};
