/**
 * SvgArtifactRenderer — thin adapter wrapping `SVGRenderer` for the
 * artifact registry. Payload is the SVG string.
 *
 * Chrome (hero overlay, regenerate buttons, critic badge, feedback thumbs)
 * is canvas-level — not the renderer's job. See `CanvasItemCard.tsx` for
 * the legacy `VisualChatInlineContent` chrome; that pattern migrates to
 * canvas-level in P1.D.
 *
 * Klein full-bleed visuals also register against this adapter for now
 * (they are SVG content). When Klein grows distinct chrome (e.g. an
 * always-on hero overlay), split into a dedicated KleinArtifactRenderer.
 */

import React from 'react';
import type { RendererProps } from '../../../types/artifact';
import { SVGRenderer } from '../../shared/SVGRenderer';

export const SvgArtifactRenderer: React.FC<RendererProps<string>> = ({
  artifact,
  className = '',
}) => {
  const svg = typeof artifact.payload === 'string' ? artifact.payload : '';
  // Pass-through styling — wrapper classes are the caller's responsibility
  // so we don't double up on borders when the caller already styles its
  // own container (which most existing call sites do).
  return <SVGRenderer svg={svg} title={artifact.title} className={className} />;
};

export default SvgArtifactRenderer;
