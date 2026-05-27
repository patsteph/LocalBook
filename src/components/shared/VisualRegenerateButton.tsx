/**
 * VisualRegenerateButton — "Regenerate" affordance for Klein full-bleed
 * visuals.
 *
 * Klein has real seed variance: re-running the same prompt produces a
 * meaningfully different image. Before this button, the user's only
 * option to re-roll was to delete the visual and re-issue the original
 * request via the chat bar (lossy — loses the canvas context). This
 * button keeps the canvas item in place and updates its content with
 * the new generation.
 *
 * Implementation: calls /visual/v2/compose with the same topic (no
 * force_idiom — lets the intent classifier route again, which will
 * land on Klein full-bleed for the same input). On success, replaces
 * the canvas item's content + critic + provenance metadata via the
 * canvas context's updateCanvasItem (which now merges metadata, so
 * the user's overlay choices survive the regenerate).
 */
import React from 'react';
import { useCanvasItems } from '../canvas/CanvasContext';
import { useAppShell } from '../canvas/CanvasContext';
import { localFetch, API_BASE_URL } from '../../services/api';

interface VisualRegenerateButtonProps {
  itemId: string;
  notebookId: string;
  originalPrompt?: string;
}

export const VisualRegenerateButton: React.FC<VisualRegenerateButtonProps> = ({
  itemId,
  notebookId,
  originalPrompt,
}) => {
  const { updateCanvasItem } = useCanvasItems();
  const { addToast } = useAppShell();
  const [busy, setBusy] = React.useState(false);

  // No prompt = can't regenerate. Hide the button entirely.
  if (!originalPrompt || !notebookId) return null;

  const handleClick = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const response = await localFetch(`${API_BASE_URL}/visual/v2/compose`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          notebook_id: notebookId,
          topic: originalPrompt,
          // No force_idiom: let the classifier route again. For the same
          // input it'll land on the same path (Klein full-bleed) but
          // Klein will use a different seed → different image.
        }),
      });
      if (!response.ok) {
        throw new Error(`Regenerate failed: HTTP ${response.status}`);
      }
      const visual = await response.json();
      if (!visual?.svg_markup) {
        throw new Error(visual?.error || 'Regenerate produced no visual');
      }
      // Replace the item's content + provenance. updateCanvasItem now
      // merges metadata, so user's overlay choices (overlayEnabled,
      // overlayPosition, overlayTitle, overlaySubtitle) survive.
      updateCanvasItem(itemId, {
        content: visual.svg_markup,
        status: 'complete',
        title: visual.title || undefined,
        metadata: {
          criticScore: visual.critic_score || undefined,
          v2Path: visual.path,
          v2Setup: visual.setup,
          v2GenerationMs: visual.generation_ms,
          templateId: visual.template_id,
          heroSubtitle: visual.subtitle || '',
        } as any,
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Regenerate failed';
      console.error('[regenerate]', err);
      addToast({
        type: 'error',
        title: 'Could not regenerate visual',
        message: msg,
        duration: 5000,
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <button
      type="button"
      onClick={handleClick}
      disabled={busy}
      className={`text-[10px] underline-offset-2 ${
        busy
          ? 'text-gray-400 dark:text-gray-500 cursor-wait'
          : 'text-gray-500 dark:text-gray-400 hover:text-indigo-600 dark:hover:text-indigo-400 hover:underline'
      }`}
      title="Re-roll Klein with the same prompt — image will differ due to seed variance"
    >
      {busy ? 'Regenerating…' : 'Regenerate ↻'}
    </button>
  );
};
