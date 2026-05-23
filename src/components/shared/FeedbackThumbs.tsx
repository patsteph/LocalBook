/**
 * FeedbackThumbs — universal thumbs-up/down feedback control for any
 * AI-generated surface in LocalBook.
 *
 * 2026-05-23. Pattern modeled on the Claude/ChatGPT response thumbs:
 * one compact control attached to every output the assistant produces,
 * so the curator brain accumulates calibration data without the user
 * having to invoke a separate feedback workflow.
 *
 * All thumbs go through useEngagement → POST /curator/engagement so they
 * land in the same `engagement_events` table that powers Phase 5 brief
 * boosting + Phase 7.2 self-evaluating briefs + Phase 7.5 medium
 * selection + Phase 7.6 source reputation.
 *
 * Usage:
 *   <FeedbackThumbs
 *     kind="curator_feature"
 *     subjectType="chat_answer"
 *     subjectId={message.timestamp.toISOString()}
 *     notebookId={notebookId}
 *     payload={{ length: message.content.length, voice: voice }}
 *   />
 *
 * Conventions:
 * - `kind` is one of the canonical EngagementKind values (see useEngagement.ts)
 * - `subjectType` discriminates which surface produced the output
 *   (chat_answer / brief / studio_doc / studio_quiz / studio_visual /
 *    studio_audio / studio_video / connection / pattern / etc.)
 * - `subjectId` should uniquely identify the specific output instance
 * - `payload` carries any metadata Phase 7 readers may want to slice by
 *   (voice used, output length, notebook, model, etc.)
 *
 * Local state only — the backend has the authoritative record. We don't
 * persist "user thumbed this" across reloads; the next render of the
 * same surface starts fresh. The brain learns from the submitted event.
 */
import React, { useState } from 'react';
import { ThumbsUp, ThumbsDown } from 'lucide-react';
import { useEngagement, EngagementKind } from '../../hooks/useEngagement';

export interface FeedbackThumbsProps {
  /** Top-level kind for engagement_events (default: 'curator_feature'). */
  kind?: EngagementKind;
  /** Discriminator: which surface produced this output. */
  subjectType: string;
  /** Unique id for this specific output (timestamp / db id / hash). */
  subjectId: string;
  /** Notebook context, when applicable. */
  notebookId?: string | null;
  /** Extra metadata for Phase 7 readers (voice, length, kind, etc.). */
  payload?: Record<string, any>;
  /** Optional caller hook fired after successful submit. */
  onFeedback?: (response: 'up' | 'down') => void;
  /** Visual size variant. */
  size?: 'xs' | 'sm';
  /** Optional className passthrough for layout tweaks. */
  className?: string;
}

export const FeedbackThumbs: React.FC<FeedbackThumbsProps> = ({
  kind = 'curator_feature',
  subjectType,
  subjectId,
  notebookId,
  payload,
  onFeedback,
  size = 'xs',
  className = '',
}) => {
  const { capture } = useEngagement();
  const [submitted, setSubmitted] = useState<'up' | 'down' | null>(null);

  const handle = (response: 'up' | 'down') => {
    if (submitted) return;
    setSubmitted(response);
    const signal = response === 'up' ? 'thumbs_up' : 'thumbs_down';
    capture(kind, signal, {
      subject_type: subjectType,
      subject_id: subjectId,
      notebook_id: notebookId || undefined,
      payload: { ...(payload || {}), submitted_at: new Date().toISOString() },
    });
    onFeedback?.(response);
  };

  const iconSize = size === 'xs' ? 'w-3 h-3' : 'w-3.5 h-3.5';
  const padding = size === 'xs' ? 'p-0.5' : 'p-1';

  const baseBtn =
    `${padding} rounded transition-colors disabled:cursor-default`;

  return (
    <div className={`inline-flex items-center gap-0.5 ${className}`}>
      <button
        type="button"
        onClick={() => handle('up')}
        disabled={submitted !== null}
        title={submitted === 'up' ? 'Thanks — noted' : 'Useful'}
        className={`${baseBtn} ${
          submitted === 'up'
            ? 'text-emerald-600 dark:text-emerald-400'
            : submitted === 'down'
              ? 'text-gray-300 dark:text-gray-600'
              : 'text-gray-400 dark:text-gray-500 hover:text-emerald-600 dark:hover:text-emerald-400'
        }`}
      >
        <ThumbsUp className={iconSize} />
      </button>
      <button
        type="button"
        onClick={() => handle('down')}
        disabled={submitted !== null}
        title={submitted === 'down' ? 'Noted — we\'ll do better' : 'Not useful'}
        className={`${baseBtn} ${
          submitted === 'down'
            ? 'text-rose-600 dark:text-rose-400'
            : submitted === 'up'
              ? 'text-gray-300 dark:text-gray-600'
              : 'text-gray-400 dark:text-gray-500 hover:text-rose-600 dark:hover:text-rose-400'
        }`}
      >
        <ThumbsDown className={iconSize} />
      </button>
    </div>
  );
};
