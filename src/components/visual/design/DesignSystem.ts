/**
 * DesignSystem.ts - Unified design tokens for all visual components
 * 
 * This ensures consistency across:
 * - Inline visuals in Chat (Canvas)
 * - Studio visual panel
 * - Findings cards
 * - Export outputs
 */

// Color Palettes - user can switch between these
export const PALETTES = {
  default: {
    id: 'default',
    name: 'LocalBook',
    primary: '#6366f1',      // Indigo
    secondary: '#a5b4fc',    // Light indigo
    accent: '#f59e0b',       // Amber
    success: '#10b981',      // Emerald
    warning: '#f59e0b',      // Amber
    error: '#ef4444',        // Red
    background: '#1e293b',   // Slate 800 (dark mode default)
    backgroundLight: '#f8fafc', // Slate 50
    text: '#f1f5f9',         // Slate 100
    textLight: '#1e293b',    // Slate 800
    muted: '#94a3b8',        // Slate 400
    border: '#475569',       // Slate 600
    // Node colors for visuals
    nodeColors: ['#6366f1', '#10b981', '#f59e0b', '#ec4899', '#8b5cf6', '#06b6d4'],
  },
  professional: {
    id: 'professional',
    name: 'Professional',
    primary: '#1e40af',      // Blue 800
    secondary: '#3b82f6',    // Blue 500
    accent: '#0ea5e9',       // Sky 500
    success: '#059669',      // Emerald 600
    warning: '#d97706',      // Amber 600
    error: '#dc2626',        // Red 600
    background: '#0f172a',   // Slate 900
    backgroundLight: '#ffffff',
    text: '#f8fafc',
    textLight: '#0f172a',
    muted: '#64748b',
    border: '#334155',
    nodeColors: ['#1e40af', '#059669', '#0ea5e9', '#7c3aed', '#0891b2', '#4f46e5'],
  },
  vibrant: {
    id: 'vibrant',
    name: 'Vibrant',
    primary: '#7c3aed',      // Violet 600
    secondary: '#c084fc',    // Violet 400
    accent: '#f472b6',       // Pink 400
    success: '#22c55e',      // Green 500
    warning: '#fbbf24',      // Amber 400
    error: '#f87171',        // Red 400
    background: '#1e1b4b',   // Indigo 950
    backgroundLight: '#faf5ff', // Violet 50
    text: '#f5f3ff',         // Violet 50
    textLight: '#1e1b4b',
    muted: '#a78bfa',        // Violet 400
    border: '#4c1d95',       // Violet 900
    nodeColors: ['#7c3aed', '#22c55e', '#f472b6', '#06b6d4', '#eab308', '#ec4899'],
  },
  minimal: {
    id: 'minimal',
    name: 'Minimal',
    primary: '#18181b',      // Zinc 900
    secondary: '#52525b',    // Zinc 600
    accent: '#a1a1aa',       // Zinc 400
    success: '#22c55e',
    warning: '#f59e0b',
    error: '#ef4444',
    background: '#18181b',
    backgroundLight: '#fafafa',
    text: '#fafafa',
    textLight: '#18181b',
    muted: '#71717a',
    border: '#3f3f46',
    nodeColors: ['#3f3f46', '#52525b', '#71717a', '#a1a1aa', '#d4d4d8', '#27272a'],
  },
} as const;

export type PaletteId = keyof typeof PALETTES;
export type Palette = typeof PALETTES[PaletteId];

// Typography
export const TYPOGRAPHY = {
  // Font families
  fontFamily: {
    sans: 'ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif',
    mono: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
  },
  
  // Font sizes (px)
  fontSize: {
    xs: 10,
    sm: 12,
    base: 14,
    lg: 16,
    xl: 20,
    '2xl': 24,
    '3xl': 30,
  },
  
  // Font weights
  fontWeight: {
    normal: 400,
    medium: 500,
    semibold: 600,
    bold: 700,
  },
  
  // Line heights
  lineHeight: {
    tight: 1.2,
    normal: 1.5,
    relaxed: 1.75,
  },
} as const;

// Spacing (8px grid system)
export const SPACING = {
  px: 1,
  0: 0,
  1: 4,
  2: 8,
  3: 12,
  4: 16,
  5: 20,
  6: 24,
  8: 32,
  10: 40,
  12: 48,
  16: 64,
} as const;

// Visual constraints for "magical" design
export const CONSTRAINTS = {
  // Title constraints
  title: {
    maxWords: 6,
    maxChars: 40,
  },
  
  // Node/label constraints
  label: {
    maxWords: 5,
    maxChars: 25,
    maxLines: 3,
  },
  
  // Visual structure constraints
  visual: {
    maxNodes: 8,           // No more than 8 nodes per visual
    maxDepth: 3,           // No more than 3 levels deep
    whitespaceRatio: 0.4,  // 40% empty space for breathing room
    minNodeSpacing: 40,    // Minimum px between nodes
  },
  
  // Animation timing
  animation: {
    fadeIn: 300,           // ms
    nodeReveal: 50,        // ms per node stagger
    hover: 150,            // ms
  },
} as const;

// Border radius
export const RADIUS = {
  none: 0,
  sm: 4,
  md: 8,
  lg: 12,
  xl: 16,
  full: 9999,
} as const;

// Shadows
export const SHADOWS = {
  none: 'none',
  sm: '0 1px 2px 0 rgb(0 0 0 / 0.05)',
  md: '0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -2px rgb(0 0 0 / 0.1)',
  lg: '0 10px 15px -3px rgb(0 0 0 / 0.1), 0 4px 6px -4px rgb(0 0 0 / 0.1)',
  glow: (color: string) => `0 0 20px ${color}40`,
} as const;

// Z-index layers
export const Z_INDEX = {
  base: 0,
  overlay: 10,
  modal: 50,
  tooltip: 100,
} as const;

// Helper to get current palette (respects user preference)
export function getPalette(id: PaletteId = 'default'): Palette {
  return PALETTES[id] || PALETTES.default;
}

// Helper to get contrasting text color
export function getContrastText(bgColor: string): string {
  // Simple luminance check
  const hex = bgColor.replace('#', '');
  const r = parseInt(hex.substring(0, 2), 16);
  const g = parseInt(hex.substring(2, 4), 16);
  const b = parseInt(hex.substring(4, 6), 16);
  const luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255;
  return luminance > 0.5 ? '#1e293b' : '#ffffff';
}

// Helper to truncate text with ellipsis
export function truncateText(text: string, maxChars: number): string {
  if (text.length <= maxChars) return text;
  return text.substring(0, maxChars - 1).trim() + '…';
}

// Helper to truncate to word boundary
export function truncateWords(text: string, maxWords: number): string {
  const words = text.split(/\s+/);
  if (words.length <= maxWords) return text;
  return words.slice(0, maxWords).join(' ') + '…';
}

// Export all as unified design system
export const DesignSystem = {
  palettes: PALETTES,
  typography: TYPOGRAPHY,
  spacing: SPACING,
  constraints: CONSTRAINTS,
  radius: RADIUS,
  shadows: SHADOWS,
  zIndex: Z_INDEX,
  getPalette,
  getContrastText,
  truncateText,
  truncateWords,
} as const;

export default DesignSystem;
