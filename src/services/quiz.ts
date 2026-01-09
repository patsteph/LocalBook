/**
 * Quiz Service - API calls for quiz generation and FSRS spaced repetition
 */

import { API_BASE_URL } from './api';

const API_BASE = API_BASE_URL;

export interface QuizQuestion {
  id: string;
  question: string;
  answer: string;
  explanation: string;
  difficulty: string;
  question_type: string;  // 'multiple_choice' or 'true_false'
  options?: string[];
  source_reference?: string;  // Name of source document
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

export const quizService = {
  async generate(
    notebookId: string,
    numQuestions: number = 5,
    difficulty: string = 'medium',
    topic?: string
  ): Promise<Quiz> {
    const response = await fetch(`${API_BASE}/quiz/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        notebook_id: notebookId,
        num_questions: numQuestions,
        difficulty,
        topic: topic || undefined,
      }),
    });
    if (!response.ok) throw new Error('Failed to generate quiz');
    return response.json();
  },

  async getDueCards(notebookId: string, limit: number = 20): Promise<ReviewCard[]> {
    const response = await fetch(`${API_BASE}/quiz/due/${notebookId}?limit=${limit}`);
    if (!response.ok) throw new Error('Failed to get due cards');
    return response.json();
  },

  async submitReview(cardId: string, rating: number): Promise<any> {
    const response = await fetch(`${API_BASE}/quiz/review`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ card_id: cardId, rating }),
    });
    if (!response.ok) throw new Error('Failed to submit review');
    return response.json();
  },

  async getStats(notebookId: string): Promise<QuizStats> {
    const response = await fetch(`${API_BASE}/quiz/stats/${notebookId}`);
    if (!response.ok) throw new Error('Failed to get stats');
    return response.json();
  },
};
