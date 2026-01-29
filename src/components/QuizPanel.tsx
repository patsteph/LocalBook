import React, { useState, useEffect } from 'react';
import { quizService, Quiz, ReviewCard, QuizStats } from '../services/quiz';
import { Button } from './shared/Button';
import { LoadingSpinner } from './shared/LoadingSpinner';
import { BookmarkButton } from './shared/BookmarkButton';

interface QuizPanelProps {
  notebookId: string;
}

interface QuizResult {
  questionId: string;
  correct: boolean;
  userAnswer: string;
  correctAnswer: string;
}

export const QuizPanel: React.FC<QuizPanelProps> = ({ notebookId }) => {
  const [mode, setMode] = useState<'generate' | 'review' | 'results'>('generate');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  
  // Generate mode
  const [numQuestions, setNumQuestions] = useState(5);
  const [difficulty, setDifficulty] = useState('medium');
  const [topic, setTopic] = useState('');
  const [quiz, setQuiz] = useState<Quiz | null>(null);
  const [currentQuestionIndex, setCurrentQuestionIndex] = useState(0);
  const [userAnswers, setUserAnswers] = useState<Record<string, string>>({});
  const [answeredQuestions, setAnsweredQuestions] = useState<Set<string>>(new Set());
  const [quizResults, setQuizResults] = useState<QuizResult[]>([]);
  const [quizComplete, setQuizComplete] = useState(false);
  
  // Review mode
  const [dueCards, setDueCards] = useState<ReviewCard[]>([]);
  const [currentCardIndex, setCurrentCardIndex] = useState(0);
  const [showCardAnswer, setShowCardAnswer] = useState(false);
  
  // Stats
  const [stats, setStats] = useState<QuizStats | null>(null);

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
      const result = await quizService.generate(notebookId, numQuestions, difficulty, topic || undefined);
      setQuiz(result);
      setCurrentQuestionIndex(0);
      setUserAnswers({});
      setAnsweredQuestions(new Set());
      setQuizResults([]);
      setQuizComplete(false);
    } catch (err: any) {
      setError(err.message || 'Failed to generate quiz');
    } finally {
      setLoading(false);
    }
  };

  // Handle answer selection
  const handleSelectAnswer = (questionId: string, answer: string) => {
    if (answeredQuestions.has(questionId)) return; // Already answered
    
    setUserAnswers({ ...userAnswers, [questionId]: answer });
  };

  // Submit answer and check if correct
  const handleSubmitAnswer = () => {
    if (!currentQuestion) return;
    
    const userAnswer = userAnswers[currentQuestion.id];
    if (!userAnswer) return;
    
    const isCorrect = userAnswer.toLowerCase().trim() === currentQuestion.answer.toLowerCase().trim();
    
    setAnsweredQuestions(prev => new Set(prev).add(currentQuestion.id));
    setQuizResults(prev => [...prev, {
      questionId: currentQuestion.id,
      correct: isCorrect,
      userAnswer,
      correctAnswer: currentQuestion.answer
    }]);
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

  // Calculate score
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
        // Done reviewing
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
          className={`px-3 py-1.5 text-sm rounded-md ${
            mode === 'generate'
              ? 'bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300'
              : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'
          }`}
        >
          Generate Quiz
        </button>
        <button
          onClick={() => setMode('review')}
          className={`px-3 py-1.5 text-sm rounded-md flex items-center gap-1 ${
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

      {/* Generate Mode */}
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
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-700 text-gray-900 dark:text-white placeholder-gray-400"
            />
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">Focus questions on a specific topic</p>
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
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-md bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
            >
              <option value="easy">Easy</option>
              <option value="medium">Medium</option>
              <option value="hard">Hard</option>
            </select>
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

          <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-4">
            {/* Difficulty badge */}
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
                {currentQuestion.question_type === 'true_false' ? 'True/False' : 'Multiple Choice'}
              </span>
            </div>

            {/* Question */}
            <p className="font-medium text-gray-900 dark:text-white mb-4">
              {currentQuestion.question}
            </p>

            {/* Answer Options */}
            <div className="space-y-2 mb-4">
              {(currentQuestion.options || ['True', 'False']).map((option, i) => {
                const isSelected = userAnswers[currentQuestion.id] === option;
                const isAnswered = answeredQuestions.has(currentQuestion.id);
                const isCorrect = option.toLowerCase().trim() === currentQuestion.answer.toLowerCase().trim();
                
                let buttonClass = 'border-gray-200 dark:border-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-700';
                
                if (isAnswered) {
                  if (isCorrect) {
                    buttonClass = 'border-green-500 bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300';
                  } else if (isSelected) {
                    buttonClass = 'border-red-500 bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300';
                  } else {
                    buttonClass = 'border-gray-200 dark:border-gray-700 text-gray-400 dark:text-gray-500';
                  }
                } else if (isSelected) {
                  buttonClass = 'border-purple-500 bg-purple-50 dark:bg-purple-900/20 text-purple-700 dark:text-purple-300';
                }
                
                return (
                  <button
                    key={i}
                    onClick={() => handleSelectAnswer(currentQuestion.id, option)}
                    disabled={isAnswered}
                    className={`w-full text-left px-3 py-2.5 rounded-lg border-2 transition-colors flex items-center justify-between ${buttonClass}`}
                  >
                    <span>{option}</span>
                    {isAnswered && isCorrect && <span className="text-green-600 dark:text-green-400">✓</span>}
                    {isAnswered && isSelected && !isCorrect && <span className="text-red-600 dark:text-red-400">✗</span>}
                  </button>
                );
              })}
            </div>

            {/* Submit or feedback */}
            {!answeredQuestions.has(currentQuestion.id) ? (
              <Button 
                onClick={handleSubmitAnswer} 
                disabled={!userAnswers[currentQuestion.id]}
                className="w-full"
              >
                Submit Answer
              </Button>
            ) : (
              <div className="space-y-3">
                {/* Result */}
                {quizResults.find(r => r.questionId === currentQuestion.id)?.correct ? (
                  <div className="bg-green-50 dark:bg-green-900/20 p-3 rounded-lg border border-green-200 dark:border-green-800">
                    <p className="text-sm font-medium text-green-800 dark:text-green-300">✅ Correct!</p>
                  </div>
                ) : (
                  <div className="bg-red-50 dark:bg-red-900/20 p-3 rounded-lg border border-red-200 dark:border-red-800">
                    <p className="text-sm font-medium text-red-800 dark:text-red-300">❌ Incorrect</p>
                    <p className="text-sm text-red-700 dark:text-red-400 mt-1">
                      Correct answer: <strong>{currentQuestion.answer}</strong>
                    </p>
                  </div>
                )}
                
                {/* Explanation */}
                <div className="bg-blue-50 dark:bg-blue-900/20 p-3 rounded-lg border border-blue-200 dark:border-blue-800">
                  <p className="text-sm font-medium text-blue-800 dark:text-blue-300">💡 Explanation</p>
                  <p className="text-sm text-blue-700 dark:text-blue-400 mt-1">{currentQuestion.explanation}</p>
                </div>
              </div>
            )}
          </div>

          {/* Navigation */}
          {answeredQuestions.has(currentQuestion.id) && (
            <Button onClick={handleNextQuestion} className="w-full">
              {currentQuestionIndex < quiz.questions.length - 1 ? 'Next Question →' : '🎉 See Results'}
            </Button>
          )}
        </div>
      )}

      {/* Quiz Results */}
      {mode === 'generate' && quiz && quizComplete && (
        <div className="space-y-4">
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
            <Button onClick={() => { setQuiz(null); setQuizComplete(false); }} className="flex-1">
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
                          className="px-2 py-2 bg-red-100 dark:bg-red-900/40 text-red-700 dark:text-red-300 rounded-md text-sm hover:bg-red-200 dark:hover:bg-red-900/60 transition-colors"
                        >
                          Again
                        </button>
                        <button
                          onClick={() => handleReviewRating(2)}
                          className="px-2 py-2 bg-orange-100 dark:bg-orange-900/40 text-orange-700 dark:text-orange-300 rounded-md text-sm hover:bg-orange-200 dark:hover:bg-orange-900/60 transition-colors"
                        >
                          Hard
                        </button>
                        <button
                          onClick={() => handleReviewRating(3)}
                          className="px-2 py-2 bg-green-100 dark:bg-green-900/40 text-green-700 dark:text-green-300 rounded-md text-sm hover:bg-green-200 dark:hover:bg-green-900/60 transition-colors"
                        >
                          Good
                        </button>
                        <button
                          onClick={() => handleReviewRating(4)}
                          className="px-2 py-2 bg-blue-100 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300 rounded-md text-sm hover:bg-blue-200 dark:hover:bg-blue-900/60 transition-colors"
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
