/**
 * registerRenderers — one-shot registration of all built-in artifact
 * renderers. Import this once at app entry (e.g. from `main.tsx`) before
 * any artifact is rendered.
 *
 * Importing multiple times is safe — `register()` warns and replaces, so
 * idempotency holds at the cost of a console warning if you double-import.
 *
 * Adding a new built-in renderer:
 *   1. Create `renderers/YourArtifactRenderer.tsx` implementing `Renderer<TPayload>`
 *   2. Import it here
 *   3. Call `rendererRegistry.register('<type>', YourArtifactRenderer)`
 *
 * Phase 2 (HTML renderer with Shadow DOM + DOMPurify) will register
 * `'html'` here. Phase 4 (Studio HTML types) adds `'json:quiz'`,
 * `'json:flashcards'`, etc. as their renderers land.
 */

import { rendererRegistry } from './RendererRegistry';
import { SvgArtifactRenderer } from './renderers/SvgArtifactRenderer';
import { MermaidArtifactRenderer } from './renderers/MermaidArtifactRenderer';
import { ChartArtifactRenderer } from './renderers/ChartArtifactRenderer';
import { MarkdownArtifactRenderer } from './renderers/MarkdownArtifactRenderer';
import { HtmlArtifactRenderer } from './renderers/HtmlArtifactRenderer';
import { NewsletterArtifactRenderer } from './renderers/NewsletterArtifactRenderer';
import { InteractiveHtmlArtifactRenderer } from './renderers/InteractiveHtmlArtifactRenderer';
import { ComparisonArtifactRenderer } from './renderers/ComparisonArtifactRenderer';

let registered = false;

export function registerBuiltInRenderers(): void {
  if (registered) return;
  registered = true;

  // Raw-content types
  rendererRegistry.register('markdown', MarkdownArtifactRenderer);
  rendererRegistry.register('html', HtmlArtifactRenderer);
  rendererRegistry.register('newsletter', NewsletterArtifactRenderer);
  rendererRegistry.register('interactive-html', InteractiveHtmlArtifactRenderer);
  rendererRegistry.register('svg', SvgArtifactRenderer);
  rendererRegistry.register('mermaid', MermaidArtifactRenderer);

  // Klein full-bleed visuals are SVG content today — alias to the SVG
  // adapter. Split into a dedicated KleinArtifactRenderer when Klein
  // gains distinct chrome (hero overlay always-on, etc.).
  rendererRegistry.register('klein', SvgArtifactRenderer);

  // Structured `json:<kind>` types
  rendererRegistry.register('json:chart', ChartArtifactRenderer);
  rendererRegistry.register('json:comparison', ComparisonArtifactRenderer);
}

// Auto-register on import so consumers don't have to remember to call.
// Safe because `register()` is idempotent and `registered` guards re-runs.
registerBuiltInRenderers();
