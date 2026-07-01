/**
 * FlashcardsCanvasTile — the interactive study experience for a flash-card deck,
 * rendered INSIDE a canvas tile (like AudioCanvasPlayer is for audio).
 *
 * Lifecycle mirrors AudioCanvasPlayer:
 *   1. ChatActionBar creates a canvas item with status='generating' and
 *      metadata: { notebookId, topic, difficulty, count }.
 *   2. This tile mounts, reads metadata, generates the deck, and drives the
 *      study session in-place.
 *   3. The parent (CanvasItemCard / CanvasWorkspaceOverlay) handles collapse,
 *      title, bookmark, etc. — we only own the body.
 *
 * Supports three answer modes (click / type / voice), per-notebook tutor voice
 * that reads feedback aloud, positive animation on correct, and a reinforcing
 * explanation + answer on wrong. At the end shows a summary with "redo misses".
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { emitEvent } from '../../lib/events';
import { sanitizeSvg } from '../../lib/sanitizeSvg';
import {
  Mic, MicOff, Volume2, CheckCircle2, XCircle, RotateCcw,
  ChevronRight, Settings2, Sparkles, Loader2, AlertCircle,
  MousePointerClick, Keyboard, BookOpen, Lightbulb, Target,
} from 'lucide-react';

import { flashcardsService, TutorProfile, Difficulty, AnswerMode } from '../../services/flashcards';
import { Quiz, QuizQuestion, OPEN_ENDED_TYPES, quizService, GapAnalysisResponse, KnowledgeGap } from '../../services/quiz';

// ─── Props ──────────────────────────────────────────────────────────────────

export interface FlashcardsCanvasTileProps {
  itemId: string;
  notebookId: string;
  topic: string;
  difficulty: Difficulty;
  count: number;
  parentStatus?: string;
  parentError?: string;
  chatContext?: string;
  /** Initial tutor config passed from setup panel */
  tutorGender?: 'female' | 'male';
  tutorAccent?: 'us' | 'uk';
  tutorAutoplay?: boolean;
  /** Force-include visual_diagram question type (visual/SVG flashcards). */
  includeVisuals?: boolean;
  onProgress?: (summary: { current: number; total: number; correct: number }) => void;
  onComplete?: (summary: { total: number; correct: number }) => void;
  onStatusChange?: (status: 'generating' | 'complete' | 'error', errorMessage?: string) => void;
}

// ─── Internal types ─────────────────────────────────────────────────────────

interface CardResult {
  questionId: string;
  correct: boolean;
  userAnswer: string;
  feedback: string;
}

const PERSONA_SUGGESTIONS = ['Nora', 'Miles', 'Ada', 'Finn', 'Sage', 'Rowan'];

// ─── Component ──────────────────────────────────────────────────────────────

export const FlashcardsCanvasTile: React.FC<FlashcardsCanvasTileProps> = ({
  notebookId,
  topic,
  difficulty,
  count,
  parentStatus,
  parentError,
  chatContext,
  tutorGender,
  tutorAccent,
  tutorAutoplay,
  includeVisuals,
  onProgress,
  onComplete,
  onStatusChange,
}) => {
  // ── Deck state ─────────────────────────────────────────────────────────
  const [deck, setDeck] = useState<Quiz | null>(null);
  const [genError, setGenError] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);

  const [cardIndex, setCardIndex] = useState(0);
  const [results, setResults] = useState<CardResult[]>([]);
  const [complete, setComplete] = useState(false);

  // ── Gap analysis (requested on completion, shown in summary) ────────────
  const [gapAnalysis, setGapAnalysis] = useState<GapAnalysisResponse | null>(null);
  const [analyzingGaps, setAnalyzingGaps] = useState(false);

  // ── Per-card study state ───────────────────────────────────────────────
  const [answerMode, setAnswerMode] = useState<AnswerMode>('type');
  const [userAnswer, setUserAnswer] = useState('');
  const [selectedChoice, setSelectedChoice] = useState<string | null>(null);
  const [grading, setGrading] = useState(false);
  const [revealed, setRevealed] = useState<{ correct: boolean; feedback: string } | null>(null);
  const [showCelebration, setShowCelebration] = useState(false);
  const [expandedExplanation, setExpandedExplanation] = useState(false);

  // ── Tutor voice (initialized from props, persists per notebook) ───────
  const [tutor, setTutor] = useState<TutorProfile>(() => ({
    gender: tutorGender ?? 'female',
    accent: tutorAccent ?? 'us',
    persona: '',
    voice_id: `${tutorGender ?? 'female'}_${tutorAccent ?? 'us'}`,
    speed: 1.0,
    autoplay: tutorAutoplay ?? true,
  }));
  const [showTutorPanel, setShowTutorPanel] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  // Race-free TTS plumbing. ttsTokenRef increments on every speak() — any
  // in-flight speak whose token no longer matches at resolve time is
  // discarded (the user has advanced). ttsAbortRef cancels the underlying
  // fetch so a slow TTS server can't keep queuing stale audio.
  const ttsTokenRef = useRef(0);
  const ttsAbortRef = useRef<AbortController | null>(null);

  // ── Voice-answer (Whisper) ─────────────────────────────────────────────
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);

  // ── Generate deck on mount (auto) ──────────────────────────────────────
  const generateDeck = useCallback(async () => {
    if (generating) return;
    setGenerating(true);
    setGenError(null);
    onStatusChange?.('generating');
    try {
      const quiz = await flashcardsService.generateDeck({
        notebookId,
        count,
        difficulty,
        topic: topic || undefined,
        chatContext: chatContext || undefined,
        includeVisuals: includeVisuals || undefined,
      });
      if (!quiz.questions || quiz.questions.length === 0) {
        throw new Error('No cards were generated. Try a different topic or add more sources first.');
      }
      setDeck(quiz);
      setResults([]);
      setCardIndex(0);
      setComplete(false);
      onStatusChange?.('complete');
    } catch (err: any) {
      const msg = err?.message || 'Failed to generate flash cards';
      setGenError(msg);
      onStatusChange?.('error', msg);
    } finally {
      setGenerating(false);
    }
  }, [notebookId, count, difficulty, topic, chatContext, includeVisuals, generating, onStatusChange]);

  // Auto-start on mount (once) — and whenever the input key props change
  const genKey = `${notebookId}|${topic}|${difficulty}|${count}|${includeVisuals ? 'v' : ''}`;
  const startedKeyRef = useRef<string | null>(null);
  useEffect(() => {
    if (parentStatus === 'error') return;
    if (startedKeyRef.current === genKey) return;
    startedKeyRef.current = genKey;
    generateDeck();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [genKey, parentStatus]);

  // ── Reset per-card state when card changes ─────────────────────────────
  const currentCard: QuizQuestion | null = useMemo(
    () => deck?.questions[cardIndex] ?? null,
    [deck, cardIndex],
  );
  const hasOptions =
    (currentCard?.question_type === 'multiple_choice' ||
     currentCard?.question_type === 'true_false' ||
     currentCard?.question_type === 'visual_diagram') &&
    currentCard.options && currentCard.options.length >= 2;

  useEffect(() => {
    setUserAnswer('');
    setSelectedChoice(null);
    setRevealed(null);
    setShowCelebration(false);
    setExpandedExplanation(false);
    if (hasOptions) setAnswerMode('click');
    else setAnswerMode('type');
  }, [cardIndex, deck, hasOptions]);

  // ── Cleanup on unmount ─────────────────────────────────────────────────
  useEffect(() => {
    return () => {
      if (audioRef.current) {
        audioRef.current.pause();
        const src = audioRef.current.src;
        if (src.startsWith('blob:')) URL.revokeObjectURL(src);
      }
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        mediaRecorderRef.current.stream.getTracks().forEach(t => t.stop());
      }
    };
  }, []);

  // ── Tutor TTS ──────────────────────────────────────────────────────────
  // Race-safe: every call gets a token. Any in-flight fetch is aborted,
  // any currently-playing audio is paused. When the new request resolves,
  // we only commit (set audioRef + play) if our token still matches —
  // otherwise the user has moved on and we discard the result silently.
  const speakLine = useCallback(async (text: string) => {
    if (!text || !tutor) return;
    // Stop whatever was playing.
    if (audioRef.current) {
      audioRef.current.pause();
      const prev = audioRef.current.src;
      if (prev.startsWith('blob:')) URL.revokeObjectURL(prev);
      audioRef.current = null;
    }
    // Cancel any in-flight fetch from a previous speak().
    if (ttsAbortRef.current) {
      ttsAbortRef.current.abort();
    }
    const myToken = ++ttsTokenRef.current;
    const controller = new AbortController();
    ttsAbortRef.current = controller;
    try {
      const url = await flashcardsService.speak({
        notebookId, text, speed: tutor.speed,
        signal: controller.signal,
      });
      // STALE CHECK — if the user advanced (or invoked speak again)
      // while we were waiting on the server, drop this result. Without
      // this guard the late-resolving audio queues up and plays after
      // the user has moved cards, producing the "1 minute behind" bug.
      if (myToken !== ttsTokenRef.current) {
        URL.revokeObjectURL(url);
        return;
      }
      const audio = new Audio(url);
      audioRef.current = audio;
      audio.onended = () => { URL.revokeObjectURL(url); };
      await audio.play();
    } catch (err: any) {
      // AbortError is expected when speakLine() is called rapidly —
      // not a real failure, just the previous request being cancelled.
      if (err?.name === 'AbortError') return;
      console.warn('Tutor speak failed:', err);
    }
  }, [notebookId, tutor]);

  // ── Voice recording → Whisper → fill answer ────────────────────────────
  const startRecording = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const chosen =
        MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus'
          : MediaRecorder.isTypeSupported('audio/webm') ? 'audio/webm'
            : MediaRecorder.isTypeSupported('audio/mp4') ? 'audio/mp4'
              : '';
      const mr = new MediaRecorder(stream, chosen ? { mimeType: chosen } : undefined);
      audioChunksRef.current = [];
      mr.ondataavailable = (e) => { if (e.data.size > 0) audioChunksRef.current.push(e.data); };
      mr.onstop = async () => {
        stream.getTracks().forEach(t => t.stop());
        setRecording(false);
        if (audioChunksRef.current.length === 0) return;
        setTranscribing(true);
        try {
          const mime = mr.mimeType || 'audio/webm';
          const blob = new Blob(audioChunksRef.current, { type: mime });
          const ext = mime.includes('mp4') ? 'm4a' : 'webm';
          const text = await flashcardsService.transcribeAnswer(blob, `answer.${ext}`);
          setUserAnswer(prev => (prev ? `${prev} ${text}`.trim() : text));
        } catch (err: any) {
          setGenError(err.message || 'Transcription failed');
        } finally {
          setTranscribing(false);
        }
      };
      mr.start();
      mediaRecorderRef.current = mr;
      setRecording(true);
    } catch (err: any) {
      setGenError(err.name === 'NotAllowedError' ? 'Microphone permission denied' : (err.message || 'Could not start recording'));
    }
  }, []);

  const stopRecording = useCallback(() => {
    const mr = mediaRecorderRef.current;
    if (mr && mr.state !== 'inactive') mr.stop();
  }, []);

  // ── Current card + submission ──────────────────────────────────────────
  const submitAnswer = useCallback(async () => {
    if (!currentCard || grading || revealed) return;

    let answer = '';
    if (answerMode === 'click' && selectedChoice) answer = selectedChoice;
    else answer = userAnswer.trim();

    if (!answer) {
      setGenError('Enter or select an answer first');
      return;
    }
    setGenError(null);
    setGrading(true);

    try {
      let correct = false;
      let feedback = '';
      const isOpenEnded = OPEN_ENDED_TYPES.has(currentCard.question_type);
      if (!isOpenEnded) {
        const normA = answer.toLowerCase().trim();
        const normC = currentCard.answer.toLowerCase().trim();
        // For very short expected answers (≤ 3 words) require exact or substring match.
        // For longer answers allow fuzzy containment.
        const expectedWords = normC.split(/\s+/).length;
        if (expectedWords <= 3) {
          correct = normA === normC || normC.includes(normA) || normA.includes(normC);
        } else {
          // Allow more lenient matching for longer answers: shared words ratio
          const aWords = new Set(normA.split(/\s+/).filter(w => w.length > 2));
          const cWords = new Set(normC.split(/\s+/).filter(w => w.length > 2));
          const shared = [...aWords].filter(w => cWords.has(w)).length;
          const minLen = Math.min(aWords.size, cWords.size);
          correct = minLen > 0 && shared / minLen >= 0.5;
        }
        feedback = correct
          ? (currentCard.explanation || 'Correct!')
          : `The correct answer is **${currentCard.answer}**. ${currentCard.explanation || ''}`.trim();
      } else {
        const res = await flashcardsService.gradeCard({
          question: currentCard.question,
          correctAnswer: currentCard.answer,
          userAnswer: answer,
          questionType: currentCard.question_type,
        });
        correct = res.correct;
        feedback = res.feedback || (correct ? 'Nice work!' : `Expected: ${currentCard.answer}`);
      }

      setRevealed({ correct, feedback });
      const nextResults = [...results, {
        questionId: currentCard.id,
        correct,
        userAnswer: answer,
        feedback,
      }];
      setResults(nextResults);

      if (correct) {
        setShowCelebration(true);
        setTimeout(() => setShowCelebration(false), 900);
        if (tutor?.autoplay && currentCard.explanation) {
          const spoken = stripMarkdownForSpeech(currentCard.explanation);
          speakLine(spoken);
        }
      } else if (tutor?.autoplay) {
        const spoken = stripMarkdownForSpeech(
          `${feedback} The correct answer is ${currentCard.answer}. ${currentCard.explanation || ''}`
        );
        speakLine(spoken);
      }

      if (deck) {
        const correctCount = nextResults.filter(r => r.correct).length;
        onProgress?.({
          current: cardIndex + 1,
          total: deck.questions.length,
          correct: correctCount,
        });
      }
    } catch (err: any) {
      setGenError(err.message || 'Grading failed');
    } finally {
      setGrading(false);
    }
  }, [currentCard, grading, revealed, answerMode, selectedChoice, userAnswer, tutor, speakLine, results, cardIndex, deck, onProgress]);

  const nextCard = useCallback(() => {
    if (!deck) return;
    // Invalidate any in-flight speak from the previous card so a slow
    // TTS server can't play the prior card's audio over the new card.
    ttsTokenRef.current++;
    if (ttsAbortRef.current) ttsAbortRef.current.abort();
    if (audioRef.current) audioRef.current.pause();
    if (cardIndex + 1 >= deck.questions.length) {
      setComplete(true);
      const correctCount = results.filter(r => r.correct).length;
      onComplete?.({ total: deck.questions.length, correct: correctCount });
    } else {
      setCardIndex(i => i + 1);
    }
  }, [deck, cardIndex, results, onComplete]);

  // ── Gap analysis — fires once when the deck completes with misses ──────
  useEffect(() => {
    if (!complete || !deck) return;
    if (gapAnalysis || analyzingGaps) return;
    const missed = results.filter(r => !r.correct);
    if (missed.length === 0) return;
    const missedQuestions = missed.map(m => {
      const q = deck.questions.find(qq => qq.id === m.questionId);
      return {
        question: q?.question || '',
        correct_answer: q?.answer || '',
        user_answer: m.userAnswer,
        explanation: q?.explanation || '',
      };
    });
    setAnalyzingGaps(true);
    quizService
      .analyzeGaps(notebookId, missedQuestions, topic || deck.topic)
      .then(setGapAnalysis)
      .catch(err => console.warn('Gap analysis failed:', err))
      .finally(() => setAnalyzingGaps(false));
  }, [complete, deck, results, gapAnalysis, analyzingGaps, notebookId, topic]);

  // ── Deck actions ───────────────────────────────────────────────────────
  const redoMissed = useCallback(() => {
    if (!deck) return;
    const missedIds = new Set(results.filter(r => !r.correct).map(r => r.questionId));
    const missedCards = deck.questions.filter(q => missedIds.has(q.id));
    if (missedCards.length === 0) return;
    setDeck({ ...deck, questions: missedCards });
    setResults([]);
    setCardIndex(0);
    setComplete(false);
    setGapAnalysis(null);
  }, [deck, results]);

  const regenerate = useCallback(() => {
    startedKeyRef.current = null; // allow re-gen
    setDeck(null);
    setResults([]);
    setCardIndex(0);
    setComplete(false);
    setGapAnalysis(null);
    generateDeck();
  }, [generateDeck]);

  // ── Cross-tile helpers ─────────────────────────────────────────────────
  /** Dispatch a request to open the Source Notes viewer for a named source.
   *  The app shell (ChatInterface) listens and performs the name→id lookup. */
  const openSource = useCallback((sourceName: string, searchTerm?: string) => {
    emitEvent('openSourceByName', { notebookId, sourceName, searchTerm: searchTerm || '' });
  }, [notebookId]);

  /** Dispatch a request to spin up a new flash-cards canvas tile focused on a
   *  study-gap topic. ChatActionBar listens and calls addCanvasItem. */
  const createGapDeck = useCallback((gap: KnowledgeGap) => {
    emitEvent('createFlashcardsDeck', {
      notebookId,
      topic: gap.suggested_topic || gap.gap_title,
      difficulty,
      count: Math.min(count, 10),
      reason: `Study gap: ${gap.gap_title}`,
    });
  }, [notebookId, difficulty, count]);

  // ── Tutor patch ────────────────────────────────────────────────────────
  const patchTutor = useCallback(async (patch: Partial<TutorProfile>) => {
    try {
      const next = await flashcardsService.updateTutor(notebookId, patch);
      setTutor(next);
    } catch (err: any) {
      console.warn('Tutor update failed:', err);
    }
  }, [notebookId]);

  // ═════════════════════════════════════════════════════════════════════
  // Render
  // ═════════════════════════════════════════════════════════════════════

  // Upstream error passed in from the parent (generation failed before
  // the deck was created, e.g. network down)
  if (parentStatus === 'error' && parentError) {
    return (
      <div className="flex items-center gap-3 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg">
        <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-red-700 dark:text-red-300">Flash Cards failed</p>
          <p className="text-xs text-red-600 dark:text-red-400">{parentError}</p>
        </div>
        <button onClick={regenerate} className="px-2 py-1 text-xs rounded-md bg-red-100 dark:bg-red-900/40 text-red-700 dark:text-red-300 hover:bg-red-200 dark:hover:bg-red-900/60">
          Retry
        </button>
      </div>
    );
  }

  // Generating (no deck yet)
  if (generating || (!deck && !genError)) {
    return (
      <div className="flex items-center gap-3 p-3 bg-purple-50 dark:bg-purple-900/20 border border-purple-200 dark:border-purple-800 rounded-lg">
        <div className="flex-shrink-0 w-9 h-9 rounded-full bg-purple-100 dark:bg-purple-900/40 text-purple-600 dark:text-purple-300 flex items-center justify-center">
          <Sparkles className="w-4 h-4 animate-pulse" />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-gray-900 dark:text-white">
            Building {count} {difficulty} flash cards…
          </p>
          <p className="text-xs text-gray-500 dark:text-gray-400 truncate">
            {topic || 'From notebook content'}
          </p>
        </div>
        <Loader2 className="w-4 h-4 text-purple-500 animate-spin flex-shrink-0" />
      </div>
    );
  }

  // Local generation error (show retry)
  if (genError && !deck) {
    return (
      <div className="flex items-center gap-3 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg">
        <AlertCircle className="w-5 h-5 text-red-500 flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-red-700 dark:text-red-300">Couldn't build cards</p>
          <p className="text-xs text-red-600 dark:text-red-400 break-words">{genError}</p>
        </div>
        <button onClick={regenerate} className="px-2 py-1 text-xs rounded-md bg-red-100 dark:bg-red-900/40 text-red-700 dark:text-red-300 hover:bg-red-200 dark:hover:bg-red-900/60 flex-shrink-0">
          Retry
        </button>
      </div>
    );
  }

  if (!deck) return null;

  const correctCount = results.filter(r => r.correct).length;

  // ─── Completion summary ────────────────────────────────────────────────
  if (complete) {
    const total = deck.questions.length;
    const pct = Math.round((correctCount / total) * 100);
    const missed = results.filter(r => !r.correct);

    return (
      <div className="space-y-3">
        {tutor && <TutorBar tutor={tutor} onPatch={patchTutor} open={showTutorPanel} onToggle={() => setShowTutorPanel(s => !s)} />}

        <div className="rounded-xl border-2 border-purple-300 dark:border-purple-700 bg-gradient-to-br from-purple-50 to-indigo-50 dark:from-purple-900/20 dark:to-indigo-900/20 p-4 text-center">
          <div className="text-3xl font-bold text-purple-700 dark:text-purple-300">{pct}%</div>
          <div className="text-xs text-gray-700 dark:text-gray-300 mt-0.5">
            {correctCount} of {total} correct
          </div>
          <div className="mt-2 text-[11px] text-gray-600 dark:text-gray-400">
            {pct >= 90 ? 'Mastered — excellent recall!' :
              pct >= 70 ? 'Solid — a second pass will lock it in.' :
                'Keep going — review the misses below.'}
          </div>
        </div>

        {/* Gap analysis — "What to study next" */}
        {missed.length > 0 && (
          <div className="rounded-lg border border-purple-200 dark:border-purple-800 bg-purple-50/50 dark:bg-purple-900/10 p-3 space-y-2">
            <div className="flex items-center gap-1.5 text-xs font-semibold text-purple-800 dark:text-purple-300">
              <Lightbulb className="w-3.5 h-3.5" />
              What to study next
            </div>
            {analyzingGaps && (
              <div className="flex items-center gap-2 text-[11px] text-purple-700 dark:text-purple-300">
                <Loader2 className="w-3 h-3 animate-spin" />
                Analyzing where you need to study more…
              </div>
            )}
            {gapAnalysis && (
              <>
                {gapAnalysis.summary && (
                  <p className="text-[11px] text-gray-700 dark:text-gray-300">{gapAnalysis.summary}</p>
                )}
                <div className="space-y-1.5">
                  {gapAnalysis.gaps.map((gap, i) => (
                    <div
                      key={i}
                      className="p-2 rounded-md bg-white dark:bg-gray-800 border border-purple-200 dark:border-purple-800 space-y-1"
                    >
                      <div className="text-xs font-semibold text-purple-900 dark:text-purple-200">{gap.gap_title}</div>
                      {gap.description && (
                        <p className="text-[11px] text-gray-600 dark:text-gray-400">{gap.description}</p>
                      )}
                      {gap.study_suggestion && (
                        <p className="text-[11px] text-gray-500 dark:text-gray-400 italic">Try: {gap.study_suggestion}</p>
                      )}
                      <button
                        onClick={() => createGapDeck(gap)}
                        className="mt-1 w-full flex items-center justify-center gap-1 px-2 py-1 text-[11px] font-medium rounded-md bg-purple-600 text-white hover:bg-purple-700 transition-colors"
                      >
                        <Target className="w-3 h-3" />
                        Quiz me on: {gap.suggested_topic || gap.gap_title}
                      </button>
                    </div>
                  ))}
                </div>
              </>
            )}
            {!analyzingGaps && !gapAnalysis && (
              <p className="text-[11px] text-gray-500 dark:text-gray-400 italic">
                Ask the chat: <span className="font-mono">@chat help me understand {topic || 'these concepts'} better</span>
              </p>
            )}
          </div>
        )}

        {missed.length > 0 && (
          <details className="rounded-lg border border-gray-200 dark:border-gray-700">
            <summary className="cursor-pointer px-3 py-2 text-xs font-semibold text-gray-700 dark:text-gray-300">
              Review misses ({missed.length})
            </summary>
            <div className="p-2 space-y-2">
              {missed.map((r, i) => {
                const q = deck.questions.find(x => x.id === r.questionId);
                if (!q) return null;
                return (
                  <div key={i} className="text-xs bg-white dark:bg-gray-800 rounded-md p-2 border border-gray-100 dark:border-gray-700">
                    <div className="font-medium text-gray-900 dark:text-white">{q.question}</div>
                    <div className="mt-1 text-red-600 dark:text-red-400">You: {r.userAnswer}</div>
                    <div className="mt-0.5 text-green-700 dark:text-green-400">Correct: {q.answer}</div>
                    {q.explanation && <div className="mt-0.5 text-gray-600 dark:text-gray-400">{q.explanation}</div>}
                    {q.source_reference && (
                      <button
                        type="button"
                        onClick={() => openSource(q.source_reference!, q.answer.substring(0, 60))}
                        className="mt-1.5 inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] rounded-full border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 hover:border-purple-400 transition-colors"
                      >
                        <BookOpen className="w-2.5 h-2.5" />
                        Source: {q.source_reference}
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          </details>
        )}

        <div className="flex gap-2">
          {missed.length > 0 && (
            <button
              onClick={redoMissed}
              className="flex-1 px-3 py-1.5 text-xs rounded-lg border border-purple-300 dark:border-purple-700 text-purple-700 dark:text-purple-300 hover:bg-purple-50 dark:hover:bg-purple-900/30 font-medium flex items-center justify-center gap-1"
            >
              <RotateCcw className="w-3 h-3" /> Redo misses
            </button>
          )}
          <button
            onClick={regenerate}
            className="flex-1 px-3 py-1.5 text-xs rounded-lg bg-purple-600 hover:bg-purple-700 text-white font-medium"
          >
            New deck
          </button>
        </div>
      </div>
    );
  }

  // ─── Active study card ─────────────────────────────────────────────────
  if (!currentCard) return null;

  return (
    <div className="space-y-2.5">
      {genError && (
        <div className="flex items-center gap-2 px-2 py-1.5 text-xs rounded-md bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300 border border-red-200 dark:border-red-800">
          <AlertCircle className="w-3.5 h-3.5 flex-shrink-0" />
          <span className="flex-1">{genError}</span>
          <button onClick={() => setGenError(null)} className="text-red-500 hover:text-red-700">×</button>
        </div>
      )}

      {/* Progress with card count indicator */}
      <div className="space-y-1">
        <div className="flex justify-between items-center text-[11px] text-gray-600 dark:text-gray-400 font-medium">
          <div className="flex items-center gap-1.5">
            <div className="flex gap-0.5">
              {deck.questions.map((_, i) => (
                <div
                  key={i}
                  className={`h-1.5 rounded-full transition-all ${
                    i === cardIndex
                      ? 'w-4 bg-purple-500'
                      : i < cardIndex
                        ? 'w-1.5 bg-purple-300 dark:bg-purple-700'
                        : 'w-1.5 bg-gray-300 dark:bg-gray-600'
                  }`}
                />
              ))}
            </div>
            <span className="ml-1">Card {cardIndex + 1}/{deck.questions.length}</span>
          </div>
          <span className="flex items-center gap-1">
            <CheckCircle2 className="w-3 h-3 text-green-500" />
            {correctCount}
          </span>
        </div>
      </div>

      {/* Card stack container with depth effect */}
      <div className="relative pt-2 pb-1">
        {/* Background card shadows (stack effect) */}
        {cardIndex < deck.questions.length - 1 && (
          <>
            <div
              className="absolute inset-x-4 top-0 h-full rounded-2xl bg-gray-200 dark:bg-gray-700 opacity-40"
              style={{ transform: 'translateY(8px) scale(0.96)' }}
            />
            <div
              className="absolute inset-x-2 top-0 h-full rounded-2xl bg-gray-300 dark:bg-gray-600 opacity-60"
              style={{ transform: 'translateY(4px) scale(0.98)' }}
            />
          </>
        )}

        {/* Main flashcard - designed like a physical index card */}
        <div
          key={`card-${cardIndex}`}
          className={`relative rounded-2xl border-2 overflow-hidden transition-all duration-500 shadow-lg animate-in fade-in slide-in-from-right-4 ${
            revealed?.correct === true
              ? 'border-green-500 bg-gradient-to-br from-green-50 to-emerald-50 dark:from-green-900/20 dark:to-emerald-900/20 shadow-green-200 dark:shadow-green-900/30'
              : revealed?.correct === false
                ? 'border-red-500 bg-gradient-to-br from-red-50 to-rose-50 dark:from-red-900/20 dark:to-rose-900/20 shadow-red-200 dark:shadow-red-900/30'
                : 'border-purple-200 dark:border-purple-800/50 bg-gradient-to-br from-white via-purple-50/30 to-indigo-50/50 dark:from-gray-800 dark:via-purple-900/10 dark:to-indigo-900/20'
          }`}
        >
          {/* Card header band — like a real index card ruled line */}
          <div className={`h-1 w-full ${
            revealed?.correct === true
              ? 'bg-gradient-to-r from-green-400 to-emerald-500'
              : revealed?.correct === false
                ? 'bg-gradient-to-r from-red-400 to-rose-500'
                : 'bg-gradient-to-r from-purple-400 via-indigo-500 to-purple-400'
          }`} />

          {/* Card corner fold effect (decorative) */}
          <div className="absolute top-0 right-0 w-10 h-10 pointer-events-none">
            <div className="absolute top-0 right-0 w-10 h-10 bg-gradient-to-bl from-purple-200/60 to-transparent dark:from-purple-800/30" style={{ clipPath: 'polygon(100% 0, 100% 100%, 0 0)' }} />
          </div>

          <div className="p-5 min-h-[140px]">
            {/* Card metadata row */}
            <div className="flex items-center justify-between mb-3">
              <div className="inline-flex items-center gap-1.5">
                <div className="w-5 h-5 rounded-md bg-purple-100 dark:bg-purple-900/40 flex items-center justify-center">
                  <span className="text-[9px] font-bold text-purple-700 dark:text-purple-300">{cardIndex + 1}</span>
                </div>
                <div className="text-[10px] uppercase tracking-widest font-bold text-purple-700 dark:text-purple-300">
                  {currentCard.difficulty} · {hasOptions ? 'Choose One' : currentCard.question_type.replace(/_/g, ' ')}
                </div>
              </div>
              {currentCard.source_reference && (
                <button
                  type="button"
                  onClick={() => openSource(
                    currentCard.source_reference!,
                    currentCard.question.substring(0, 60),
                  )}
                  className="inline-flex items-center gap-1 px-1.5 py-0.5 text-[10px] rounded-full border border-purple-200 dark:border-purple-800 text-purple-700 dark:text-purple-300 hover:bg-purple-50 dark:hover:bg-purple-900/40 transition-colors bg-white/60 dark:bg-gray-800/60"
                  title="Open source content"
                >
                  <BookOpen className="w-2.5 h-2.5" />
                  <span className="truncate max-w-[140px]">{currentCard.source_reference}</span>
                </button>
              )}
            </div>

            {/* Question - prominent like a real flashcard */}
            <div className="text-base font-semibold text-gray-900 dark:text-gray-100 leading-relaxed tracking-tight">
              {currentCard.question}
            </div>

            {/* Visual diagram for visual_diagram questions */}
            {currentCard.visual_svg && (
              <div className="mt-3 p-3 bg-white/80 dark:bg-gray-900/60 rounded-lg border border-gray-200 dark:border-gray-700">
                <div
                  className="w-full overflow-hidden"
                  dangerouslySetInnerHTML={{ __html: sanitizeSvg(currentCard.visual_svg) }}
                  style={{ maxHeight: '200px' }}
                />
              </div>
            )}
          </div>

        {!revealed ? (
          <div className="px-4 pb-4 space-y-2.5 border-t border-gray-200 dark:border-gray-700 pt-3">
            {/* Mode switcher */}
            <div className="flex gap-1 text-[11px]">
              {hasOptions && (
                <ModeChip active={answerMode === 'click'} onClick={() => setAnswerMode('click')} icon={<MousePointerClick className="w-3 h-3" />}>Click</ModeChip>
              )}
              <ModeChip active={answerMode === 'type'} onClick={() => setAnswerMode('type')} icon={<Keyboard className="w-3 h-3" />}>Type</ModeChip>
              <ModeChip active={answerMode === 'voice'} onClick={() => setAnswerMode('voice')} icon={<Mic className="w-3 h-3" />}>Voice</ModeChip>
            </div>

            {/* Click */}
            {answerMode === 'click' && hasOptions && (
              <div className="space-y-1.5">
                {currentCard.options!.map((opt, i) => (
                  <button
                    key={i}
                    onClick={() => setSelectedChoice(opt)}
                    className={`w-full text-left px-3 py-2 rounded-lg border text-sm transition-colors ${
                      selectedChoice === opt
                        ? 'border-purple-500 bg-purple-50 dark:bg-purple-900/30 text-purple-900 dark:text-purple-100'
                        : 'border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300'
                    }`}
                  >
                    <span className="font-mono text-[11px] text-gray-500 mr-2">{String.fromCharCode(65 + i)}.</span>
                    {opt}
                  </button>
                ))}
              </div>
            )}

            {/* Type */}
            {answerMode === 'type' && (
              <textarea
                value={userAnswer}
                onChange={(e) => setUserAnswer(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) submitAnswer();
                }}
                placeholder="Type your answer… (⌘+Enter to submit)"
                rows={2}
                className="w-full px-2.5 py-1.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-purple-500 bg-white dark:bg-gray-700 text-gray-900 dark:text-white resize-none"
              />
            )}

            {/* Voice */}
            {answerMode === 'voice' && (
              <div className="flex flex-col items-center gap-2 py-1">
                <button
                  onMouseDown={!recording ? startRecording : undefined}
                  onMouseUp={recording ? stopRecording : undefined}
                  onMouseLeave={recording ? stopRecording : undefined}
                  onTouchStart={!recording ? startRecording : undefined}
                  onTouchEnd={recording ? stopRecording : undefined}
                  disabled={transcribing}
                  className={`w-12 h-12 rounded-full flex items-center justify-center transition-all shadow ${
                    recording ? 'bg-red-500 animate-pulse scale-110'
                      : transcribing ? 'bg-gray-400'
                        : 'bg-purple-600 hover:bg-purple-700'
                  } text-white disabled:cursor-not-allowed`}
                  title={recording ? 'Release to stop' : transcribing ? 'Transcribing…' : 'Hold to speak'}
                >
                  {recording ? <MicOff className="w-5 h-5" /> : <Mic className="w-5 h-5" />}
                </button>
                <p className="text-[11px] text-gray-600 dark:text-gray-400 text-center">
                  {recording ? 'Listening… release to finish' : transcribing ? 'Transcribing…' : 'Hold the mic and speak'}
                </p>
                {userAnswer && !recording && !transcribing && (
                  <div className="w-full px-2 py-1 text-xs bg-gray-50 dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded italic text-gray-800 dark:text-gray-200">
                    "{userAnswer}"
                  </div>
                )}
              </div>
            )}

            <button
              onClick={submitAnswer}
              disabled={grading || transcribing || recording}
              className="w-full px-3 py-1.5 text-sm rounded-lg bg-purple-600 hover:bg-purple-700 disabled:opacity-50 text-white font-medium transition-colors"
            >
              {grading ? 'Grading…' : 'Submit Answer'}
            </button>
          </div>
        ) : (
          // Revealed feedback
          <div className="px-4 pb-4 space-y-2.5 border-t-2 pt-3 border-current/20">
            <div className="flex items-start gap-2.5">
              {revealed.correct ? (
                <CheckCircle2 className="w-5 h-5 text-green-600 flex-shrink-0 mt-0.5" />
              ) : (
                <XCircle className="w-5 h-5 text-red-600 flex-shrink-0 mt-0.5" />
              )}
              <div className="flex-1 min-w-0">
                <div className={`font-semibold text-sm ${revealed.correct ? 'text-green-700 dark:text-green-300' : 'text-red-700 dark:text-red-300'}`}>
                  {revealed.correct ? 'Correct!' : 'Not quite.'}
                </div>
                {!revealed.correct && (
                  <div className="mt-1.5 space-y-1.5">
                    <div className="text-xs text-gray-800 dark:text-gray-200">
                      <span className="font-semibold">Answer:</span> {currentCard.answer}
                    </div>
                    {currentCard.explanation && (
                      <div className="text-xs text-gray-700 dark:text-gray-300 bg-white/70 dark:bg-gray-900/40 rounded-md p-1.5 border border-gray-200 dark:border-gray-700">
                        <span className="font-semibold">Why:</span>{' '}
                        {expandedExplanation || currentCard.explanation.length <= 180
                          ? currentCard.explanation
                          : (
                            <>
                              {currentCard.explanation.slice(0, 180).replace(/\s+\S*$/, '')}…
                              <button
                                onClick={() => setExpandedExplanation(true)}
                                className="ml-1 text-purple-600 dark:text-purple-400 hover:underline font-medium"
                              >
                                Show more
                              </button>
                            </>
                          )}
                      </div>
                    )}
                    {revealed.feedback && revealed.feedback !== currentCard.explanation && (
                      <div className="text-xs text-gray-700 dark:text-gray-300 italic">{revealed.feedback}</div>
                    )}
                  </div>
                )}
                {revealed.correct && currentCard.explanation && (
                  <div className="mt-0.5 text-[11px] text-gray-600 dark:text-gray-400">
                    {expandedExplanation || currentCard.explanation.length <= 180
                      ? currentCard.explanation
                      : (
                        <>
                          {currentCard.explanation.slice(0, 180).replace(/\s+\S*$/, '')}…
                          <button
                            onClick={() => setExpandedExplanation(true)}
                            className="ml-1 text-purple-600 dark:text-purple-400 hover:underline font-medium"
                          >
                            Show more
                          </button>
                        </>
                      )}
                  </div>
                )}
                {currentCard.source_reference && (
                  <button
                    type="button"
                    onClick={() => openSource(
                      currentCard.source_reference!,
                      currentCard.answer.substring(0, 60),
                    )}
                    className="mt-2 inline-flex items-center gap-1 px-2 py-0.5 text-[11px] rounded-full border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700 hover:border-purple-400 transition-colors"
                    title="Open source content"
                  >
                    <BookOpen className="w-3 h-3" />
                    <span className="truncate max-w-[220px]">Source: {currentCard.source_reference}</span>
                  </button>
                )}
              </div>
            </div>

            <div className="flex gap-2">
              {tutor && (
                <button
                  onClick={() => speakLine(stripMarkdownForSpeech(
                    revealed.correct
                      ? (currentCard.explanation || 'Nice work!')
                      : `The correct answer is ${currentCard.answer}. ${currentCard.explanation || revealed.feedback}`
                  ))}
                  className="flex items-center gap-1 px-2 py-1 text-[11px] rounded-md border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700"
                  title="Read aloud in the tutor voice"
                >
                  <Volume2 className="w-3 h-3" /> Read aloud
                </button>
              )}
              <button
                onClick={nextCard}
                className="flex-1 flex items-center justify-center gap-1 px-3 py-1.5 text-sm rounded-lg bg-purple-600 hover:bg-purple-700 text-white font-medium"
              >
                {cardIndex + 1 >= deck.questions.length ? 'Finish deck' : 'Next card'}
                <ChevronRight className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        )}
        </div>
      </div>

      {showCelebration && (
        <div className="pointer-events-none fixed inset-0 flex items-center justify-center z-50">
          <div className="relative">
            <div className="text-5xl animate-ping">✨</div>
            <div className="absolute inset-0 text-5xl animate-bounce">🎉</div>
          </div>
        </div>
      )}
    </div>
  );
};

// ─── Sub-components ────────────────────────────────────────────────────────

const ModeChip: React.FC<{
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  children: React.ReactNode;
}> = ({ active, onClick, icon, children }) => (
  <button
    onClick={onClick}
    className={`flex items-center gap-1 px-2 py-0.5 rounded-md border text-[11px] font-medium transition-colors ${
      active
        ? 'border-purple-500 bg-purple-50 dark:bg-purple-900/30 text-purple-700 dark:text-purple-300'
        : 'border-gray-300 dark:border-gray-600 text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700'
    }`}
  >
    {icon}{children}
  </button>
);

interface TutorBarProps {
  tutor: TutorProfile;
  onPatch: (patch: Partial<TutorProfile>) => void;
  open: boolean;
  onToggle: () => void;
}

const TutorBar: React.FC<TutorBarProps> = ({ tutor, onPatch, open, onToggle }) => {
  const label = tutor.persona
    ? `${tutor.persona} · ${tutor.accent.toUpperCase()} ${tutor.gender}`
    : `${tutor.accent.toUpperCase()} ${tutor.gender} tutor`;

  return (
    <div className="rounded-md border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/40 text-[11px]">
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between gap-2 px-2 py-1 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-md"
      >
        <span className="flex items-center gap-1.5 truncate">
          <Volume2 className="w-3 h-3 text-purple-600 flex-shrink-0" />
          <span className="font-medium">Tutor:</span>
          <span className="truncate">{label}</span>
          {tutor.autoplay && <span className="text-[9px] uppercase tracking-wide text-purple-600 dark:text-purple-400">auto-read</span>}
        </span>
        <Settings2 className="w-3 h-3 text-gray-400 flex-shrink-0" />
      </button>

      {open && (
        <div className="border-t border-gray-200 dark:border-gray-700 p-2 space-y-1.5">
          <div className="grid grid-cols-2 gap-1.5">
            <select
              value={tutor.gender}
              onChange={(e) => onPatch({ gender: e.target.value as any })}
              className="px-1.5 py-0.5 text-[11px] border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
            >
              <option value="female">Female</option>
              <option value="male">Male</option>
            </select>
            <select
              value={tutor.accent}
              onChange={(e) => onPatch({ accent: e.target.value as any })}
              className="px-1.5 py-0.5 text-[11px] border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
            >
              <option value="us">American</option>
              <option value="uk">British</option>
            </select>
          </div>
          <div className="flex gap-1">
            <input
              type="text"
              value={tutor.persona}
              onChange={(e) => onPatch({ persona: e.target.value })}
              placeholder="Persona name"
              className="flex-1 px-1.5 py-0.5 text-[11px] border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
            />
            {PERSONA_SUGGESTIONS.filter(s => s !== tutor.persona).slice(0, 2).map(s => (
              <button
                key={s}
                onClick={() => onPatch({ persona: s })}
                className="px-1.5 py-0.5 text-[10px] rounded border border-gray-300 dark:border-gray-600 text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700"
              >
                {s}
              </button>
            ))}
          </div>
          <label className="flex items-center gap-1.5 text-gray-700 dark:text-gray-300">
            <input
              type="checkbox"
              checked={tutor.autoplay}
              onChange={(e) => onPatch({ autoplay: e.target.checked })}
              className="accent-purple-600"
            />
            Read feedback aloud on wrong answers
          </label>
          <div className="flex items-center gap-1.5">
            <span className="text-gray-500 dark:text-gray-400">Speed:</span>
            <input
              type="range" min={0.75} max={1.25} step={0.05}
              value={tutor.speed}
              onChange={(e) => onPatch({ speed: parseFloat(e.target.value) })}
              className="flex-1 accent-purple-600"
            />
            <span className="font-mono text-gray-500 dark:text-gray-400 w-8 text-right">{tutor.speed.toFixed(2)}×</span>
          </div>
        </div>
      )}
    </div>
  );
};

// ─── Helpers ────────────────────────────────────────────────────────────────

function stripMarkdownForSpeech(s: string): string {
  return s
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/\*(.+?)\*/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\[(.+?)\]\([^)]+\)/g, '$1')
    .replace(/\s+/g, ' ')
    .trim();
}
