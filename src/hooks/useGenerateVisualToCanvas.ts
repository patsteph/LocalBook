/**
 * useGenerateVisualToCanvas — single entry point for visual generation.
 *
 * Replaces bespoke generateSmartStream flows in VisualPanel, ChatActionBar,
 * and CanvasWorkspaceOverlay. Every UI surface that creates a visual goes
 * through this hook, so identical input produces identical output — same
 * backend params, same canvas-item shape, same v2 metadata threading,
 * same error handling.
 *
 * Surface-specific UI (loading spinners, toasts) is handled via callbacks
 * passed in `options`. The hook owns the canvas item lifecycle.
 */
import { useCallback } from 'react';
import { visualService } from '../services/visual';
import { useCanvasItems } from '../components/canvas/CanvasContext';
import { useEngagement } from './useEngagement';

interface GenerateOptions {
  /** Entry-point tag for telemetry (e.g., 'studio_bar', 'left_nav', 'canvas_overlay'). */
  source: 'left_nav' | 'studio_bar' | 'canvas_overlay' | 'inline_chat';
  /** Called the moment the placeholder canvas item is created. */
  onStart?: (canvasItemId: string) => void;
  /** Called when the visual finishes successfully. */
  onComplete?: (canvasItemId: string) => void;
  /** Called when generation fails. */
  onError?: (message: string) => void;
}

export interface GenerateResult {
  ok: boolean;
  canvasItemId?: string;
  error?: string;
}

/**
 * Returns an async function that produces a visual from a topic prompt.
 *
 * Contract:
 *   - notebookId + non-empty topic required (returns ok:false otherwise)
 *   - Drops a 'generating' canvas item immediately so the user sees activity
 *   - Calls /visual/smart/stream with FIXED params (auto color theme, no
 *     template_id, no guidance) so output is shape-invariant across surfaces
 *   - On primary event: updates canvas item with svg/mermaid content + full
 *     v2 metadata (criticScore, v2Path, templateId, originalPrompt, etc.)
 *   - On error: marks canvas item as errored + invokes onError callback
 */
export function useGenerateVisualToCanvas() {
  const { addCanvasItem, updateCanvasItem } = useCanvasItems();
  const { capture: captureEngagement } = useEngagement();

  return useCallback(
    async (
      notebookId: string | null,
      topic: string,
      options: GenerateOptions,
    ): Promise<GenerateResult> => {
      if (!notebookId) return { ok: false, error: 'No notebook selected' };
      const trimmed = (topic || '').trim();
      if (!trimmed) return { ok: false, error: 'Topic is empty' };

      const subjId = `studio_visual_${Date.now()}_${options.source}`;
      const canvasItemId = `visual-${subjId}`;

      // Engagement capture — uniform regardless of entry point
      captureEngagement('curator_feature', 'invoked', {
        subject_type: 'studio_visual',
        subject_id: subjId,
        notebook_id: notebookId,
        payload: { skill_id: 'visual', entry_point: options.source, topic_chars: trimmed.length },
      });

      // Canvas placeholder — uniform across surfaces
      addCanvasItem({
        id: canvasItemId,
        type: 'visual',
        title: `Visual: ${trimmed.substring(0, 60)}${trimmed.length > 60 ? '…' : ''}`,
        content: '',
        collapsed: false,
        status: 'generating',
        metadata: { notebookId, source: options.source },
      });

      options.onStart?.(canvasItemId);

      try {
        await visualService.generateSmartStream(
          notebookId,
          trimmed,
          'auto',  // FIXED — no per-surface color theme variation
          // onPrimary
          (diagram) => {
            updateCanvasItem(canvasItemId, {
              content: diagram.svg || diagram.code || '',
              status: 'complete',
              title: diagram.title || `Visual: ${trimmed.substring(0, 60)}`,
              metadata: {
                notebookId,
                source: options.source,
                originalPrompt: trimmed,  // For regenerate-with-feedback + swap idiom
                criticScore: diagram.v2_critic_score || undefined,
                v2Path: diagram.v2_path,
                v2Setup: diagram.v2_setup,
                v2GenerationMs: diagram.v2_generation_ms,
                templateId: diagram.template_id,
              },
            });
            options.onComplete?.(canvasItemId);
          },
          // onAlternative — ignored; canvas shows the primary only
          () => {},
          // onDone
          () => {},
          // onError
          (msg) => {
            updateCanvasItem(canvasItemId, {
              status: 'error',
              metadata: { notebookId, source: options.source, errorMessage: msg },
            });
            options.onError?.(msg);
          },
          // FIXED: no templateId, no guidance — every surface gets the v2 path
          undefined,
          undefined,
        );
        return { ok: true, canvasItemId };
      } catch (err: any) {
        const msg = err?.message || 'Generation failed';
        updateCanvasItem(canvasItemId, {
          status: 'error',
          metadata: { notebookId, source: options.source, errorMessage: msg },
        });
        options.onError?.(msg);
        return { ok: false, canvasItemId, error: msg };
      }
    },
    [addCanvasItem, updateCanvasItem, captureEngagement],
  );
}
