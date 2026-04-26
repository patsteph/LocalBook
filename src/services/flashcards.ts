/**
 * Flash Cards service.
 *
 * Flash Cards reuse the existing Quiz generator + grader. This module only
 * adds the thin client wrappers around the Flash-Cards-specific endpoints:
 *
 *   - /flashcards/tutor/{notebook_id}  GET + PUT (per-notebook tutor voice)
 *   - /flashcards/speak                POST     (one-shot TTS of a short line)
 *   - /voice/transcribe-quick          POST     (one-shot STT — no source created)
 *
 * The main card lifecycle calls go through `quizService` (generate + grade).
 */

import { API_BASE_URL } from './api';
import { quizService, GradeAnswerResponse } from './quiz';

const API_BASE = API_BASE_URL;

// ─── Types ────────────────────────────────────────────────────────────────

export type TutorGender = 'female' | 'male';
export type TutorAccent = 'us' | 'uk';

export interface TutorProfile {
  gender: TutorGender;
  accent: TutorAccent;
  persona: string;         // display-only name, e.g. "Nora"
  voice_id: string;        // concrete Kokoro voice (derived from gender/accent)
  speed: number;           // 0.5..1.5
  autoplay: boolean;       // read feedback aloud on wrong answers
}

export interface TutorUpdate {
  gender?: TutorGender;
  accent?: TutorAccent;
  persona?: string;
  voice_id?: string;
  speed?: number;
  autoplay?: boolean;
}

export type Difficulty = 'easy' | 'medium' | 'hard';

export type AnswerMode = 'click' | 'type' | 'voice';

// ─── Tutor voice ──────────────────────────────────────────────────────────

export const flashcardsService = {
  async getTutor(notebookId: string): Promise<TutorProfile> {
    const resp = await fetch(`${API_BASE}/flashcards/tutor/${notebookId}`);
    if (!resp.ok) throw new Error(`Failed to load tutor profile (${resp.status})`);
    return resp.json();
  },

  async updateTutor(notebookId: string, patch: TutorUpdate): Promise<TutorProfile> {
    const resp = await fetch(`${API_BASE}/flashcards/tutor/${notebookId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    if (!resp.ok) {
      const msg = await resp.text();
      throw new Error(`Failed to update tutor: ${msg || resp.status}`);
    }
    return resp.json();
  },

  /**
   * Render short text as speech in the tutor voice. Returns a same-origin
   * blob URL suitable for `new Audio(url)`.
   *
   * The caller is responsible for revoking the URL when done:
   *   const url = await flashcardsService.speak(...)
   *   const audio = new Audio(url);
   *   audio.onended = () => URL.revokeObjectURL(url);
   */
  async speak(params: {
    notebookId?: string;
    text: string;
    speed?: number;
    voiceId?: string;
    gender?: TutorGender;
    accent?: TutorAccent;
  }): Promise<string> {
    const resp = await fetch(`${API_BASE}/flashcards/speak`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        notebook_id: params.notebookId,
        text: params.text,
        speed: params.speed ?? 1.0,
        voice_id: params.voiceId,
        gender: params.gender,
        accent: params.accent,
      }),
    });
    if (!resp.ok) {
      const msg = await resp.text();
      throw new Error(`Tutor TTS failed: ${msg || resp.status}`);
    }
    const blob = await resp.blob();
    return URL.createObjectURL(blob);
  },

  /**
   * Transcribe a short recorded answer via the existing /voice/transcribe-quick
   * endpoint (stateless — does NOT save as a source).
   */
  async transcribeAnswer(audio: Blob, filename = 'answer.webm'): Promise<string> {
    const fd = new FormData();
    fd.append('file', audio, filename);
    const resp = await fetch(`${API_BASE}/voice/transcribe-quick`, {
      method: 'POST',
      body: fd,
    });
    if (!resp.ok) {
      const msg = await resp.text();
      throw new Error(`Transcription failed: ${msg || resp.status}`);
    }
    const data = await resp.json();
    return (data.text || '').trim();
  },

  // ── Card lifecycle (delegates to quizService) ──────────────────────────

  /**
   * Generate a deck of N flash cards at the given difficulty.
   *
   * Uses a mix of question types for optimal learning:
   * - multiple_choice, true_false: tactile click cards
   * - short_answer, fill_in_the_blank: open recall
   * - visual_diagram: dual-coding with SVG diagrams (1-2 per deck)
   * 
   * Includes learning science principles: variety, spaced repetition,
   * dual coding, and test-enhanced learning.
   */
  async generateDeck(opts: {
    notebookId: string;
    count: number;       // 3..50
    difficulty: Difficulty;
    topic?: string;
    chatContext?: string;
    /** If true, force-include visual_diagram in the requested types. Otherwise
     *  the backend still auto-adds it when the source content has visual keywords. */
    includeVisuals?: boolean;
  }) {
    const count = Math.max(3, Math.min(50, Math.floor(opts.count)));
    // Uses the reliable 3-type mix (same as quiz): multiple_choice is the workhorse,
    // true_false gives tactile click cards, fill_in_the_blank tests specific terms.
    // short_answer was removed because LLMs tend to generate prose answers that fail
    // sanitization. visual_diagram is either force-added here (includeVisuals) or
    // auto-detected by backend based on content keywords.
    const types: string[] = ['multiple_choice', 'true_false', 'fill_in_the_blank'];
    if (opts.includeVisuals) types.push('visual_diagram');
    return quizService.generate(
      opts.notebookId,
      count,
      opts.difficulty,
      opts.topic,
      opts.chatContext,
      types,
    );
  },

  async gradeCard(params: {
    question: string;
    correctAnswer: string;
    userAnswer: string;
    questionType: string;
  }): Promise<GradeAnswerResponse> {
    return quizService.gradeAnswer({
      question: params.question,
      correct_answer: params.correctAnswer,
      user_answer: params.userAnswer,
      question_type: params.questionType,
    });
  },
};
