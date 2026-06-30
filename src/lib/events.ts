/**
 * Typed custom-event registry (E3/Q7, 2026-06-30).
 *
 * Replaces scattered `window.dispatchEvent(new CustomEvent('name', { detail }))`
 * + `window.addEventListener('name', handler as unknown as EventListener)` with
 * a single typed surface:
 *   emitEvent('lb:openSource', { sourceId })   // detail is type-checked
 *   const off = onEvent('lb:openSource', d => …) // d is typed; off() unsubscribes
 *
 * Add a new event by adding its name + payload to LBEventMap below.
 */

// Payload (CustomEvent.detail) type per event. `void` = no detail.
export interface LBEventMap {
  // Navigation / open intents
  'lb:openWebResearch': { tab: 'web' | 'site'; query: string };
  'lb:openLibraryItem': { id: string; kind: string; title?: string; raw?: any };
  'lb:openSource': { sourceId: string; notebookId?: string; sourceName?: string; searchTerm?: string; articlePosition?: number };
  'lb:chatPrompt': { text: string };
  'openSourceByName': { notebookId: string; sourceName: string; searchTerm: string };
  'openExportModal': { content: string; title: string; theme: 'light' };
  'openCanvasVisual': { content: string };
  'createFlashcardsDeck': { notebookId: string; topic: string; difficulty: 'easy' | 'medium' | 'hard'; count: number; reason: string };

  // Library refresh pulses (no payload)
  'sourcesUpdated': void;
  'notesUpdated': void;
  'contentUpdated': void;
  'audioUpdated': void;
  'videoUpdated': void;
  'visualsUpdated': void;
  'quizzesUpdated': void;

  // Visual regenerate / swap
  'visualSwapIdiom': { notebookId: string; originalPrompt: string; newIdiom: string; previousIdiom?: string };
  'visualRegenerateWithFeedback': { notebookId: string; originalPrompt: string; reason: string; previousSubjectId: string; previousTemplateId?: string };

  // Feynman in-content nav (emitters only; listeners live in the Feynman/quiz surface)
  'feynmanQuizNav': { topic: string; difficulty: unknown };
  'feynmanAudioNav': { section: string };
}

export type LBEventName = keyof LBEventMap;

/** Dispatch a typed app event. Detail is required for events that carry one,
 *  and omitted for `void` events. */
export function emitEvent<K extends LBEventName>(
  name: K,
  ...args: LBEventMap[K] extends void ? [] : [detail: LBEventMap[K]]
): void {
  window.dispatchEvent(new CustomEvent(name, { detail: args[0] as LBEventMap[K] }));
}

/** Subscribe to a typed app event. Returns an unsubscribe fn (drop-in for a
 *  useEffect cleanup). The handler receives the typed detail. */
export function onEvent<K extends LBEventName>(
  name: K,
  handler: (detail: LBEventMap[K]) => void,
): () => void {
  const listener = (e: Event) => handler((e as CustomEvent<LBEventMap[K]>).detail);
  window.addEventListener(name, listener);
  return () => window.removeEventListener(name, listener);
}
