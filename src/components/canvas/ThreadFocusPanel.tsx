/**
 * ThreadFocusPanel — open a thread's REAL content without leaving the canvas.
 *
 * The canvas was a map of what you'd discussed with nothing under the surface: chips
 * deliberately render a compact summary, because the full artifact squished into a
 * ~300×180 tile read as blurry. This is the other half — the "focus" action that was
 * always meant to follow.
 *
 * ⚠️ Why this fetches instead of rendering `node.snapshot`: the snapshot is a STABLE
 * OFFLINE summary, not the artifact. `canvas_artifacts` truncates a document to 600
 * chars and reduces audio/video/quiz to a one-line markdown placeholder. Only `visual`
 * (svg/mermaid) and `infographic` (full payload) carry real content, so those two render
 * straight from the snapshot and everything else goes and gets the real thing.
 *
 * Lives in its own file: JourneyCanvas.tsx is already ~1650 lines, and per the repo's
 * file-size rule a new distinct responsibility gets split out rather than piled on.
 */
import React, { useCallback, useEffect, useState } from 'react';
import { X, RefreshCw, ExternalLink, AlertCircle } from 'lucide-react';

import { ArtifactRender } from '../artifact/RendererRegistry';
import { AudioCanvasPlayer } from '../chat/AudioCanvasPlayer';
import { contentService } from '../../services/content';
import { quizService, type QuizQuestion } from '../../services/quiz';
import { videoService } from '../../services/video';
import type { CanvasNode } from '../../services/canvas';
import type { Artifact } from '../../types/artifact';

/** ref_types whose snapshot already holds the real, renderable artifact. */
const SNAPSHOT_IS_REAL = new Set(['visual', 'infographic']);

interface ThreadFocusPanelProps {
  node: CanvasNode;
  notebookId: string;
  onClose: () => void;
}

type Loaded =
  | { kind: 'artifact'; artifact: Artifact }
  | { kind: 'quiz'; questions: QuizQuestion[]; topic: string; difficulty?: string }
  | { kind: 'audio'; audioId: string }
  | { kind: 'video'; videoId: string };

export const ThreadFocusPanel: React.FC<ThreadFocusPanelProps> = ({ node, notebookId, onClose }) => {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    setLoaded(null);
    try {
      const refId = node.ref_id;
      switch (node.ref_type) {
        case 'audio':
          // The player owns its own status polling + playback; it only needs the ids.
          setLoaded({ kind: 'audio', audioId: refId });
          break;
        case 'video':
          setLoaded({ kind: 'video', videoId: refId });
          break;
        case 'quiz': {
          const quiz = await quizService.get(refId);
          setLoaded({
            kind: 'quiz',
            questions: quiz.questions || [],
            topic: quiz.topic || node.title,
            difficulty: quiz.difficulty,
          });
          break;
        }
        case 'document': {
          const doc = await contentService.get(refId);
          setLoaded({
            kind: 'artifact',
            artifact: {
              id: `doc-${refId}`,
              type: 'markdown',
              payload: doc.content || '_This document has no saved body._',
              title: doc.topic || node.title,
            },
          });
          break;
        }
        default: {
          // visual / infographic / question / source — the snapshot IS the content.
          const snap = node.snapshot as Artifact | undefined;
          if (snap?.type && snap.payload !== undefined) {
            setLoaded({ kind: 'artifact', artifact: { ...snap, title: snap.title || node.title } });
          } else {
            setError('Nothing more to show for this item.');
          }
        }
      }
    } catch (e) {
      console.warn('[ThreadFocusPanel] load failed', e);
      // Falling back to the snapshot beats an error screen — a truncated preview is still
      // a refresher, and this is exactly the case where the artifact was deleted from its
      // store but the canvas node survives until the next Populate.
      const snap = node.snapshot as Artifact | undefined;
      if (snap?.type && snap.payload !== undefined && !SNAPSHOT_IS_REAL.has(node.ref_type)) {
        setLoaded({ kind: 'artifact', artifact: { ...snap, title: snap.title || node.title } });
        setError('Showing the saved preview — the original could not be loaded.');
      } else {
        setError('Could not load this item.');
      }
    } finally {
      setLoading(false);
    }
  }, [node]);

  useEffect(() => { load(); }, [load]);

  // Esc closes — this panel sits over a canvas that swallows most key handling.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="absolute inset-y-0 right-0 z-30 flex w-[min(560px,94%)] flex-col border-l border-gray-200 bg-white shadow-2xl dark:border-gray-700 dark:bg-gray-800">
      <div className="flex items-center justify-between gap-2 border-b border-gray-100 px-3 py-2 dark:border-gray-700">
        <div className="min-w-0">
          <p className="text-[10px] font-medium uppercase tracking-wide text-gray-400">
            {node.ref_type.replace(/_/g, ' ')}
          </p>
          <p className="truncate text-[12px] font-semibold text-gray-700 dark:text-gray-200" title={node.title}>
            {node.title || 'Untitled'}
          </p>
        </div>
        <button
          type="button"
          onClick={onClose}
          className="flex-shrink-0 rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-700"
          title="Close (Esc)"
          aria-label="Close"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="flex-1 overflow-auto p-3">
        {loading && (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-gray-400">
            <RefreshCw className="h-5 w-5 animate-spin" />
            <p className="text-[11px]">Opening…</p>
          </div>
        )}

        {error && (
          <div className="mb-3 flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-700 dark:border-amber-900 dark:bg-amber-950/50 dark:text-amber-300">
            <AlertCircle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {loaded?.kind === 'audio' && (
          <AudioCanvasPlayer
            audioId={loaded.audioId}
            notebookId={notebookId}
            title={node.title || 'Podcast'}
          />
        )}

        {loaded?.kind === 'video' && (
          <video
            className="w-full rounded-lg bg-black"
            controls
            preload="metadata"
            src={videoService.getStreamUrl(loaded.videoId)}
          >
            <track kind="captions" />
          </video>
        )}

        {loaded?.kind === 'artifact' && (
          <ArtifactRender artifact={loaded.artifact} context="canvas-full" />
        )}

        {loaded?.kind === 'quiz' && (
          <QuizRefresher
            questions={loaded.questions}
            topic={loaded.topic}
            difficulty={loaded.difficulty}
          />
        )}
      </div>
    </div>
  );
};

/**
 * Read-only quiz recall — answers hidden until asked for, one at a time.
 *
 * Deliberately NOT the graded `StudioQuizBlock`: this is the canvas's "refresher" affordance,
 * and silently feeding a casual glance-back into the FSRS scheduler would corrupt the review
 * history that the spaced-repetition surfaces depend on. Studying still happens in Studio.
 */
const QuizRefresher: React.FC<{ questions: QuizQuestion[]; topic: string; difficulty?: string }> = ({
  questions, topic, difficulty,
}) => {
  const [revealed, setRevealed] = useState<Set<string>>(new Set());
  const toggle = (id: string) =>
    setRevealed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  if (!questions.length) {
    return <p className="text-[12px] text-gray-500 dark:text-gray-400">This quiz has no saved questions.</p>;
  }

  return (
    <div className="space-y-2">
      <p className="text-[11px] text-gray-400">
        {questions.length} question{questions.length === 1 ? '' : 's'}
        {difficulty ? ` · ${difficulty}` : ''} · {topic}
      </p>
      {questions.map((q, i) => {
        const key = q.id || `q${i}`;
        const open = revealed.has(key);
        return (
          <div key={key} className="rounded-lg border border-gray-200 p-2.5 dark:border-gray-700">
            <p className="text-[12px] font-medium text-gray-800 dark:text-gray-100">
              {i + 1}. {q.question}
            </p>
            {!!q.options?.length && (
              <ul className="mt-1.5 space-y-0.5">
                {q.options.map((opt, oi) => (
                  <li key={oi} className="text-[11.5px] text-gray-600 dark:text-gray-300">
                    · {opt}
                  </li>
                ))}
              </ul>
            )}
            <button
              type="button"
              onClick={() => toggle(key)}
              className="mt-1.5 text-[10.5px] font-semibold text-violet-600 hover:underline dark:text-violet-400"
            >
              {open ? 'Hide answer' : 'Show answer'}
            </button>
            {open && (
              <div className="mt-1.5 rounded bg-gray-50 px-2 py-1.5 dark:bg-gray-900/50">
                <p className="text-[11.5px] font-medium text-gray-800 dark:text-gray-100">{q.answer}</p>
                {q.explanation && (
                  <p className="mt-1 text-[11px] leading-snug text-gray-500 dark:text-gray-400">{q.explanation}</p>
                )}
                {q.source_reference && (
                  <p className="mt-1 flex items-center gap-1 text-[10px] text-gray-400">
                    <ExternalLink className="h-2.5 w-2.5" />
                    {q.source_reference}
                  </p>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};
