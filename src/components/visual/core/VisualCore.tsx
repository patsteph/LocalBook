/**
 * VisualCore.tsx - Unified visual rendering component
 * 
 * This is the core renderer that handles both SVG and Mermaid diagrams.
 * Used by InlineVisual (Chat), StudioVisual, and FindingsCard.
 */

import React from 'react';
import { SVGRenderer } from '../../shared/SVGRenderer';
import { MermaidRenderer } from '../../shared/MermaidRenderer';
import { DesignSystem, type PaletteId } from '../design/DesignSystem';

export interface VisualData {
  id: string;
  type: 'svg' | 'mermaid';
  code: string;
  title?: string;
  template_id?: string;
  pattern?: string;
  tagline?: string;  // Editable summary line shown below visual
}

export interface VisualCoreProps {
  visual: VisualData;
  className?: string;
  palette?: PaletteId;
  compact?: boolean;           // For inline/thumbnail views
  showTitle?: boolean;
}

export const VisualCore: React.FC<VisualCoreProps> = ({
  visual,
  className = '',
  palette = 'default',
  compact = false,
  showTitle = true,
}) => {
  const colors = DesignSystem.getPalette(palette);

  if (!visual || !visual.code) {
    return (
      <div className={`flex items-center justify-center p-4 bg-gray-100 dark:bg-gray-800 rounded-lg ${className}`}>
        <span className="text-sm text-gray-500 dark:text-gray-400">No visual data</span>
      </div>
    );
  }

  // Container styles based on compact mode
  const containerClasses = compact
    ? `visual-core visual-core--compact rounded-lg overflow-hidden ${className}`
    : `visual-core rounded-lg overflow-hidden ${className}`;

  // Determine which renderer to use
  const isSVG = visual.type === 'svg' || visual.code.includes('<svg');

  return (
    <div 
      className={containerClasses}
      style={{
        '--visual-primary': colors.primary,
        '--visual-secondary': colors.secondary,
        '--visual-accent': colors.accent,
        '--visual-bg': colors.background,
        '--visual-text': colors.text,
      } as React.CSSProperties}
    >
      {/* Optional title header */}
      {showTitle && visual.title && !compact && (
        <div 
          className="px-3 py-2 border-b border-gray-700 bg-gray-800/50"
          style={{ borderColor: colors.border }}
        >
          <h3 className="text-sm font-medium text-gray-200 truncate">
            {DesignSystem.truncateText(visual.title, DesignSystem.constraints.title.maxChars)}
          </h3>
          {visual.pattern && (
            <span className="text-xs text-gray-500 capitalize">{visual.pattern.replace(/-/g, ' ')}</span>
          )}
        </div>
      )}

      {/* Visual content */}
      <div className={compact ? 'p-2' : 'p-4'}>
        {isSVG ? (
          <SVGRenderer 
            svg={visual.code}
            className="w-full"
          />
        ) : (
          <MermaidRenderer 
            code={visual.code}
            className="w-full"
          />
        )}
      </div>

    </div>
  );
};

export default VisualCore;
