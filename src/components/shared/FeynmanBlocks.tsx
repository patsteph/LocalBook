/**
 * FeynmanBlocks — Interactive components rendered from code blocks in Feynman documents.
 *
 * Instead of fragile markdown link parsing (#feynman-quiz:...), the backend emits
 * fenced code blocks with language tags `feynman-quiz` and `feynman-audio`.
 * ReactMarkdown's `code` handler detects these and renders interactive buttons.
 *
 * Quiz blocks fetch pre-generated quizzes from a background cache — no LLM call
 * on click.  Quizzes are generated in the background after document creation
 * (Phase 7) using the section narrative as input, producing focused questions.
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Target, Headphones, ChevronRight, ChevronDown, Check, X, Loader2 } from 'lucide-react';
import { API_BASE_URL } from '../../services/api';

// ── Quiz Cache Types ──────────────────────────────────────────────────────

interface CachedQuestion {
  q: string;
  a: string;
  options: string[];
  explanation: string;
}

// Module-level cache: survives React remounts caused by parent re-renders.
// Key = "notebook_id:level" → { questions, expanded }
const _quizStateCache: Record<string, { questions: CachedQuestion[] | null; expanded: boolean }> = {};
function _cacheKey(nbId?: string, level?: number): string { return `${nbId || ''}:${level || 0}`; }

// ── Quiz Block ─────────────────────────────────────────────────────────────

interface QuizBlockData {
  notebook_id?: string;
  level?: number;
  difficulty: string;
  label: string;
  // Legacy fields (backward compat)
  topic?: string;
}

export const FeynmanQuizBlock: React.FC<{ json: string; docTitle?: string }> = ({ json, docTitle }) => {
  let data: QuizBlockData;
  try {
    data = JSON.parse(json);
  } catch {
    return null;
  }

  const ck = _cacheKey(data.notebook_id, data.level);
  const cached = _quizStateCache[ck];

  const [expanded, setExpanded] = useState(cached?.expanded ?? false);
  const [questions, setQuestions] = useState<CachedQuestion[] | null>(cached?.questions ?? null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Persist expanded + questions to module cache on change
  useEffect(() => {
    _quizStateCache[ck] = { questions, expanded };
  }, [questions, expanded, ck]);

  const fetchQuiz = useCallback(async () => {
    if (!data.notebook_id || !data.level) return false;
    try {
      const res = await fetch(
        `${API_BASE_URL}/content/feynman-quiz-cache?notebook_id=${encodeURIComponent(data.notebook_id)}&level=${data.level}`
      );
      const result = await res.json();
      if (result.status === 'ready' && result.questions?.length) {
        setQuestions(result.questions);
        setLoading(false);
        return true;
      }
      if (result.status === 'generating') {
        setLoading(true);
        return false;
      }
      // not_found — quiz generation didn't run or failed
      setError('Quiz not available yet. Try regenerating the document.');
      setLoading(false);
      return true;
    } catch {
      setError('Could not fetch quiz.');
      setLoading(false);
      return true;
    }
  }, [data.notebook_id, data.level]);

  // Clean up polling on unmount
  useEffect(() => {
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  const handleClick = async () => {
    if (expanded) {
      setExpanded(false);
      return;
    }
    setExpanded(true);

    // If we already have questions, just show them
    if (questions) return;

    // Legacy fallback: no notebook_id means old-style trigger
    if (!data.notebook_id || !data.level) {
      const topicPrefix = docTitle?.replace(/^Document:\s*/i, '').replace(/^Feynman.*?:\s*/i, '') || '';
      const fullTopic = topicPrefix ? `${data.topic || ''}: ${topicPrefix}`.trim() : (data.topic || '');
      window.dispatchEvent(new CustomEvent('feynmanQuizNav', {
        detail: { topic: fullTopic, difficulty: data.difficulty }
      }));
      return;
    }

    // Fetch from cache
    setLoading(true);
    const done = await fetchQuiz();
    if (!done) {
      // Still generating — poll every 3 seconds
      pollRef.current = setInterval(async () => {
        const finished = await fetchQuiz();
        if (finished && pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
      }, 3000);
    }
  };

  return (
    <div className="not-prose my-5">
      <button
        onClick={handleClick}
        className={`
          group w-full flex items-center justify-between gap-3
          px-5 py-3.5 rounded-xl font-medium text-sm
          transition-all duration-200 cursor-pointer
          ${expanded
            ? 'bg-purple-600 text-white border-purple-600 shadow-lg shadow-purple-500/25'
            : 'bg-gradient-to-r from-purple-50 to-indigo-50 dark:from-purple-900/20 dark:to-indigo-900/20 text-purple-700 dark:text-purple-300 border border-purple-200 dark:border-purple-700 hover:from-purple-100 hover:to-indigo-100 dark:hover:from-purple-900/30 dark:hover:to-indigo-900/30 hover:border-purple-300 dark:hover:border-purple-600 hover:shadow-md'
          }
        `}
      >
        <span className="flex items-center gap-2.5">
          <span className={`
            flex items-center justify-center w-8 h-8 rounded-lg
            ${expanded
              ? 'bg-white/20'
              : 'bg-purple-100 dark:bg-purple-800/50 group-hover:bg-purple-200 dark:group-hover:bg-purple-800'
            }
          `}>
            <Target className="w-4.5 h-4.5" />
          </span>
          <span className="flex flex-col items-start">
            <span className="font-semibold">{data.label}</span>
            <span className={`text-xs ${expanded ? 'text-purple-200' : 'text-purple-500 dark:text-purple-400'}`}>
              {data.difficulty.charAt(0).toUpperCase() + data.difficulty.slice(1)} difficulty · {questions ? `${questions.length} questions` : 'Interactive quiz'}
            </span>
          </span>
        </span>
        {expanded ? <ChevronDown className="w-5 h-5" /> : <ChevronRight className={`w-5 h-5 transition-transform group-hover:translate-x-1`} />}
      </button>

      {expanded && (
        <div className="mt-3 space-y-3">
          {loading && (
            <div className="flex items-center gap-2 text-sm text-purple-600 dark:text-purple-400 py-4 justify-center">
              <Loader2 className="w-4 h-4 animate-spin" />
              <span>Preparing your quiz...</span>
            </div>
          )}
          {error && (
            <p className="text-sm text-red-500 dark:text-red-400 text-center py-2">{error}</p>
          )}
          {questions && questions.map((q, i) => (
            <InlineQuestion key={i} index={i} question={q} />
          ))}
        </div>
      )}
    </div>
  );
};

// ── Inline Question Component ───────────────────────────────────────────

// Module-level answer cache: preserves user's quiz progress across remounts
const _answerCache: Record<string, { selected: string | null; revealed: boolean }> = {};

const InlineQuestion: React.FC<{ index: number; question: CachedQuestion }> = ({ index, question }) => {
  const qKey = (question.q || '').slice(0, 80);
  const prevAnswer = _answerCache[qKey];
  const [selected, setSelected] = useState<string | null>(prevAnswer?.selected ?? null);
  const [revealed, setRevealed] = useState(prevAnswer?.revealed ?? false);
  const isCorrect = selected === question.a;

  const handleSelect = (option: string) => {
    if (revealed) return;
    setSelected(option);
    setRevealed(true);
    _answerCache[qKey] = { selected: option, revealed: true };
  };

  return (
    <div className="bg-white dark:bg-gray-800 border border-purple-100 dark:border-purple-900/50 rounded-lg p-4 shadow-sm">
      <p className="font-medium text-sm text-gray-900 dark:text-gray-100 mb-3">
        <span className="text-purple-600 dark:text-purple-400 mr-1.5">{index + 1}.</span>
        {question.q}
      </p>
      <div className="space-y-1.5">
        {(question.options || []).map((opt, oi) => {
          const isThis = selected === opt;
          const isAnswer = opt === question.a;
          let optClass = 'border-gray-200 dark:border-gray-700 hover:border-purple-300 dark:hover:border-purple-600 cursor-pointer';
          if (revealed) {
            if (isAnswer) optClass = 'border-green-400 bg-green-50 dark:bg-green-900/20 dark:border-green-600';
            else if (isThis && !isCorrect) optClass = 'border-red-400 bg-red-50 dark:bg-red-900/20 dark:border-red-600';
            else optClass = 'border-gray-200 dark:border-gray-700 opacity-60';
          }
          return (
            <button
              key={oi}
              onClick={() => handleSelect(opt)}
              disabled={revealed}
              className={`w-full text-left px-3 py-2 rounded-md border text-sm transition-colors flex items-center gap-2 ${optClass}`}
            >
              <span className="text-xs font-mono text-gray-400 w-4">{String.fromCharCode(65 + oi)}</span>
              <span className="flex-1 text-gray-800 dark:text-gray-200">{opt}</span>
              {revealed && isAnswer && <Check className="w-4 h-4 text-green-600 dark:text-green-400 flex-shrink-0" />}
              {revealed && isThis && !isCorrect && <X className="w-4 h-4 text-red-500 flex-shrink-0" />}
            </button>
          );
        })}
      </div>
      {revealed && (
        <div className={`mt-3 p-3 rounded-md text-sm ${isCorrect ? 'bg-green-50 dark:bg-green-900/20 text-green-800 dark:text-green-300' : 'bg-amber-50 dark:bg-amber-900/20 text-amber-800 dark:text-amber-300'}`}>
          <p className="font-medium mb-1">{isCorrect ? 'Correct!' : `The answer is: ${question.a}`}</p>
          <p className="text-xs opacity-80">{question.explanation}</p>
        </div>
      )}
    </div>
  );
};

// ── Audio Block ────────────────────────────────────────────────────────────

interface AudioBlockData {
  label: string;
  section?: string;
}

export const FeynmanAudioBlock: React.FC<{ json: string }> = ({ json }) => {
  const [clicked, setClicked] = useState(false);

  let data: AudioBlockData;
  try {
    data = JSON.parse(json);
  } catch {
    return null;
  }

  const handleClick = () => {
    setClicked(true);
    window.dispatchEvent(new CustomEvent('feynmanAudioNav', {
      detail: { section: data.section || 'full' }
    }));
    setTimeout(() => setClicked(false), 2000);
  };

  return (
    <div className="not-prose my-5">
      <button
        onClick={handleClick}
        className={`
          group w-full flex items-center justify-between gap-3
          px-5 py-3.5 rounded-xl font-medium text-sm
          transition-all duration-200 cursor-pointer
          ${clicked
            ? 'bg-emerald-600 text-white border-emerald-600 shadow-lg shadow-emerald-500/25'
            : 'bg-gradient-to-r from-emerald-50 to-teal-50 dark:from-emerald-900/20 dark:to-teal-900/20 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-700 hover:from-emerald-100 hover:to-teal-100 dark:hover:from-emerald-900/30 dark:hover:to-teal-900/30 hover:border-emerald-300 dark:hover:border-emerald-600 hover:shadow-md'
          }
        `}
      >
        <span className="flex items-center gap-2.5">
          <span className={`
            flex items-center justify-center w-8 h-8 rounded-lg
            ${clicked
              ? 'bg-white/20'
              : 'bg-emerald-100 dark:bg-emerald-800/50 group-hover:bg-emerald-200 dark:group-hover:bg-emerald-800'
            }
          `}>
            <Headphones className="w-4.5 h-4.5" />
          </span>
          <span className="flex flex-col items-start">
            <span className="font-semibold">{data.label}</span>
            <span className={`text-xs ${clicked ? 'text-emerald-200' : 'text-emerald-500 dark:text-emerald-400'}`}>
              Teaching podcast · Feynman-style explanation
            </span>
          </span>
        </span>
        <ChevronRight className={`w-5 h-5 transition-transform ${clicked ? '' : 'group-hover:translate-x-1'}`} />
      </button>
      {clicked && (
        <p className="text-xs text-emerald-600 dark:text-emerald-400 mt-1.5 ml-1 animate-pulse">
          ✓ Audio tab opened — generate or play the teaching podcast
        </p>
      )}
    </div>
  );
};

// ── Helper: Check if a code block className is a Feynman interactive block ──

export const isFeynmanBlock = (className?: string): boolean => {
  if (!className) return false;
  return /language-feynman-(quiz|audio)/.test(className);
};
