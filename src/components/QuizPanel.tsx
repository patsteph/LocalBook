import React, { useState, useEffect } from 'react';
import { quizService, Quiz, ReviewCard, QuizStats, GapAnalysisResponse, KnowledgeGap } from '../services/quiz';
import { Button } from './shared/Button';
import { LoadingSpinner } from './shared/LoadingSpinner';
import { BookmarkButton } from './shared/BookmarkButton';
import { useAppShell } from './canvas/CanvasContext';
import { useEngagement } from '../hooks/useEngagement';
import { FeedbackThumbs } from './shared/FeedbackThumbs';

const ALL_QUESTION_TYPE_OPTIONS = [
  { id: 'multiple_choice', label: 'Multiple Choice' },
  { id: 'true_false', label: 'True / False' },
  { id: 'fill_in_the_blank', label: 'Fill in the Blank' },
  { id: 'short_answer', label: 'Short Answer' },
  { id: 'spot_the_error', label: 'Spot the Error' },
];

interface QuizPanelProps {
  notebookId: string;
  initialTopic?: string;
  initialDifficulty?: string;
  onQuizGenerated?: (quiz: Quiz) => void;
}

interface QuizResult {
  questionId: string;
  correct: boolean;
  userAnswer: string;
  correctAnswer: string;
}

export const QuizPanel: React.FC<QuizPanelProps> = ({ notebookId, initialTopic, initialDifficulty, onQuizGenerated }) => {
  const { chatContext } = useAppShell();
  const [mode, setMode] = useState<'generate' | 'review' | 'results'>('generate');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  // Generate mode
  const [numQuestions, setNumQuestions] = useState(5);
  const [difficulty, setDifficulty] = useState(initialDifficulty || 'medium');
  const [topic, setTopic] = useState(initialTopic || '');
  const [selectedTypes, setSelectedTypes] = useState<string[]>(['multiple_choice', 'true_false', 'fill_in_the_blank']);
  const [quiz, setQuiz] = useState<Quiz | null>(null);
  // 2026-05-23: Phase 7.5 capture — quiz generation engagement.
  const { capture: captureEngagement } = useEngagement();
  const [quizSubjectId, setQuizSubjectId] = useState<string | null>(null);
  const [currentQuestionIndex, setCurrentQuestionIndex] = useState(0);
  // Maps questionId → user's typed/selected answer
  const [userAnswers, setUserAnswers] = useState<Record<string, string>>({});
  // Maps questionId → { correct, feedback, score } — set on reveal
  const [revealedAnswers, setRevealedAnswers] = useState<Record<string, { correct: boolean; feedback: string; score: number }>>({});
  const [gradingId, setGradingId] = useState<string | null>(null);
  const [quizResults, setQuizResults] = useState<QuizResult[]>([]);
  const [quizComplete, setQuizComplete] = useState(false);
  
  // Review mode
  const [dueCards, setDueCards] = useState<ReviewCard[]>([]);
  const [currentCardIndex, setCurrentCardIndex] = useState(0);
  const [showCardAnswer, setShowCardAnswer] = useState(false);
  
  // Stats
  const [stats, setStats] = useState<QuizStats | null>(null);
  
  // Gap analysis
  const [gapAnalysis, setGapAnalysis] = useState<GapAnalysisResponse | null>(null);
  const [analyzingGaps, setAnalyzingGaps] = useState(false);

  // Update from props when navigating from Feynman curriculum
  useEffect(() => {
    if (initialTopic) setTopic(initialTopic);
    if (initialDifficulty) setDifficulty(initialDifficulty);
  }, [initialTopic, initialDifficulty]);

  useEffect(() => {
    loadStats();
    loadDueCards();
  }, [notebookId]);

  const loadStats = async () => {
    try {
      const data = await quizService.getStats(notebookId);
      setStats(data);
    } catch (err) {
      console.error('Failed to load stats:', err);
    }
  };

  const loadDueCards = async () => {
    try {
      const cards = await quizService.getDueCards(notebookId);
      setDueCards(cards);
    } catch (err) {
      console.error('Failed to load due cards:', err);
    }
  };

  const handleGenerateQuiz = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await quizService.generate(notebookId, numQuestions, difficulty, topic || undefined, chatContext || undefined, selectedTypes);
      setQuiz(result);
      setCurrentQuestionIndex(0);
      setUserAnswers({});
      setRevealedAnswers({});
      setQuizResults([]);
      setQuizComplete(false);
      onQuizGenerated?.(result);
      // Phase 7.5 capture (2026-05-23): quiz generation event tagged with
      // difficulty + question count so we can learn what quiz shapes land.
      const subjId = `studio_quiz_${Date.now()}`;
      setQuizSubjectId(subjId);
      captureEngagement('curator_feature', 'invoked', {
        subject_type: 'studio_quiz',
        subject_id: subjId,
        notebook_id: notebookId,
        payload: {
          skill_id: 'quiz',
          difficulty,
          num_questions: numQuestions,
          types: selectedTypes,
          had_chat_context: !!chatContext,
        },
      });
    } catch (err: any) {
      setError(err.message || 'Failed to generate quiz');
    } finally {
      setLoading(false);
    }
  };

  const toggleQuestionType = (typeId: string) => {
    setSelectedTypes(prev =>
      prev.includes(typeId)
        ? prev.length > 1 ? prev.filter(t => t !== typeId) : prev  // keep at least one
        : [...prev, typeId]
    );
  };

  // Reveal answer for choice-based questions instantly on click (no separate Submit)
  const handleSelectChoice = (questionId: string, option: string) => {
    if (revealedAnswers[questionId]) return;
    const q = quiz?.questions.find(q => q.id === questionId);
    if (!q) return;
    const isCorrect = option.toLowerCase().trim() === q.answer.toLowerCase().trim();
    setUserAnswers(prev => ({ ...prev, [questionId]: option }));
    setRevealedAnswers(prev => ({ ...prev, [questionId]: { correct: isCorrect, feedback: q.explanation, score: isCorrect ? 1 : 0 } }));
    setQuizResults(prev => [...prev, { questionId, correct: isCorrect, userAnswer: option, correctAnswer: q.answer }]);
  };

  // For open-ended types: update typed answer
  const handleTypeAnswer = (questionId: string, value: string) => {
    if (revealedAnswers[questionId]) return;
    setUserAnswers(prev => ({ ...prev, [questionId]: value }));
  };

  // Submit open-ended answer to LLM grader
  const handleSubmitOpenEnded = async (questionId: string) => {
    const q = quiz?.questions.find(q => q.id === questionId);
    if (!q || revealedAnswers[questionId]) return;
    const userAnswer = userAnswers[questionId]?.trim();
    if (!userAnswer) return;

    setGradingId(questionId);
    try {
      const gradeResult = await quizService.gradeAnswer({
        question: q.question,
        correct_answer: q.answer,
        user_answer: userAnswer,
        question_type: q.question_type,
      });
      setRevealedAnswers(prev => ({ ...prev, [questionId]: { correct: gradeResult.correct, feedback: gradeResult.feedback, score: gradeResult.score } }));
      setQuizResults(prev => [...prev, { questionId, correct: gradeResult.correct, userAnswer, correctAnswer: q.answer }]);
    } catch {
      // Fallback: string match
      const isCorrect = userAnswer.toLowerCase() === q.answer.toLowerCase();
      setRevealedAnswers(prev => ({ ...prev, [questionId]: { correct: isCorrect, feedback: q.explanation, score: isCorrect ? 1 : 0 } }));
      setQuizResults(prev => [...prev, { questionId, correct: isCorrect, userAnswer, correctAnswer: q.answer }]);
    } finally {
      setGradingId(null);
    }
  };

  // Move to next question or finish quiz
  const handleNextQuestion = () => {
    if (!quiz) return;
    if (currentQuestionIndex < quiz.questions.length - 1) {
      setCurrentQuestionIndex(currentQuestionIndex + 1);
    } else {
      setQuizComplete(true);
    }
  };

  useEffect(() => {
    if (!quizComplete || !quiz) return;
    const missed = quizResults.filter(r => !r.correct);
    if (missed.length === 0) return;
    setAnalyzingGaps(true);
    const missedQuestions = missed.map(m => {
      const q = quiz.questions.find(qq => qq.id === m.questionId);
      return {
        question: q?.question || '',
        correct_answer: m.correctAnswer,
        user_answer: m.userAnswer,
        explanation: q?.explanation || '',
      };
    });
    quizService.analyzeGaps(notebookId, missedQuestions, topic || quiz.topic)
      .then(setGapAnalysis)
      .catch(err => console.error('Gap analysis failed:', err))
      .finally(() => setAnalyzingGaps(false));
  }, [quizComplete]);

  const handleStudyGap = (gap: KnowledgeGap) => {
    setTopic(gap.suggested_topic);
    setQuiz(null);
    setQuizComplete(false);
    setGapAnalysis(null);
  };

  const getScore = () => {
    const correct = quizResults.filter(r => r.correct).length;
    return { correct, total: quizResults.length };
  };

  const handleReviewRating = async (rating: number) => {
    if (!dueCards[currentCardIndex]) return;
    try {
      await quizService.submitReview(dueCards[currentCardIndex].card_id, rating);
      if (currentCardIndex < dueCards.length - 1) {
        setCurrentCardIndex(currentCardIndex + 1);
        setShowCardAnswer(false);
      } else {
        await loadDueCards();
        await loadStats();
        setCurrentCardIndex(0);
        setShowCardAnswer(false);
      }
    } catch (err) {
      console.error('Failed to submit review:', err);
    }
  };

  const currentQuestion = quiz?.questions[currentQuestionIndex];
  const currentCard = dueCards[currentCardIndex];

  const TYPE_LABELS: Record<string, string> = {
    multiple_choice: 'Multiple Choice',
    true_false: 'True / False',
    fill_in_the_blank: 'Fill in the Blank',
    short_answer: 'Short Answer',
    spot_the_error: 'Spot the Error',
  };

  const isChoiceType = (qt: string) => qt === 'multiple_choice' || qt === 'true_false';

  return (
    <div className="space-y-4">
      {/* Stats Bar */}
      {stats && (
        <div className="flex gap-4 text-sm text-gray-600 dark:text-gray-400 bg-gray-50 dark:bg-gray-800 rounded-lg p-3">
          <span><strong>{stats.total_cards}</strong> cards</span>
          <span><strong>{stats.cards_due}</strong> due</span>
          <span><strong>{stats.total_reviews}</strong> reviews</span>
        </div>
      )}

      {/* Mode Tabs */}
      <div className="flex gap-2">
        <button
          onClick={() => setMode('generate')}
          className={`px-3 py-1.5 text-sm rounded-lg ${
            mode === 'generate'
              ? 'bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300'
              : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'
          }`}
        >
          Generate Quiz
        </button>
        <button
          onClick={() => setMode('review')}
          className={`px-3 py-1.5 text-sm rounded-lg flex items-center gap-1 ${
            mode === 'review'
              ? 'bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300'
              : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'
          }`}
        >
          Review {dueCards.length > 0 && <span className="bg-red-500 text-white text-xs px-1.5 rounded-full">{dueCards.length}</span>}
        </button>
      </div>

      {error && (
        <div className="bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 p-3 rounded-lg text-sm">
          {error}
        </div>
      )}

      {/* Generate Mode — setup form */}
      {mode === 'generate' && !quiz && (
        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Topic <span className="text-gray-400 font-normal">(optional)</span>
            </label>
            <input
              type="text"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="e.g., Machine Learning, React Hooks, WWII..."
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400"
            />
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Number of Questions
            </label>
            <input
              type="range"
              min="3"
              max="10"
              value={numQuestions}
              onChange={(e) => setNumQuestions(Number(e.target.value))}
              className="w-full"
            />
            <span className="text-sm text-gray-500 dark:text-gray-400">{numQuestions} questions</span>
          </div>

          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Difficulty
            </label>
            <select
              value={difficulty}
              onChange={(e) => setDifficulty(e.target.value)}
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
            >
              <option value="easy">Easy</option>
              <option value="medium">Medium</option>
              <option value="hard">Hard</option>
            </select>
          </div>

          {/* Question Type Selector */}
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
              Question Types
            </label>
            <div className="flex flex-wrap gap-2">
              {ALL_QUESTION_TYPE_OPTIONS.map(opt => (
                <button
                  key={opt.id}
                  onClick={() => toggleQuestionType(opt.id)}
                  className={`px-3 py-1 text-xs rounded-full border transition-colors ${
                    selectedTypes.includes(opt.id)
                      ? 'bg-purple-100 border-purple-400 text-purple-700 dark:bg-purple-900/50 dark:border-purple-500 dark:text-purple-300'
                      : 'bg-white border-gray-300 text-gray-500 dark:bg-gray-800 dark:border-gray-600 dark:text-gray-400 hover:border-gray-400'
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          <Button onClick={handleGenerateQuiz} disabled={loading} className="w-full">
            {loading ? <LoadingSpinner size="sm" /> : '🎯 Generate Quiz'}
          </Button>
        </div>
      )}

      {/* Active Quiz */}
      {mode === 'generate' && quiz && currentQuestion && !quizComplete && (
        <div className="space-y-4">
          {/* Progress bar */}
          <div className="flex items-center gap-2">
            <div className="flex-1 h-2 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
              <div
                className="h-full bg-purple-500 transition-all"
                style={{ width: `${((currentQuestionIndex + 1) / quiz.questions.length) * 100}%` }}
              />
            </div>
            <span className="text-sm text-gray-500 dark:text-gray-400">
              {currentQuestionIndex + 1}/{quiz.questions.length}
            </span>
          </div>

          {/* Source reference */}
          {currentQuestion.source_reference && (
            <div className="text-xs text-gray-500 dark:text-gray-400 flex items-center gap-1">
              <span>📄</span>
              <span className="truncate">{currentQuestion.source_reference}</span>
            </div>
          )}

          <div className="bg-white dark:bg-gray-800 border border-purple-100 dark:border-purple-900/50 rounded-lg p-4 shadow-sm">
            {/* Type + difficulty badge row */}
            <div className="flex justify-between items-start mb-3">
              <span className={`px-2 py-0.5 text-xs rounded-full ${
                currentQuestion.difficulty === 'easy'
                  ? 'bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400'
                  : currentQuestion.difficulty === 'hard'
                  ? 'bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400'
                  : 'bg-yellow-100 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-400'
              }`}>
                {currentQuestion.difficulty}
              </span>
              <span className="text-xs text-gray-400 dark:text-gray-500">
                {TYPE_LABELS[currentQuestion.question_type] ?? currentQuestion.question_type}
              </span>
            </div>

            {/* Question */}
            <p className="font-medium text-gray-900 dark:text-white mb-4">
              <span className="text-purple-500 dark:text-purple-400 mr-1.5">{currentQuestionIndex + 1}.</span>
              {currentQuestion.question}
            </p>

            {/* ── Choice-based (MC / T/F) — instant reveal on click ── */}
            {isChoiceType(currentQuestion.question_type) && (
              <div className="space-y-1.5">
                {(currentQuestion.options ?? ['True', 'False']).map((option, i) => {
                  const revealed = !!revealedAnswers[currentQuestion.id];
                  const isSelected = userAnswers[currentQuestion.id] === option;
                  const isCorrect = option.toLowerCase().trim() === currentQuestion.answer.toLowerCase().trim();
                  let cls = 'border-gray-200 dark:border-gray-700 hover:border-purple-300 dark:hover:border-purple-600 cursor-pointer';
                  if (revealed) {
                    if (isCorrect) cls = 'border-green-400 bg-green-50 dark:bg-green-900/20 dark:border-green-600';
                    else if (isSelected) cls = 'border-red-400 bg-red-50 dark:bg-red-900/20 dark:border-red-600';
                    else cls = 'border-gray-200 dark:border-gray-700 opacity-50';
                  }
                  return (
                    <button
                      key={i}
                      onClick={() => handleSelectChoice(currentQuestion.id, option)}
                      disabled={revealed}
                      className={`w-full text-left px-3 py-2 rounded-md border text-sm transition-colors flex items-center gap-2 ${cls}`}
                    >
                      <span className="text-xs font-mono text-gray-400 w-4">{String.fromCharCode(65 + i)}</span>
                      <span className="flex-1 text-gray-800 dark:text-gray-200">{option}</span>
                      {revealed && isCorrect && <span className="text-green-600 dark:text-green-400 text-base">✓</span>}
                      {revealed && isSelected && !isCorrect && <span className="text-red-500 text-base">✗</span>}
                    </button>
                  );
                })}
              </div>
            )}

            {/* ── Open-ended (fill_in_blank, short_answer, spot_the_error) ── */}
            {!isChoiceType(currentQuestion.question_type) && (
              <div className="space-y-3">
                {currentQuestion.question_type === 'short_answer' ? (
                  <textarea
                    value={userAnswers[currentQuestion.id] ?? ''}
                    onChange={(e) => handleTypeAnswer(currentQuestion.id, e.target.value)}
                    disabled={!!revealedAnswers[currentQuestion.id]}
                    rows={3}
                    placeholder="Type your answer in 1–2 sentences..."
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400 text-sm resize-none disabled:opacity-60"
                  />
                ) : (
                  <input
                    type="text"
                    value={userAnswers[currentQuestion.id] ?? ''}
                    onChange={(e) => handleTypeAnswer(currentQuestion.id, e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter' && !revealedAnswers[currentQuestion.id]) handleSubmitOpenEnded(currentQuestion.id); }}
                    disabled={!!revealedAnswers[currentQuestion.id]}
                    placeholder={currentQuestion.question_type === 'fill_in_the_blank' ? 'Fill in the blank...' : 'Identify and correct the error...'}
                    className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400 text-sm disabled:opacity-60"
                  />
                )}
                {!revealedAnswers[currentQuestion.id] && (
                  <Button
                    onClick={() => handleSubmitOpenEnded(currentQuestion.id)}
                    disabled={!userAnswers[currentQuestion.id]?.trim() || gradingId === currentQuestion.id}
                    className="w-full"
                  >
                    {gradingId === currentQuestion.id ? <LoadingSpinner size="sm" /> : 'Check Answer'}
                  </Button>
                )}
              </div>
            )}

            {/* ── Feedback panel (shown after reveal for all types) ── */}
            {revealedAnswers[currentQuestion.id] && (
              <div className={`mt-3 p-3 rounded-md text-sm ${
                revealedAnswers[currentQuestion.id].correct
                  ? 'bg-green-50 dark:bg-green-900/20 text-green-800 dark:text-green-300'
                  : 'bg-amber-50 dark:bg-amber-900/20 text-amber-800 dark:text-amber-300'
              }`}>
                <p className="font-medium mb-1">
                  {revealedAnswers[currentQuestion.id].correct ? '✅ Correct!' : `❌ Not quite — answer: ${currentQuestion.answer}`}
                </p>
                <p className="text-xs opacity-80">
                  {revealedAnswers[currentQuestion.id].feedback || currentQuestion.explanation}
                </p>
              </div>
            )}
          </div>

          {/* Next button — shown once answered */}
          {revealedAnswers[currentQuestion.id] && (
            <Button onClick={handleNextQuestion} className="w-full">
              {currentQuestionIndex < quiz.questions.length - 1 ? 'Next Question →' : '🎉 See Results'}
            </Button>
          )}
        </div>
      )}

      {/* Quiz Results */}
      {mode === 'generate' && quiz && quizComplete && (
        <div className="space-y-4">
          {/* 2026-05-23: thumbs on the completed quiz — landed after the
              user has actually answered everything, the best signal moment. */}
          {quizSubjectId && (
            <div className="flex items-center justify-end gap-1.5">
              <span className="text-[10px] text-gray-500 dark:text-gray-400">How was this quiz?</span>
              <FeedbackThumbs
                kind="curator_feature"
                subjectType="studio_quiz"
                subjectId={quizSubjectId}
                notebookId={notebookId}
                payload={{
                  skill_id: 'quiz',
                  difficulty,
                  num_questions: numQuestions,
                  score_pct: Math.round((getScore().correct / Math.max(getScore().total, 1)) * 100),
                }}
                size="sm"
              />
            </div>
          )}
          <div className="text-center py-6 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg">
            <div className="text-5xl mb-3">
              {getScore().correct === getScore().total ? '🏆' : getScore().correct >= getScore().total * 0.7 ? '🎉' : '📚'}
            </div>
            <h3 className="text-2xl font-bold text-gray-900 dark:text-white">
              {getScore().correct} / {getScore().total}
            </h3>
            <p className="text-gray-500 dark:text-gray-400 mt-1">
              {getScore().correct === getScore().total 
                ? 'Perfect score!' 
                : getScore().correct >= getScore().total * 0.7 
                ? 'Great job!' 
                : 'Keep studying!'}
            </p>
            <div className="mt-2 text-sm text-gray-500 dark:text-gray-400">
              {Math.round((getScore().correct / getScore().total) * 100)}% correct
            </div>
          </div>

          {/* Question breakdown */}
          <div className="space-y-2">
            <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300">Question Breakdown</h4>
            {quiz.questions.map((q) => {
              const result = quizResults.find(r => r.questionId === q.id);
              return (
                <div key={q.id} className={`p-3 rounded-lg border ${
                  result?.correct 
                    ? 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800'
                    : 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800'
                }`}>
                  <div className="flex items-start gap-2">
                    <span className={result?.correct ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}>
                      {result?.correct ? '✓' : '✗'}
                    </span>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm text-gray-800 dark:text-gray-200 line-clamp-1">{q.question}</p>
                      {!result?.correct && (
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                          Answer: {q.answer}
                        </p>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          {/* Knowledge Gap Analysis */}
          {analyzingGaps && (
            <div className="flex items-center gap-2 p-4 bg-purple-50 dark:bg-purple-900/20 border border-purple-200 dark:border-purple-800 rounded-lg">
              <LoadingSpinner size="sm" />
              <span className="text-sm text-purple-700 dark:text-purple-300">Analyzing knowledge gaps...</span>
            </div>
          )}

          {gapAnalysis && gapAnalysis.gaps.length > 0 && (
            <div className="space-y-3">
              <h4 className="text-sm font-medium text-purple-700 dark:text-purple-300 flex items-center gap-1.5">
                <span>🎯</span> Knowledge Gaps Detected
              </h4>
              {gapAnalysis.summary && (
                <p className="text-sm text-gray-600 dark:text-gray-400">{gapAnalysis.summary}</p>
              )}
              {gapAnalysis.gaps.map((gap, i) => (
                <div key={i} className="p-3 bg-purple-50 dark:bg-purple-900/20 border border-purple-200 dark:border-purple-800 rounded-lg space-y-2">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-purple-800 dark:text-purple-200">{gap.gap_title}</p>
                      <p className="text-xs text-gray-600 dark:text-gray-400 mt-1">{gap.description}</p>
                    </div>
                  </div>
                  <p className="text-xs text-gray-500 dark:text-gray-400 italic">{gap.study_suggestion}</p>
                  <button
                    onClick={() => handleStudyGap(gap)}
                    className="w-full text-left px-3 py-2 text-xs font-medium bg-purple-100 dark:bg-purple-800/40 text-purple-700 dark:text-purple-300 rounded-lg hover:bg-purple-200 dark:hover:bg-purple-800/60 transition-colors"
                  >
                    🎯 Quiz me on: {gap.suggested_topic}
                  </button>
                </div>
              ))}
            </div>
          )}

          <div className="flex gap-2">
            <BookmarkButton
              notebookId={notebookId}
              type="note"
              title={`Quiz: ${getScore().correct}/${getScore().total} - ${topic || 'General'}`}
              content={{
                text: `Quiz Results: ${getScore().correct}/${getScore().total} (${Math.round((getScore().correct / getScore().total) * 100)}%)`,
                topic: topic || 'General',
                difficulty,
                questions: quiz.questions.map(q => ({
                  question: q.question,
                  answer: q.answer,
                  correct: quizResults.find(r => r.questionId === q.id)?.correct,
                })),
              }}
            />
            <Button onClick={() => { setQuiz(null); setQuizComplete(false); setGapAnalysis(null); }} className="flex-1">
              Generate New Quiz
            </Button>
          </div>
        </div>
      )}

      {/* Review Mode */}
      {mode === 'review' && (
        <div className="space-y-4">
          {dueCards.length === 0 ? (
            <div className="text-center py-8 text-gray-500 dark:text-gray-400">
              <p className="text-4xl mb-2">🎉</p>
              <p>No cards due for review!</p>
              <p className="text-sm">Generate a quiz to create new cards.</p>
            </div>
          ) : currentCard && (
            <>
              <div className="text-sm text-gray-500 dark:text-gray-400">
                Card {currentCardIndex + 1} of {dueCards.length}
              </div>

              <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-4">
                <p className="font-medium text-gray-900 dark:text-white mb-4">
                  {currentCard.question}
                </p>

                {!showCardAnswer ? (
                  <Button onClick={() => setShowCardAnswer(true)} variant="secondary" className="w-full">
                    Show Answer
                  </Button>
                ) : (
                  <div className="space-y-4">
                    <div className="bg-green-50 dark:bg-green-900/20 p-3 rounded-lg">
                      <p className="text-green-700 dark:text-green-400">{currentCard.answer}</p>
                    </div>

                    <div>
                      <p className="text-sm text-gray-600 dark:text-gray-400 mb-2">How well did you remember?</p>
                      <div className="grid grid-cols-4 gap-2">
                        <button
                          onClick={() => handleReviewRating(1)}
                          className="px-2 py-2 bg-red-100 dark:bg-red-900/40 text-red-700 dark:text-red-300 rounded-lg text-sm hover:bg-red-200 dark:hover:bg-red-900/60 transition-colors"
                        >
                          Again
                        </button>
                        <button
                          onClick={() => handleReviewRating(2)}
                          className="px-2 py-2 bg-orange-100 dark:bg-orange-900/40 text-orange-700 dark:text-orange-300 rounded-lg text-sm hover:bg-orange-200 dark:hover:bg-orange-900/60 transition-colors"
                        >
                          Hard
                        </button>
                        <button
                          onClick={() => handleReviewRating(3)}
                          className="px-2 py-2 bg-green-100 dark:bg-green-900/40 text-green-700 dark:text-green-300 rounded-lg text-sm hover:bg-green-200 dark:hover:bg-green-900/60 transition-colors"
                        >
                          Good
                        </button>
                        <button
                          onClick={() => handleReviewRating(4)}
                          className="px-2 py-2 bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300 rounded-lg text-sm hover:bg-blue-200 dark:hover:bg-blue-900/60 transition-colors"
                        >
                          Easy
                        </button>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
};
