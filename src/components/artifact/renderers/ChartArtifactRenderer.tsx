/**
 * ChartArtifactRenderer — thin adapter wrapping `ChartRenderer` for the
 * artifact registry. Payload is a `ChartConfig` JSON object.
 *
 * Registered as `'json:chart'` (the json:<kind> convention for structured
 * payloads). See `src/components/shared/ChartRenderer.tsx` for the
 * config schema.
 */

import React from 'react';
import type { RendererProps } from '../../../types/artifact';
import { ChartRenderer, type ChartConfig } from '../../shared/ChartRenderer';

export const ChartArtifactRenderer: React.FC<RendererProps<ChartConfig>> = ({
  artifact,
  context,
  className = '',
}) => {
  const config = artifact.payload as ChartConfig;
  if (!config) {
    return (
      <div className={`p-3 rounded-lg bg-gray-50 dark:bg-gray-900/40 ${className}`}>
        <p className="text-[11px] text-gray-500 dark:text-gray-400">Chart config missing</p>
      </div>
    );
  }
  // Heights match the legacy VisualCore mapping: compact (chat-inline)
  // = 200, full canvas = 350, leaner for source-viewer / exports.
  const height =
    context === 'chat-inline' ? 200 :
    context === 'canvas-full' ? 350 :
    250;
  return <ChartRenderer config={config} height={height} className={className} />;
};

export default ChartArtifactRenderer;
