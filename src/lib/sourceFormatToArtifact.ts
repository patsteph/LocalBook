/**
 * sourceFormatToArtifactType — maps the `format` field on a source
 * (`SourceContent.format`, originally produced by
 * `backend/services/document_processor.py::_get_file_type`) to an
 * `ArtifactType` the renderer registry can dispatch on.
 *
 * Returns `null` when the format has no rendering improvement to offer
 * (PDF text, web scrape, YouTube transcript, plain txt, code) — the
 * caller should fall through to the raw-text path which preserves
 * highlight and search affordances.
 *
 * No detection heuristics: trusts the backend's `format` field. Sources
 * with a missing/unrecognized format fall through safely.
 *
 * Phase 3 of v2-information-cortex. Reusable for future viewers/exports
 * that need the same mapping.
 */

import type { ArtifactType } from '../types/artifact';

export function sourceFormatToArtifactType(
  format: string | null | undefined,
): ArtifactType | null {
  if (!format) return null;
  const f = format.toLowerCase().trim();
  if (f === 'markdown' || f === 'md' || f === 'mdown') return 'markdown';
  if (f === 'html' || f === 'htm') return 'html';
  // Phase 9 — Correspondent-ingested email + reply-to-ingest forwards
  // render via the permissive Newsletter renderer. SourceNotesViewer
  // additionally checks for content_html availability; when absent,
  // it falls through to the raw-text path (back-compat for sources
  // ingested before content_html was preserved).
  if (f === 'email' || f === 'forward') return 'newsletter';
  return null;
}
