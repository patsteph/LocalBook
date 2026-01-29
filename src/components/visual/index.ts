/**
 * Visual System - Barrel exports
 * 
 * Unified visual components for Chat, Studio, and Findings.
 */

// Core components
export { VisualCore, type VisualData, type VisualCoreProps } from './core/VisualCore';
export { VisualToolbar, type VisualToolbarProps } from './core/VisualToolbar';
export { VisualSkeleton, type VisualSkeletonProps } from './core/VisualSkeleton';

// Surface components
export { InlineVisual, type InlineVisualProps } from './surfaces/InlineVisual';

// Design system
export { 
  DesignSystem,
  PALETTES,
  TYPOGRAPHY,
  SPACING,
  CONSTRAINTS,
  RADIUS,
  SHADOWS,
  Z_INDEX,
  getPalette,
  getContrastText,
  truncateText,
  truncateWords,
  type PaletteId,
  type Palette,
} from './design/DesignSystem';
