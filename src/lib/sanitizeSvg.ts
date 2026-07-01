/**
 * sanitizeSvg (2026-07-01) — client-side XSS scrub for model-authored inline SVG
 * used by quiz / flashcard `visual_diagram` questions.
 *
 * The backend `svg_sanitizer.py` is the canonical pass (runs before persistence);
 * this is defense in depth at every `dangerouslySetInnerHTML` render site, and it
 * also cleans any quiz persisted before the server sanitizer existed. Same
 * DOMPurify SVG profile as `MermaidRenderer.tsx`.
 */
import DOMPurify from 'dompurify';

export function sanitizeSvg(svg: string | null | undefined): string {
  if (!svg) return '';
  return DOMPurify.sanitize(svg, { USE_PROFILES: { svg: true, svgFilters: true } });
}
