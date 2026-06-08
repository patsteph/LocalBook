/**
 * MermaidArtifactRenderer — thin adapter wrapping `MermaidRenderer` for
 * the artifact registry. Payload is the Mermaid code string.
 */

import React from 'react';
import type { RendererProps } from '../../../types/artifact';
import { MermaidRenderer } from '../../shared/MermaidRenderer';

export const MermaidArtifactRenderer: React.FC<RendererProps<string>> = ({
  artifact,
  className = '',
}) => {
  const code = typeof artifact.payload === 'string' ? artifact.payload : '';
  // Pass-through styling — see SvgArtifactRenderer for rationale.
  return <MermaidRenderer code={code} className={className} />;
};

export default MermaidArtifactRenderer;
