/**
 * VisualEditRegenerateButton — "Edit ✎" affordance for Klein full-bleed
 * visuals. Lets the user modify the original prompt before re-running.
 *
 * Three workflows captured behind one interface:
 *   1. Free-form edit — user opens the panel, modifies the prompt text
 *      directly, hits Regenerate.
 *   2. Quick refinement — user clicks a preset chip (More detail, Warmer
 *      light, etc.) which APPENDS a curated modifier clause to the
 *      textarea. They can submit immediately or edit further.
 *   3. Reset — Cancel button clears modifications and closes the panel
 *      without regenerating.
 *
 * Same backend flow as VisualRegenerateButton (POST /visual/v2/compose),
 * just with the user's edited prompt instead of the verbatim original.
 * The canvas item's content + critic + metadata are replaced in place;
 * overlay choices survive via the metadata-merge update.
 */
import React from 'react';
import { useCanvasItems, useAppShell } from '../canvas/CanvasContext';
import { localFetch, API_BASE_URL } from '../../services/api';

interface VisualEditRegenerateButtonProps {
  itemId: string;
  notebookId: string;
  originalPrompt?: string;
}

// Session-level cache: the styles list is static and the same for every
// visual, so we fetch once and reuse for the rest of the session.
interface KleinStyle {
  id: string;
  label: string;
  prompt_tail: string;
}
let stylesCache: KleinStyle[] | null = null;
let stylesPromise: Promise<KleinStyle[]> | null = null;

const fetchKleinStyles = async (): Promise<KleinStyle[]> => {
  if (stylesCache) return stylesCache;
  if (stylesPromise) return stylesPromise;
  stylesPromise = (async () => {
    try {
      const res = await localFetch(`${API_BASE_URL}/visual/v2/klein-styles`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const styles: KleinStyle[] = Array.isArray(data?.styles) ? data.styles : [];
      stylesCache = styles;
      return styles;
    } catch (err) {
      console.warn('[edit-regenerate] failed to load klein styles', err);
      stylesCache = [];
      return [];
    } finally {
      stylesPromise = null;
    }
  })();
  return stylesPromise;
};

// Curated refinement modifiers. Each appends a short clause to the prompt
// when the chip is clicked. Kept generic so they compose with most
// existing prompts; user can always edit further before submitting.
const REFINEMENT_CHIPS: { label: string; modifier: string; title: string }[] = [
  {
    label: '↑ More detail',
    modifier: ', hyper-detailed, intricate fine detail, ultra-sharp',
    title: 'Push Klein for finer detail and higher visual fidelity',
  },
  {
    label: '🌅 Warmer light',
    modifier: ', golden hour lighting, warm directional light, soft ambient glow',
    title: 'Re-light the scene with warm golden tones',
  },
  {
    label: '🎨 Bolder palette',
    modifier: ', vivid saturated colors, high color contrast, punchy accent tones',
    title: 'Increase color saturation and contrast',
  },
  {
    label: '🖼 Wider shot',
    modifier: ', wide cinematic shot, expansive composition, generous negative space',
    title: 'Pull the camera back for a wider composition',
  },
  {
    label: '🌙 Softer mood',
    modifier: ', soft and dreamy atmosphere, gentle diffused light, calm tone',
    title: 'Soften the mood and lighting',
  },
  {
    label: '🎬 Cinematic',
    modifier: ', cinematic composition, dramatic lighting, depth of field, anamorphic feel',
    title: 'Shift toward a cinematic film aesthetic',
  },
];

export const VisualEditRegenerateButton: React.FC<VisualEditRegenerateButtonProps> = ({
  itemId,
  notebookId,
  originalPrompt,
}) => {
  const { updateCanvasItem } = useCanvasItems();
  const { addToast } = useAppShell();
  const [open, setOpen] = React.useState(false);
  const [draft, setDraft] = React.useState<string>(originalPrompt || '');
  const [busy, setBusy] = React.useState(false);
  const [kleinStyles, setKleinStyles] = React.useState<KleinStyle[]>(
    () => stylesCache ?? []
  );

  // Reset the draft whenever the panel opens, so leaving and re-opening
  // gives the user a fresh copy of the original prompt to work from.
  React.useEffect(() => {
    if (open) setDraft(originalPrompt || '');
  }, [open, originalPrompt]);

  // Lazy-load the style chip list the first time the panel opens.
  // Result is cached at module level so subsequent opens are free.
  React.useEffect(() => {
    if (!open || kleinStyles.length > 0) return;
    let cancelled = false;
    fetchKleinStyles().then((styles) => {
      if (!cancelled) setKleinStyles(styles);
    });
    return () => {
      cancelled = true;
    };
  }, [open, kleinStyles.length]);

  if (!originalPrompt || !notebookId) return null;

  const applyChip = (modifier: string) => {
    setDraft((cur) => {
      const trimmed = cur.trimEnd();
      // Avoid double-apply when the chip text is already present
      if (trimmed.includes(modifier.trim())) return trimmed;
      return trimmed + modifier;
    });
  };

  const resetDraft = () => setDraft(originalPrompt || '');

  const handleRegenerate = async () => {
    const trimmed = draft.trim();
    if (busy || !trimmed) return;
    setBusy(true);
    try {
      const response = await localFetch(`${API_BASE_URL}/visual/v2/compose`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          notebook_id: notebookId,
          topic: trimmed,
        }),
      });
      if (!response.ok) {
        throw new Error(`Regenerate failed: HTTP ${response.status}`);
      }
      const visual = await response.json();
      if (!visual?.svg_markup) {
        throw new Error(visual?.error || 'Regenerate produced no visual');
      }
      // Replace content + provenance + update the saved originalPrompt
      // so subsequent Regenerate ↻ uses the modified prompt (the user
      // adopted this edit as the new baseline).
      updateCanvasItem(itemId, {
        content: visual.svg_markup,
        status: 'complete',
        title: visual.title || undefined,
        metadata: {
          originalPrompt: trimmed,
          criticScore: visual.critic_score || undefined,
          v2Path: visual.path,
          v2Setup: visual.setup,
          v2GenerationMs: visual.generation_ms,
          templateId: visual.template_id,
          heroSubtitle: visual.subtitle || '',
          suggestedOverlayPosition: visual.suggested_overlay_position,
        } as any,
      });
      setOpen(false);
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Regenerate failed';
      console.error('[edit-regenerate]', err);
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
    <>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={`text-[10px] underline-offset-2 ${
          open
            ? 'text-indigo-600 dark:text-indigo-400 underline'
            : 'text-gray-500 dark:text-gray-400 hover:text-indigo-600 dark:hover:text-indigo-400 hover:underline'
        }`}
        title="Modify the prompt and regenerate"
      >
        Edit ✎
      </button>

      {open && (
        <div className="w-full mt-2 border border-gray-200 dark:border-gray-700 rounded-lg bg-gray-50 dark:bg-gray-800/60 p-3 space-y-2">
          {/* Quick refinement chips — append modifier text to the draft */}
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="text-[10px] uppercase tracking-wide text-gray-500 dark:text-gray-400 mr-1">
              Refine
            </span>
            {REFINEMENT_CHIPS.map((chip) => (
              <button
                key={chip.label}
                type="button"
                onClick={() => applyChip(chip.modifier)}
                title={chip.title}
                disabled={busy}
                className="text-[11px] px-2 py-1 rounded border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-700 dark:text-gray-200 hover:bg-indigo-50 hover:border-indigo-300 dark:hover:bg-indigo-900/30 dark:hover:border-indigo-600 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {chip.label}
              </button>
            ))}
          </div>

          {/* Style preset chips — append style prompt_tail to the draft */}
          {kleinStyles.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-[10px] uppercase tracking-wide text-gray-500 dark:text-gray-400 mr-1">
                Style
              </span>
              {kleinStyles.map((style) => (
                <button
                  key={style.id}
                  type="button"
                  onClick={() => applyChip(`, ${style.prompt_tail}`)}
                  title={style.prompt_tail}
                  disabled={busy}
                  className="text-[11px] px-2 py-1 rounded border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-700 dark:text-gray-200 hover:bg-indigo-50 hover:border-indigo-300 dark:hover:bg-indigo-900/30 dark:hover:border-indigo-600 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {style.label}
                </button>
              ))}
            </div>
          )}

          {/* Editable prompt textarea */}
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            disabled={busy}
            rows={Math.min(8, Math.max(3, draft.split('\n').length + 1))}
            className="w-full text-xs px-2 py-2 border border-gray-200 dark:border-gray-600 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-100 font-mono leading-relaxed resize-y disabled:opacity-50"
            placeholder="Edit the prompt, then regenerate…"
          />

          <div className="flex items-center justify-between gap-2 text-[11px]">
            <button
              type="button"
              onClick={resetDraft}
              disabled={busy || draft === originalPrompt}
              className="text-gray-500 dark:text-gray-400 hover:text-indigo-600 dark:hover:text-indigo-400 underline-offset-2 hover:underline disabled:opacity-40 disabled:cursor-not-allowed disabled:no-underline"
              title="Discard your edits and start from the original prompt"
            >
              ↺ Reset to original
            </button>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setOpen(false)}
                disabled={busy}
                className="px-2 py-1 rounded border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={handleRegenerate}
                disabled={busy || !draft.trim()}
                className={`px-2.5 py-1 rounded font-medium ${
                  busy
                    ? 'bg-indigo-300 text-white cursor-wait'
                    : 'bg-indigo-600 hover:bg-indigo-700 text-white disabled:bg-gray-300 dark:disabled:bg-gray-700 disabled:cursor-not-allowed'
                }`}
              >
                {busy ? 'Regenerating…' : 'Regenerate'}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
};
