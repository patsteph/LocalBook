/**
 * useMainViewShortcuts — keyboard navigation across main views.
 *
 * 2026-06-02: ⌘1-⌘5 jump to Chat / Library / Constellation / Timeline / Curator.
 * ⌘[ (or Cmd+Backspace) returns to the previous view ("smart back").
 * ⌘K reserved for the command palette (wired separately).
 * ESC closes overlays in the parent component (use a separate handler there).
 *
 * Respects text-input focus: shortcuts ignore keyboard activity when the user
 * is typing in an input/textarea/contenteditable. This keeps the keyboard
 * surface usable without surprising the user mid-type.
 */
import { useEffect, useRef, useCallback } from 'react';
import { PanelView } from '../components/canvas/types';

const VIEW_ORDER: PanelView[] = ['chat', 'library', 'constellation', 'timeline', 'curator'];

function isTextInputTarget(target: EventTarget | null): boolean {
  if (!target || !(target instanceof HTMLElement)) return false;
  const tag = target.tagName.toLowerCase();
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return true;
  if (target.isContentEditable) return true;
  return false;
}

interface Options {
  currentView: PanelView;
  onSwitchView: (view: PanelView) => void;
  onOpenCommandPalette?: () => void;
}

export function useMainViewShortcuts({
  currentView,
  onSwitchView,
  onOpenCommandPalette,
}: Options) {
  // Remember the previous view so ⌘[ acts as a smart back.
  const previousViewRef = useRef<PanelView | null>(null);
  const lastViewRef = useRef<PanelView>(currentView);

  useEffect(() => {
    if (lastViewRef.current !== currentView) {
      previousViewRef.current = lastViewRef.current;
      lastViewRef.current = currentView;
    }
  }, [currentView]);

  const goBack = useCallback(() => {
    const prev = previousViewRef.current;
    if (prev && prev !== currentView) onSwitchView(prev);
  }, [currentView, onSwitchView]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (isTextInputTarget(e.target)) return;
      const isMod = e.metaKey || e.ctrlKey;
      if (!isMod) return;

      // ⌘K — command palette
      if (e.key.toLowerCase() === 'k') {
        if (onOpenCommandPalette) {
          e.preventDefault();
          onOpenCommandPalette();
        }
        return;
      }

      // ⌘[ / Cmd+Backspace — smart back
      if (e.key === '[' || (e.key === 'Backspace' && e.metaKey)) {
        e.preventDefault();
        goBack();
        return;
      }

      // ⌘1-⌘5 — direct view jump
      const num = Number(e.key);
      if (Number.isInteger(num) && num >= 1 && num <= VIEW_ORDER.length) {
        const target = VIEW_ORDER[num - 1];
        if (target && target !== currentView) {
          e.preventDefault();
          onSwitchView(target);
        }
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [currentView, onSwitchView, onOpenCommandPalette, goBack]);

  return { goBack, previousView: previousViewRef.current };
}
