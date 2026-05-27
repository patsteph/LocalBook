/**
 * VisualIdiomSwap — small "Swap idiom" affordance on canvas visual cards.
 *
 * When the v2 picker chose poorly, this lets the user override it with any
 * idiom from the same category (3-4 options per category, ~16 total).
 * Clicking dispatches a global event that App.tsx catches and re-runs
 * generation with `force_idiom` set.
 *
 * Categories mirror backend services/visual_idioms.py CATEGORIES dict —
 * kept in sync manually since the catalog is small and changes rarely.
 */
import React, { useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

const CATEGORIES: Record<string, { label: string; idioms: { id: string; label: string }[] }> = {
  ARCHITECTURE: {
    label: 'Architecture',
    idioms: [
      { id: 'microservices_mesh', label: 'Microservices mesh' },
      { id: 'layered_architecture', label: 'Layered architecture' },
      { id: 'cqrs_pattern', label: 'CQRS pattern' },
      { id: 'swimlane', label: 'Swimlane' },
    ],
  },
  COMPARISON: {
    label: 'Comparison',
    idioms: [
      { id: 'comparison_matrix', label: 'Comparison matrix' },
      { id: 'quadrant_2x2', label: '2×2 quadrant' },
      { id: 'before_after', label: 'Before / after' },
      { id: 'pros_cons', label: 'Pros / cons' },
    ],
  },
  PROCESS: {
    label: 'Process',
    idioms: [
      { id: 'linear_process', label: 'Linear process' },
      { id: 'request_flow', label: 'Request flow' },
      { id: 'journey_map', label: 'Journey map' },
      { id: 'decision_tree', label: 'Decision tree' },
    ],
  },
  DATA: {
    label: 'Data',
    idioms: [
      { id: 'stat_callouts', label: 'Stat callouts' },
      { id: 'timeline', label: 'Timeline' },
    ],
  },
  STRUCTURE: {
    label: 'Structure',
    idioms: [
      { id: 'tree_hierarchy', label: 'Tree hierarchy' },
      { id: 'concept_map', label: 'Concept map' },
    ],
  },
  HERO: {
    label: 'Hero',
    idioms: [
      { id: 'value_proposition', label: 'Value proposition' },
      { id: 'hero_with_callouts', label: 'Hero + callouts (Klein)' },
    ],
  },
};

// Find which category an idiom_id belongs to.
function categoryFor(idiomId?: string): string | null {
  if (!idiomId) return null;
  for (const [cat, meta] of Object.entries(CATEGORIES)) {
    if (meta.idioms.some((i) => i.id === idiomId)) return cat;
  }
  return null;
}

interface VisualIdiomSwapProps {
  currentIdiom?: string;
  notebookId: string;
  originalPrompt?: string;
}

const MENU_MAX_H = 200; // matches max-h-[200px] tailwind class on the menu
const MENU_W = 200;
const EDGE_PAD = 8;
const TRIGGER_GAP = 4;

export const VisualIdiomSwap: React.FC<VisualIdiomSwapProps> = ({
  currentIdiom,
  notebookId,
  originalPrompt,
}) => {
  const [open, setOpen] = useState(false);
  const [swapping, setSwapping] = useState(false);
  const [coords, setCoords] = useState<{
    top: number;
    left: number;
    flipUp: boolean;
  }>({ top: 0, left: 0, flipUp: false });
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  // Position computation runs whenever the menu opens. We use position:fixed
  // and portal into document.body so the menu escapes parent overflow:
  // hidden/auto clipping — the previous absolute-positioned version got
  // cropped inside CanvasItemCard's overflow wrapper, leaving the menu
  // unreachable when the visual sat near the bottom of the canvas.
  useLayoutEffect(() => {
    if (!open || !triggerRef.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom;
    const spaceAbove = rect.top;
    const flipUp = spaceBelow < MENU_MAX_H + TRIGGER_GAP && spaceAbove > spaceBelow;

    // Right-align the menu to the trigger, but clamp to viewport edges.
    const rawLeft = rect.right - MENU_W;
    const left = Math.max(
      EDGE_PAD,
      Math.min(rawLeft, window.innerWidth - MENU_W - EDGE_PAD),
    );

    setCoords({
      top: flipUp ? rect.top - TRIGGER_GAP : rect.bottom + TRIGGER_GAP,
      left,
      flipUp,
    });
  }, [open]);

  // Close on outside click
  useLayoutEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (
        triggerRef.current && !triggerRef.current.contains(e.target as Node) &&
        menuRef.current && !menuRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [open]);

  // Close on viewport scroll/resize — popover position is computed once on
  // open; if the user scrolls, the menu would visually detach from its
  // trigger. Cheapest fix is to just close.
  useLayoutEffect(() => {
    if (!open) return;
    const onChange = () => setOpen(false);
    window.addEventListener('scroll', onChange, true);
    window.addEventListener('resize', onChange);
    return () => {
      window.removeEventListener('scroll', onChange, true);
      window.removeEventListener('resize', onChange);
    };
  }, [open]);

  // Without the original prompt we can't re-run generation; hide the swap.
  if (!originalPrompt || !notebookId) return null;

  const category = categoryFor(currentIdiom);
  // Show all idioms from the same category as the current pick. If we
  // can't identify the category (legacy idiom or unknown), show nothing.
  if (!category) return null;
  const options = CATEGORIES[category].idioms.filter((i) => i.id !== currentIdiom);
  if (!options.length) return null;

  const swap = (newIdiom: string) => {
    if (swapping) return;
    setSwapping(true);
    setOpen(false);
    window.dispatchEvent(new CustomEvent('visualSwapIdiom', {
      detail: { notebookId, originalPrompt, newIdiom, previousIdiom: currentIdiom },
    }));
  };

  // Portal the menu into document.body so parent overflow can't clip it.
  // Position is computed from the trigger's bounding rect; flip up when
  // there's not enough room below.
  const menu = open ? (
    <div
      ref={menuRef}
      style={{
        position: 'fixed',
        left: coords.left,
        ...(coords.flipUp
          ? { bottom: window.innerHeight - coords.top }
          : { top: coords.top }),
        width: MENU_W,
        maxHeight: MENU_MAX_H,
        zIndex: 9999,
      }}
      className="overflow-y-auto bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg py-1"
    >
      <div className="px-3 py-1 text-[10px] uppercase tracking-wider text-gray-400 dark:text-gray-500 sticky top-0 bg-white dark:bg-gray-800">
        {CATEGORIES[category].label}
      </div>
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          onClick={() => swap(o.id)}
          className="block w-full text-left px-3 py-1.5 text-xs text-gray-700 dark:text-gray-200 hover:bg-indigo-50 dark:hover:bg-indigo-900/30"
        >
          {o.label}
        </button>
      ))}
    </div>
  ) : null;

  return (
    <div className="relative inline-block">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((x) => !x)}
        className="text-[10px] text-gray-500 dark:text-gray-400 hover:text-indigo-600 dark:hover:text-indigo-400 underline-offset-2 hover:underline"
        title={`Replace with another ${CATEGORIES[category].label.toLowerCase()} idiom`}
      >
        {swapping ? 'Swapping…' : 'Swap idiom ▾'}
      </button>
      {menu && createPortal(menu, document.body)}
    </div>
  );
};
