/**
 * MainNavStrip — soft-rounded word buttons for main view navigation.
 *
 * 2026-06-02: replaces the dropdown view-picker. Persistent strip; each
 * button is a word with a tonal outline + subtle hover. Active view has
 * a thin tonal underline + deepened text.
 *
 * Pulse: subscribes to viewPulse events. When fired for a given view,
 * the corresponding button gets a brief opacity wave (one cycle, ~1.8s).
 * Coalesced + suppressed-for-active-view in the viewPulse module so this
 * component just reacts.
 */
import React, { useState, useEffect } from 'react';
import { PanelView, VIEW_LABELS } from './canvas/types';
import { useViewPulse, setActiveView, PULSE_DURATION } from '../lib/viewPulse';

type PulseKind = Extract<PanelView, 'chat' | 'library' | 'constellation' | 'timeline' | 'curator'>;

interface MainNavStripProps {
  views: PanelView[];
  currentView: PanelView;
  onSwitchView: (view: PanelView) => void;
  /** Optional badge (e.g., curator pending draft) — view-id → render. */
  badges?: Partial<Record<PanelView, React.ReactNode>>;
}

export const MainNavStrip: React.FC<MainNavStripProps> = ({ views, currentView, onSwitchView, badges }) => {
  // Tell the pulse bus what view is active so pulses for it get suppressed.
  useEffect(() => {
    setActiveView(currentView);
  }, [currentView]);

  return (
    <nav className="flex items-center gap-0.5" aria-label="Main views">
      {views.map(v => (
        <NavButton
          key={v}
          view={v}
          isActive={v === currentView}
          onClick={() => onSwitchView(v)}
          badge={badges?.[v]}
        />
      ))}
    </nav>
  );
};

interface NavButtonProps {
  view: PanelView;
  isActive: boolean;
  onClick: () => void;
  badge?: React.ReactNode;
}

const PULSE_KINDS: ReadonlyArray<PulseKind> = ['chat', 'library', 'constellation', 'timeline', 'curator'];

const NavButton: React.FC<NavButtonProps> = ({ view, isActive, onClick, badge }) => {
  const [pulseKey, setPulseKey] = useState(0);
  const [isPulsing, setIsPulsing] = useState(false);
  const isPulseEligible = PULSE_KINDS.includes(view as PulseKind);

  // Always subscribe (hook rules); the callback no-ops for non-eligible views.
  useViewPulse(isPulseEligible ? (view as PulseKind) : 'chat', () => {
    if (!isPulseEligible) return;
    setPulseKey(k => k + 1);
    setIsPulsing(true);
  });

  // Clear the pulse flag after one cycle. Keyed on pulseKey so successive
  // pulses each get their own full cycle (rare due to the 30s coalesce).
  useEffect(() => {
    if (!isPulsing) return;
    const t = setTimeout(() => setIsPulsing(false), PULSE_DURATION + 100);
    return () => clearTimeout(t);
  }, [pulseKey, isPulsing]);

  return (
    <button
      onClick={onClick}
      data-view={view}
      className={`relative px-2.5 py-1 text-[12px] rounded-md border transition-colors ${
        isActive
          ? 'border-transparent text-gray-900 dark:text-gray-100 font-medium'
          : 'border-gray-200/40 dark:border-gray-700/40 text-gray-500 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 hover:bg-gray-100/60 dark:hover:bg-gray-800/60'
      } ${isPulsing ? 'lb-nav-pulse' : ''}`}
    >
      {VIEW_LABELS[view]}
      {/* Active-state underline */}
      {isActive && (
        <span
          className="absolute left-2.5 right-2.5 -bottom-[3px] h-[2px] rounded-full bg-blue-500 dark:bg-blue-400"
          aria-hidden="true"
        />
      )}
      {/* Pulse halo — one cycle, restraint baked in. */}
      {isPulsing && (
        <span
          key={pulseKey}
          aria-hidden="true"
          className="absolute inset-0 rounded-md pointer-events-none ring-2 ring-blue-400/0 lb-nav-pulse-ring"
        />
      )}
      {badge}
    </button>
  );
};
