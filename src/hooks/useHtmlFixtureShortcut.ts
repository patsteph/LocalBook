/**
 * useHtmlFixtureShortcut — dev-only HTML CanvasItem injectors so Phase 2 of
 * v2-information-cortex can be spot-checked in the built app (the user does
 * not run dev mode, so `import.meta.env.DEV` would always be false).
 *
 * Two paths, since keyboard shortcuts can be intercepted by macOS / Tauri:
 *
 *   1. **Console call (preferred)** — exposed unconditionally as
 *      `window.__lbHtmlFixture('benign' | 'malicious')`. Open devtools,
 *      paste it, hit enter. Keyboard-conflict-proof.
 *
 *   2. **Keyboard shortcuts** — Cmd+Shift+Y (benign) / Cmd+Shift+B
 *      (malicious). Gated by `localStorage.lb.devHtmlFixture === '1'`
 *      so they stay out of the way during normal use. Avoided Cmd+Shift+H
 *      and Cmd+Shift+J in case macOS/Tauri intercept those globally.
 *
 * TEMPORARY. Remove this hook (and its consumer in `App.tsx`) when
 * Phase 4 lands the real Studio HTML drawer.
 */

import { useEffect } from 'react';
import type { CanvasItem } from '../components/canvas/types';
import { BENIGN_HTML_FIXTURE, MALICIOUS_HTML_FIXTURE, MALICIOUS_INTERACTIVE_FIXTURE } from '../components/artifact/renderers/htmlArtifactFixtures';

const LOCAL_STORAGE_KEY = 'lb.devHtmlFixture';

type AddCanvasItem = (item: Omit<CanvasItem, 'id' | 'timestamp'> & { id?: string }) => void;
type FixtureKind = 'benign' | 'malicious' | 'interactive-probe';

declare global {
  interface Window {
    __lbHtmlFixture?: (kind?: FixtureKind) => void;
  }
}

function isTextInputTarget(target: EventTarget | null): boolean {
  if (!target || !(target instanceof HTMLElement)) return false;
  const tag = target.tagName.toLowerCase();
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return true;
  if (target.isContentEditable) return true;
  return false;
}

export function useHtmlFixtureShortcut(addCanvasItem: AddCanvasItem): void {
  useEffect(() => {
    const inject = (kind: FixtureKind = 'benign') => {
      if (kind === 'malicious') {
        addCanvasItem({
          type: 'html',
          title: 'HTML fixture (sanitization probe)',
          content: MALICIOUS_HTML_FIXTURE,
          collapsed: false,
          status: 'complete',
        });
      } else if (kind === 'interactive-probe') {
        // Phase 11 — inject the iframe-sandbox escape probe as a quiz
        // canvas item so it routes through the InteractiveHtml renderer.
        addCanvasItem({
          type: 'quiz',
          title: 'Interactive HTML sandbox probe (Phase 11)',
          content: '',
          collapsed: false,
          status: 'complete',
          metadata: { interactive_html: MALICIOUS_INTERACTIVE_FIXTURE } as any,
        });
      } else {
        addCanvasItem({
          type: 'html',
          title: 'HTML fixture (benign)',
          content: BENIGN_HTML_FIXTURE,
          collapsed: false,
          status: 'complete',
        });
      }
    };

    window.__lbHtmlFixture = inject;

    const handler = (e: KeyboardEvent) => {
      if (isTextInputTarget(e.target)) return;
      if (!(e.metaKey || e.ctrlKey) || !e.shiftKey) return;
      if (localStorage.getItem(LOCAL_STORAGE_KEY) !== '1') return;

      const key = e.key.toLowerCase();
      if (key === 'y') {
        e.preventDefault();
        inject('benign');
      } else if (key === 'b') {
        e.preventDefault();
        inject('malicious');
      } else if (key === 'i') {
        // Phase 11 — Cmd+Shift+I fires the iframe sandbox-escape probe.
        e.preventDefault();
        inject('interactive-probe');
      }
    };

    window.addEventListener('keydown', handler);
    return () => {
      window.removeEventListener('keydown', handler);
      delete window.__lbHtmlFixture;
    };
  }, [addCanvasItem]);
}
