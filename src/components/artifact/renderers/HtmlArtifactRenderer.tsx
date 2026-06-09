/**
 * HtmlArtifactRenderer — Shadow DOM + DOMPurify adapter for `'html'`
 * artifacts. Payload is the HTML string.
 *
 * Phase 2 of v2-information-cortex: STATIC HTML ONLY. No <script>, no
 * iframes, no inline `<style>` (a Tailwind utility subset is injected
 * instead — see `htmlArtifactTailwindSubset.ts`).
 *
 * Layered defense (frontend half — backend layer arrives in Phase 6):
 *   1. DOMPurify with html profile, FORBID_TAGS adds dangerous structural
 *      tags, FORBID_ATTR adds `style` (CSS-exfiltration via
 *      background-image: url(...) is NOT covered by DOMPurify's default).
 *   2. Shadow DOM open root isolates the cascade; nothing the payload
 *      sets can reach outside the host element.
 *   3. Document-level CSP (see `src-tauri/tauri.conf.json`) denies any
 *      external resource the payload tries to load even if a tag slipped
 *      through.
 *
 * Interactive HTML (iframe sandbox + srcdoc) is Phase 11.
 *
 * Adapter pattern matches the rest of `renderers/*` — no React.memo,
 * props `{ artifact, context, className }`, pass-through styling.
 */

import React, { useEffect, useRef } from 'react';
import DOMPurify from 'dompurify';
import type { RendererProps } from '../../../types/artifact';
import { HTML_ARTIFACT_TAILWIND_SUBSET } from './htmlArtifactTailwindSubset';

// Config validated at the `sanitize()` call site via DOMPurify's own Config
// interface. Kept mutable (no `as const`) so DOMPurify's `string[]` arg
// types accept it.
const SANITIZE_CONFIG = {
  USE_PROFILES: { html: true },
  // Block dangerous structural tags. `style` blocked because we inject our
  // own subset; allowing payload <style> would defeat the cascade isolation
  // (selectors can target :host or escape via combinators).
  FORBID_TAGS: ['script', 'style', 'iframe', 'object', 'embed', 'link', 'meta', 'base', 'form'],
  // `style` attribute blocked because DOMPurify allows it by default but it
  // is a known CSS-exfiltration vector via `background-image: url(...)`.
  FORBID_ATTR: ['style'],
  ALLOW_DATA_ATTR: false,
  // Belt and braces — DOMPurify already blocks javascript: URIs, but make
  // it explicit so future config drift doesn't reopen the hole.
  ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|tel):|#|\/|[^a-z]|[a-z+.-]+(?:[^a-z+.\-:]|$))/i,
};

export const HtmlArtifactRenderer: React.FC<RendererProps<string>> = ({
  artifact,
  className = '',
}) => {
  const hostRef = useRef<HTMLDivElement>(null);
  const html = typeof artifact.payload === 'string' ? artifact.payload : '';

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;

    const shadow = host.shadowRoot ?? host.attachShadow({ mode: 'open' });
    const clean = DOMPurify.sanitize(html, SANITIZE_CONFIG);

    // K2 (2026-06-09) — read the app's current theme from the root
    // element so Shadow DOM doesn't drift from the parent. Tailwind's
    // dark: prefix keys off `<html class="dark">`; we mirror that as
    // `.lb-html-artifact.lb-dark` inside the shadow tree. Without
    // this, an app in light mode + OS in dark mode renders pale text
    // on a white card and the user can't read anything.
    const isDark = typeof document !== 'undefined'
      && document.documentElement.classList.contains('dark');
    const themeClass = isDark ? 'lb-html-artifact lb-dark' : 'lb-html-artifact';

    // Replace the entire shadow content on every payload change. Cheaper
    // than diffing for the small-card sizes we target in Phase 2, and
    // guarantees no stale nodes survive a sanitization-config tightening.
    shadow.innerHTML = `<style>${HTML_ARTIFACT_TAILWIND_SUBSET}</style><div class="${themeClass}">${clean}</div>`;
  }, [html]);

  // Pass-through styling — see SvgArtifactRenderer for rationale.
  return <div ref={hostRef} className={className} />;
};

export default HtmlArtifactRenderer;
