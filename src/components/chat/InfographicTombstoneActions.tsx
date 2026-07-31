/**
 * InfographicTombstoneActions — the first-class action row for an infographic
 * canvas tombstone. Mirrors the affordances a visual tombstone exposes, but
 * built on the infographic lane's own primitives:
 *
 *   1. Provenance line — the router decision (lane · mode · confidence · stage)
 *      that the Studio drawer captured, surfaced compactly on the tombstone.
 *      (The Sources legend itself is rendered by InfographicArtifactRenderer.)
 *
 *   2. Restyle (free) — accent / tone / density / glow are pure CSS-variable
 *      overrides (plan §2.4 "one stylesheet, restyle is free"). They re-render
 *      the SAME artifact instantly with NO regeneration — we just write the
 *      chosen `style` into the payload, which the renderer + export both honor.
 *
 *   3. Regenerate-with-tweak (heavier) — re-runs POST /visual/infographic with
 *      a lane / archetype modifier. Status-aware, like VisualRegenerateButton.
 *
 *   4. Export — PNG / PDF / HTML through the unified /export/artifact pipeline,
 *      saved via the Tauri native dialog (WKWebView breaks <a download>).
 *
 * Fail-open throughout: a failed regenerate/export toasts and leaves the
 * existing artifact intact.
 */
import { useState } from 'react';
import { Wand2, RefreshCw } from 'lucide-react';
import { useCanvasItems, useAppShell } from '../canvas/CanvasContext';
import type { CanvasItem } from '../canvas/types';
import { infographicService } from '../../services/infographic';
import type { InfographicLane } from '../visual/InfographicLanePicker';
import {
  ACCENT_OPTIONS,
  TONE_OPTIONS,
  SCALE_OPTIONS,
  type InfographicStyle,
} from '../artifact/renderers/infographicRestyle';
import { FeedbackThumbs } from '../shared/FeedbackThumbs';

// Archetypes are L2-only (L1 is the single annotated_chart archetype).
const ARCHETYPE_OPTIONS: { id: string; label: string }[] = [
  { id: '', label: 'Auto' },
  { id: 'pipeline_compare', label: 'Compare' },
  { id: 'facts_table', label: 'Facts' },
  { id: 'three_stage', label: 'Pipeline' },
  { id: 'stat_grid', label: 'Stats' },
  { id: 'timeline', label: 'Timeline' },
  { id: 'tree_hierarchy', label: 'Tree' },
];

const REGEN_LANES: { id: InfographicLane; label: string }[] = [
  { id: 'auto', label: 'Auto' },
  { id: 'L1', label: 'Chart' },
  { id: 'L2', label: 'Diagram' },
  { id: 'L3', label: 'Scene' },
  { id: 'L4', label: 'Art' },
];


export function InfographicTombstoneActions({ item }: { item: CanvasItem }) {
  const { updateCanvasItem } = useCanvasItems();
  const { addToast } = useAppShell();

  const meta = item.metadata || {};
  const payload = (meta.infographic || {}) as Record<string, any>;
  const style = (payload.style || {}) as InfographicStyle;
  const routing = (meta.routing || {}) as { mode?: string; confidence?: number; stage?: string; lane?: string };
  const notebookId: string = meta.notebookId || '';
  const topic: string = meta.topic || '';
  const lane: string = (meta.lane || payload.lane || 'L2') as string;
  const isL2 = String(lane).toUpperCase() !== 'L1';

  const busy = item.status === 'generating';

  // ── restyle (free): write the chosen style into the payload ──────────
  const setStyle = (patch: Partial<InfographicStyle>) => {
    const nextStyle = { ...style, ...patch };
    updateCanvasItem(item.id, {
      metadata: { ...meta, infographic: { ...payload, style: nextStyle } },
    });
  };

  // ── regenerate-with-tweak (heavier): re-run the endpoint ─────────────
  const [regenOpen, setRegenOpen] = useState(false);
  const [regenLane, setRegenLane] = useState<InfographicLane>(
    (meta.infographicLane as InfographicLane) || 'auto',
  );
  const [regenArchetype, setRegenArchetype] = useState<string>('');

  const handleRegenerate = async () => {
    if (busy || !notebookId) return;
    // Build B — if the current artifact was AUTO-routed and the user is now
    // forcing a different explicit lane, that's a ground-truth router correction.
    const currentLane = String(routing.lane || lane).toUpperCase();
    const correcting =
      routing.mode === 'auto' &&
      regenLane !== 'auto' &&
      String(regenLane).toUpperCase() !== currentLane;
    updateCanvasItem(item.id, { status: 'generating' });
    try {
      const result = await infographicService.generate(
        notebookId, topic, regenLane, regenArchetype || undefined,
        correcting ? currentLane : undefined,
        correcting ? routing.confidence : undefined,
      );
      const newPayload = (result.artifact as any)?.payload || {};
      // Preserve the user's restyle across a regenerate.
      updateCanvasItem(item.id, {
        status: 'complete',
        metadata: {
          ...meta,
          infographic: { ...newPayload, style },
          artifact: result.artifact,
          lane: result.lane,
          routing: result.routing,
          infographicLane: regenLane,
        },
      });
      setRegenOpen(false);
      addToast({ type: 'success', title: 'Infographic regenerated' });
    } catch (err) {
      updateCanvasItem(item.id, { status: 'complete' });
      addToast({
        type: 'error',
        title: 'Could not regenerate infographic',
        message: err instanceof Error ? err.message : 'Unknown error',
        duration: 5000,
      });
    }
  };


  const confPct = typeof routing.confidence === 'number'
    ? `${Math.round(routing.confidence * 100)}%` : null;

  const chipBase =
    'text-[10px] px-1.5 py-0.5 rounded border transition-colors disabled:opacity-50 disabled:cursor-not-allowed';
  const chipOff =
    'border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-600 dark:text-gray-300 hover:border-[#e0503a]/50';
  const chipOn = 'border-[#e0503a] bg-[#e0503a]/10 text-[#e0503a]';

  return (
    <div className="mt-3 border-t border-gray-100 dark:border-gray-700/50 pt-2 space-y-2">
      {/* 1. Provenance / router info line */}
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-1.5 text-[10px] text-gray-500 dark:text-gray-400">
          <span className="inline-flex items-center px-1.5 py-0.5 rounded font-semibold border border-[#e0503a]/40 text-[#e0503a]">
            Lane {String(lane).toUpperCase()}
          </span>
          {routing.mode && <span>· {routing.mode}</span>}
          {confPct && <span>· {confPct}</span>}
          {routing.stage && <span>· stage {routing.stage}</span>}
          {payload.archetype && payload.archetype !== 'annotated_chart' && (
            <span className="font-mono text-gray-400">· {payload.archetype}</span>
          )}
        </div>
        {notebookId && (
          <FeedbackThumbs
            kind="curator_feature"
            subjectType="studio_infographic"
            subjectId={item.id}
            notebookId={notebookId}
            payload={{ skill_id: 'infographic', lane, archetype: payload.archetype }}
            size="sm"
          />
        )}
      </div>

      {/* 2. Restyle (free) — accent / tone / density / glow */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className="inline-flex items-center gap-1 text-[9px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
          <Wand2 className="w-3 h-3" /> Style
        </span>
        {/* accent swatches */}
        <div className="flex items-center gap-1">
          {ACCENT_OPTIONS.map((a) => {
            const active = (style.accent || 'coral') === a.id;
            return (
              <button
                key={a.id}
                type="button"
                title={a.label}
                aria-pressed={active}
                onClick={() => setStyle({ accent: a.id })}
                className={`w-4 h-4 rounded-full border transition-transform ${active ? 'ring-2 ring-offset-1 ring-gray-400 dark:ring-offset-gray-800 scale-110' : 'border-white/60 hover:scale-110'}`}
                style={{ backgroundColor: a.swatch }}
              />
            );
          })}
        </div>
        {/* tone */}
        <div className="flex items-center gap-1">
          {TONE_OPTIONS.map((t) => {
            const active = (style.tone || 'paper') === t.id;
            return (
              <button key={t.id} type="button" onClick={() => setStyle({ tone: t.id })}
                className={`${chipBase} ${active ? chipOn : chipOff}`} aria-pressed={active}>
                {t.label}
              </button>
            );
          })}
        </div>
        {/* density */}
        <div className="flex items-center gap-1">
          {SCALE_OPTIONS.map((s) => {
            const active = (style.scale || 'cozy') === s.id;
            return (
              <button key={s.id} type="button" onClick={() => setStyle({ scale: s.id })}
                className={`${chipBase} ${active ? chipOn : chipOff}`} aria-pressed={active}>
                {s.label}
              </button>
            );
          })}
        </div>
        {/* glow toggle */}
        <button
          type="button"
          onClick={() => setStyle({ glow: style.glow === false })}
          aria-pressed={style.glow !== false}
          className={`${chipBase} ${style.glow !== false ? chipOn : chipOff}`}
          title="Toggle the hero glow"
        >
          Glow {style.glow === false ? 'off' : 'on'}
        </button>
      </div>

      {/* 3. Regenerate-with-tweak. (Download/export lives in the card's ⋮ menu —
          the same ExportMenu visuals use — so it's consistent + not clipped.) */}
      <div className="flex items-center gap-2 flex-wrap">
        <button
          type="button"
          onClick={() => setRegenOpen((v) => !v)}
          disabled={busy || !notebookId}
          className={`inline-flex items-center gap-1 text-[10px] ${regenOpen ? 'text-[#e0503a]' : 'text-gray-500 dark:text-gray-400 hover:text-[#e0503a]'} disabled:opacity-50`}
          title="Re-run with a different lane / archetype"
        >
          <RefreshCw className={`w-3 h-3 ${busy ? 'animate-spin' : ''}`} />
          {busy ? 'Regenerating…' : 'Regenerate ⟳'}
        </button>
      </div>

      {/* regenerate panel */}
      {regenOpen && (
        <div className="border border-gray-200 dark:border-gray-700 rounded-md bg-gray-50 dark:bg-gray-800/60 p-2 space-y-1.5">
          <div className="flex flex-wrap items-center gap-1">
            <span className="text-[9px] uppercase tracking-wide text-gray-500 dark:text-gray-400 mr-0.5">Lane</span>
            {REGEN_LANES.map((l) => (
              <button key={l.id} type="button" disabled={busy}
                onClick={() => setRegenLane(l.id)}
                className={`${chipBase} ${regenLane === l.id ? chipOn : chipOff}`}>
                {l.label}
              </button>
            ))}
          </div>
          {isL2 && (regenLane === 'L2' || regenLane === 'auto') && (
            <div className="flex flex-wrap items-center gap-1">
              <span className="text-[9px] uppercase tracking-wide text-gray-500 dark:text-gray-400 mr-0.5">Layout</span>
              {ARCHETYPE_OPTIONS.map((a) => (
                <button key={a.id || 'auto'} type="button" disabled={busy}
                  onClick={() => setRegenArchetype(a.id)}
                  className={`${chipBase} ${regenArchetype === a.id ? chipOn : chipOff}`}>
                  {a.label}
                </button>
              ))}
            </div>
          )}
          <div className="flex items-center justify-end gap-1.5">
            <button type="button" onClick={() => setRegenOpen(false)} disabled={busy}
              className="text-[10px] px-1.5 py-0.5 rounded border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-50">
              Cancel
            </button>
            <button type="button" onClick={handleRegenerate} disabled={busy}
              className={`text-[10px] px-2 py-0.5 rounded font-medium ${busy ? 'bg-[#e0503a]/60 text-white cursor-wait' : 'bg-[#e0503a] hover:bg-[#c8452f] text-white'}`}>
              {busy ? 'Regenerating…' : 'Regenerate'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default InfographicTombstoneActions;
