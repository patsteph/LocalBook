/**
 * Artifact spec — the canonical shape for anything LocalBook generates and
 * renders. One interface, six (and growing) renderer types, four render
 * contexts. Per `READFIRST/in-progress/v2-information-cortex.md` Phase 1.
 *
 * Adding a new artifact type:
 *   1. Add the discriminator to ArtifactType (or use a `json:<kind>` value)
 *   2. Write a Renderer<TPayload> component
 *   3. Call rendererRegistry.register('<type>', YourRenderer) at app boot
 *
 * Adding a new render context:
 *   1. Add to RenderContext
 *   2. Renderers branch on `context` to adapt density / interactivity
 */

import type React from 'react';

// ─── Type discriminator ───────────────────────────────────────────────────
// Raw-content types carry a string payload.
// `json:<kind>` types carry a structured payload specific to that kind
// (quiz, flashcards, chart, audio-player, video-player, note-editor, ...).
// Using a template-literal type keeps the registry strongly typed while
// letting backend services add new kinds without a TS deploy.
export type ArtifactType =
  | 'markdown'
  | 'html'
  | 'newsletter'
  | 'interactive-html'
  | 'svg'
  | 'mermaid'
  | 'klein'
  | `json:${string}`;

// ─── Render contexts ──────────────────────────────────────────────────────
// Renderers may adapt density, interactivity, and chrome based on context.
//   - canvas-full:   primary canvas surface; full chrome, full interaction
//   - chat-inline:   compact card inside a chat bubble
//   - source-viewer: source-document panel; emphasis on legibility
//   - export-image:  Playwright will screenshot — render without interactive chrome
//   - export-pdf:    print-CSS friendly variant
export type RenderContext =
  | 'canvas-full'
  | 'chat-inline'
  | 'source-viewer'
  | 'export-image'
  | 'export-pdf';

// ─── Actions ──────────────────────────────────────────────────────────────
// Optional action affordances exposed by the renderer's chrome. The handler
// may be set later by the parent component (e.g. canvas wires up regenerate
// after the artifact arrives).
export interface ArtifactAction {
  id: string;
  label: string;
  icon?: string;
  handler?: () => void | Promise<void>;
}

// ─── The Artifact envelope ────────────────────────────────────────────────
// Generic over the payload type so json:<kind> artifacts can be typed
// strictly at the renderer level while the registry stays heterogeneous.
export interface Artifact<TPayload = unknown> {
  id: string;
  type: ArtifactType;
  payload: TPayload;

  // Display hints
  palette?: string;
  title?: string;
  tagline?: string;

  // Interactive affordances surfaced by the renderer chrome
  actions?: ArtifactAction[];

  // Free-form bag — notebook_id, source_ids, criticScore, templateId,
  // overlay flags, etc. Migrated incrementally from CanvasItem.metadata.
  metadata?: Record<string, unknown>;
}

// ─── Renderer contract ────────────────────────────────────────────────────
export interface RendererProps<TPayload = unknown> {
  artifact: Artifact<TPayload>;
  context: RenderContext;
  className?: string;
}

export type Renderer<TPayload = unknown> = React.ComponentType<RendererProps<TPayload>>;
