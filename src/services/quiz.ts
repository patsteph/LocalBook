/**
 * Quiz Service - API calls for quiz generation and FSRS spaced repetition
 */

import { API_BASE_URL, localFetch } from './api';

const API_BASE = API_BASE_URL;

export interface QuizQuestion {
  id: string;
  question: string;
  answer: string;
  explanation: string;
  difficulty: string;
  question_type: string;  // 'multiple_choice', 'true_false', 'short_answer', 'fill_in_the_blank', 'visual_diagram'
  options?: string[];
  source_reference?: string;  // Name of source document
  /** Visual diagram SVG for diagram-based flashcards (optional) */
  visual_svg?: string;
  /** Labels that are shown vs hidden in the diagram */
  visual_labels?: {
    shown: string[];
    hidden: string[];  // The answer should be one of these
  };
}

export interface Quiz {
  quiz_id: string;
  notebook_id: string;
  topic: string;
  questions: QuizQuestion[];
  generated_at: string;
  source_summary: string;
}

export interface ReviewCard {
  card_id: string;
  question: string;
  answer: string;
  due_date: string;
  difficulty: number;
  stability: number;
  reps: number;
}

export interface QuizStats {
  notebook_id: string;
  total_cards: number;
  cards_reviewed: number;
  cards_due: number;
  total_reviews: number;
}

export interface KnowledgeGap {
  gap_title: string;
  description: string;
  study_suggestion: string;
  suggested_topic: string;
}

export interface GapAnalysisResponse {
  gaps: KnowledgeGap[];
  summary: string;
  score_percent: number;
}

export interface MissedQuestion {
  question: string;
  correct_answer: string;
  user_answer: string;
  explanation: string;
}

export interface GradeAnswerRequest {
  question: string;
  correct_answer: string;
  user_answer: string;
  question_type: string;
}

export interface GradeAnswerResponse {
  correct: boolean;
  score: number;
  feedback: string;
}

export const OPEN_ENDED_TYPES = new Set(['short_answer', 'spot_the_error', 'fill_in_the_blank']);

export const quizService = {
  async generate(
    notebookId: string,
    numQuestions: number = 5,
    difficulty: string = 'medium',
    topic?: string,
    chatContext?: string,
    questionTypes?: string[],
  ): Promise<Quiz> {
    const response = await localFetch(`${API_BASE}/quiz/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        notebook_id: notebookId,
        num_questions: numQuestions,
        difficulty,
        topic: topic || undefined,
        ...(chatContext ? { chat_context: chatContext } : {}),
        ...(questionTypes?.length ? { question_types: questionTypes } : {}),
      }),
    });
    if (!response.ok) {
      let detail = '';
      try {
        const body = await response.json();
        detail = body?.detail || body?.message || JSON.stringify(body);
      } catch {
        try { detail = await response.text(); } catch {}
      }
      throw new Error(`Quiz generation failed (${response.status})${detail ? `: ${detail}` : ''}`);
    }
    return response.json();
  },

  /** Phase 11 — compose a sandboxed interactive HTML page from a quiz.
   *  Returned string goes into a CanvasItem's metadata.interactive_html
   *  so CanvasItemCard can dispatch it through the InteractiveHtml
   *  artifact renderer. */
  async toInteractiveHtml(questions: QuizQuestion[], title?: string): Promise<string> {
    const response = await localFetch(`${API_BASE}/quiz/interactive-html`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ questions, title: title || undefined }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    return data.html || '';
  },

  // Library: list persisted quizzes for a notebook, newest first (Tier 5).
  async list(notebookId: string): Promise<any[]> {
    const response = await localFetch(`${API_BASE}/quiz/list/${notebookId}`);
    if (!response.ok) throw new Error('Failed to list quizzes');
    return response.json();
  },

  // Library: delete a persisted quiz (Tier 5).
  async delete(quizId: string): Promise<void> {
    const response = await localFetch(`${API_BASE}/quiz/${quizId}`, { method: 'DELETE' });
    if (!response.ok) throw new Error('Failed to delete quiz');
  },

  // Library: download a quiz as markdown (Tier 5). Triggers a browser save.
  async download(quizId: string): Promise<void> {
    const response = await localFetch(`${API_BASE}/quiz/${quizId}/download`);
    if (!response.ok) throw new Error('Failed to download quiz');
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `quiz-${quizId}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  },

  async getDueCards(notebookId: string, limit: number = 20): Promise<ReviewCard[]> {
    const response = await localFetch(`${API_BASE}/quiz/due/${notebookId}?limit=${limit}`);
    if (!response.ok) throw new Error('Failed to get due cards');
    return response.json();
  },

  async submitReview(cardId: string, rating: number): Promise<any> {
    const response = await localFetch(`${API_BASE}/quiz/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ card_id: cardId, rating }),
    });
    if (!response.ok) throw new Error('Failed to submit review');
    return response.json();
  },

  async getStats(notebookId: string): Promise<QuizStats> {
    const response = await localFetch(`${API_BASE}/quiz/stats/${notebookId}`);
    if (!response.ok) throw new Error('Failed to get stats');
    return response.json();
  },

  async gradeAnswer(req: GradeAnswerRequest): Promise<GradeAnswerResponse> {
    const response = await localFetch(`${API_BASE}/quiz/grade`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req),
    });
    if (!response.ok) throw new Error('Failed to grade answer');
    return response.json();
  },

  async analyzeGaps(
    notebookId: string,
    missedQuestions: MissedQuestion[],
    quizTopic?: string,
  ): Promise<GapAnalysisResponse> {
    const response = await localFetch(`${API_BASE}/quiz/gap-analysis`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        notebook_id: notebookId,
        missed_questions: missedQuestions,
        quiz_topic: quizTopic || undefined,
      }),
    });
    if (!response.ok) throw new Error('Failed to analyze knowledge gaps');
    return response.json();
  },
};
