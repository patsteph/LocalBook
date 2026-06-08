/**
 * NewsletterArtifactRenderer — Phase 9 of v2-information-cortex.
 *
 * Renders Correspondent-ingested newsletter HTML with the original
 * inline-style layout preserved. Companion to HtmlArtifactRenderer:
 *
 *   HtmlArtifactRenderer    → strict; for LLM-generated HTML (Phase 2)
 *                              strips <style>, style="...", <img>, etc.
 *   NewsletterArtifactRenderer → permissive; for user-supplied newsletter
 *                                HTML the server already sanitized via
 *                                sanitize_html_for_display(). Allows
 *                                inline style="..." minus dangerous CSS
 *                                patterns (url(), @import, expression())
 *                                so layout/typography survive.
 *
 * Defense in depth:
 *  1. Server: sanitize_html_for_display strips trackers + dangerous CSS.
 *  2. Client (here): DOMPurify with permissive config + uponSanitizeAttribute
 *     hook that re-strips url()/@import/expression() from style values.
 *  3. Tauri CSP: blocks outbound network from the WebView.
 *
 * Why no <img>? Tracking pixels. Even the server strips them; the
 * renderer doubles up.
 */

import React, { useEffect, useRef } from 'react';
import DOMPurify from 'dompurify';
import type { RendererProps } from '../../../types/artifact';

// Belt + suspenders against CSS patterns that can phone home or execute.
const DANGEROUS_CSS_RE = /(url\s*\([^)]*\))|(@import\b[^;]*;?)|(expression\s*\([^)]*\))/gi;

// One-time hook registration. DOMPurify hooks are global, so we guard
// with a module-level flag.
let _styleHookInstalled = false;
function ensureStyleHook() {
  if (_styleHookInstalled) return;
  _styleHookInstalled = true;
  DOMPurify.addHook('uponSanitizeAttribute', (_node, data) => {
    if (data.attrName !== 'style' || typeof data.attrValue !== 'string') return;
    // Strip dangerous patterns, then drop empty declarations.
    let val = data.attrValue.replace(DANGEROUS_CSS_RE, '');
    const decls = val
      .split(';')
      .map((d) => d.trim())
      .filter((d) => d && d.includes(':') && d.split(':', 2)[1].trim().length > 0);
    val = decls.join('; ').trim();
    if (!val) {
      data.keepAttr = false;
    } else {
      data.attrValue = val;
    }
  });
}

const SANITIZE_CONFIG = {
  USE_PROFILES: { html: true },
  // Still block: scripts, link/meta/base (network), iframes/objects/embeds
  // (execution surfaces), forms (action exfil), AND <img> (trackers — even
  // though Tauri CSP would block the fetch), AND <style> blocks (we keep
  // INLINE style attributes via the hook above; <style> blocks could
  // contain @import or scoped selectors that fight the host).
  FORBID_TAGS: ['script', 'style', 'iframe', 'object', 'embed', 'link', 'meta', 'base', 'form', 'img'],
  // NB: 'style' is NOT in FORBID_ATTR. Phase 2's HtmlArtifactRenderer adds
  // it for strict mode; we deliberately omit it so the hook can sanitize.
  FORBID_ATTR: [],
  ALLOW_DATA_ATTR: false,
  ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|tel):|#|\/|[^a-z]|[a-z+.-]+(?:[^a-z+.\-:]|$))/i,
};

// Modest container styles injected into the shadow root. We DON'T inject
// a Tailwind subset here — newsletters bring their own styles. Just keep
// the viewport sane and stop runaway widths.
const CONTAINER_CSS = `
  :host, .lb-newsletter-root {
    all: initial;
    display: block;
    color: #111827;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    font-size: 14px;
    line-height: 1.5;
    max-width: 100%;
    overflow-x: auto;
  }
  .lb-newsletter-root *, .lb-newsletter-root *::before, .lb-newsletter-root *::after {
    box-sizing: border-box;
    max-width: 100%;
  }
  .lb-newsletter-root a { color: #2563eb; }
  .lb-newsletter-root table { border-collapse: collapse; }
`;

export const NewsletterArtifactRenderer: React.FC<RendererProps<string>> = ({
  artifact,
  className = '',
}) => {
  const hostRef = useRef<HTMLDivElement>(null);
  const html = typeof artifact.payload === 'string' ? artifact.payload : '';

  useEffect(() => {
    ensureStyleHook();
    const host = hostRef.current;
    if (!host) return;

    const shadow = host.shadowRoot ?? host.attachShadow({ mode: 'open' });
    const clean = DOMPurify.sanitize(html, SANITIZE_CONFIG);
    shadow.innerHTML = `<style>${CONTAINER_CSS}</style><div class="lb-newsletter-root">${clean}</div>`;
  }, [html]);

  return <div ref={hostRef} className={className} />;
};

export default NewsletterArtifactRenderer;
