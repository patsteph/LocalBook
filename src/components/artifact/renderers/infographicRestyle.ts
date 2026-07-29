/**
 * infographicRestyle.ts — the "one stylesheet, restyle is free" rule (plan
 * §2.4) on the frontend.
 *
 * A restyle is a small set of CSS-variable overrides layered AFTER the
 * byte-identical `INFOGRAPHIC_L2_CSS`. Because the design tokens live in
 * `.ib { --ib-* }`, a later `.ib { --ib-accent: … }` rule wins (equal
 * specificity, later source order) and cascades everywhere the variable is
 * read — so accent / tone / density switches recolor the whole artifact with
 * NO regeneration and NO edit to the base stylesheet.
 *
 * MIRROR: `buildRestyleCss` mirrors the backend
 * `backend/services/infographic/restyle.py` (`restyle_css`). The Shadow-DOM
 * renderer injects THIS copy on screen; the export pipeline injects the Python
 * copy — keep the two token maps in sync so a saved PNG/PDF/HTML matches what
 * the user restyled on screen.
 *
 * NOTE: kept in a SEPARATE file from `infographicDesignSystem.ts` on purpose —
 * that file's single backtick template literal is byte-compared against the
 * Python source, and a second template literal there would break the greedy
 * mirror-extraction regex.
 */

export interface InfographicStyle {
  accent?: string; // one of ACCENT_OPTIONS ids
  tone?: string;   // one of TONE_OPTIONS ids
  scale?: string;  // one of SCALE_OPTIONS ids
  glow?: boolean;  // hero glow on (default) / off
}

// Accent palettes — override the coral trio. "coral" is the default (no CSS).
// `swatch` drives the picker chip; it equals the accent hex.
export const ACCENT_OPTIONS: { id: string; label: string; swatch: string }[] = [
  { id: 'coral', label: 'Coral', swatch: '#e0503a' },
  { id: 'blue', label: 'Blue', swatch: '#2563eb' },
  { id: 'emerald', label: 'Emerald', swatch: '#0f9d70' },
  { id: 'violet', label: 'Violet', swatch: '#7c3aed' },
  { id: 'amber', label: 'Amber', swatch: '#d97706' },
  { id: 'slate', label: 'Slate', swatch: '#475569' },
];

const ACCENTS: Record<string, Record<string, string>> = {
  coral: {},
  blue: {
    '--ib-accent': '#2563eb',
    '--ib-accent-soft': 'rgba(37,99,235,.16)',
    '--ib-glow': 'rgba(37,99,235,.42)',
  },
  emerald: {
    '--ib-accent': '#0f9d70',
    '--ib-accent-soft': 'rgba(15,157,112,.16)',
    '--ib-glow': 'rgba(15,157,112,.42)',
  },
  violet: {
    '--ib-accent': '#7c3aed',
    '--ib-accent-soft': 'rgba(124,58,237,.16)',
    '--ib-glow': 'rgba(124,58,237,.42)',
  },
  amber: {
    '--ib-accent': '#d97706',
    '--ib-accent-soft': 'rgba(217,119,6,.16)',
    '--ib-glow': 'rgba(217,119,6,.42)',
  },
  slate: {
    '--ib-accent': '#475569',
    '--ib-accent-soft': 'rgba(71,85,105,.16)',
    '--ib-glow': 'rgba(71,85,105,.42)',
  },
};

export const TONE_OPTIONS: { id: string; label: string }[] = [
  { id: 'paper', label: 'Paper' },
  { id: 'light', label: 'Light' },
  { id: 'dark', label: 'Dark' },
];

const TONES: Record<string, Record<string, string>> = {
  paper: {},
  light: {
    '--ib-bg': '#ffffff',
    '--ib-card-b': '#f5f3ef',
    '--ib-line': '#e6e4df',
  },
  dark: {
    '--ib-ink': '#f2f0ec',
    '--ib-ink-soft': '#cbc8c2',
    '--ib-muted': '#938f88',
    '--ib-bg': '#1b1a18',
    '--ib-card-a': '#26241f',
    '--ib-card-b': '#201e1b',
    '--ib-line': '#3a3833',
    '--ib-bracket': 'rgba(216,213,205,.5)',
  },
};

export const SCALE_OPTIONS: { id: string; label: string }[] = [
  { id: 'compact', label: 'Compact' },
  { id: 'cozy', label: 'Cozy' },
  { id: 'roomy', label: 'Roomy' },
];

const SCALES: Record<string, string> = {
  cozy: '',
  compact: '0.9',
  roomy: '1.12',
};

/** Resolve the effective accent hex for a style (used by the L1 chart overlay,
 *  which draws with inline styles rather than the shadow-DOM variables). */
export function accentHex(style?: InfographicStyle): string {
  const opt = ACCENT_OPTIONS.find((a) => a.id === (style?.accent || 'coral'));
  return opt ? opt.swatch : '#e0503a';
}

/** Build the CSS-variable override block for a restyle. Returns '' for an
 *  empty/default style. Never throws — bad input degrades to '' so the base
 *  stylesheet (coral/paper) stands unchanged. Mirrors restyle.py:restyle_css. */
export function buildRestyleCss(style?: InfographicStyle): string {
  if (!style || typeof style !== 'object') return '';

  const root: Record<string, string> = {};

  Object.assign(root, ACCENTS[style.accent || 'coral'] || {});

  const toneId = style.tone || 'paper';
  Object.assign(root, TONES[toneId] || {});

  const scale = SCALES[style.scale || 'cozy'] || '';
  if (scale) root['zoom'] = scale;

  const blocks: string[] = [];
  const keys = Object.keys(root);
  if (keys.length > 0) {
    const decls = keys.map((k) => `${k}:${root[k]};`).join('');
    blocks.push(`.ib{${decls}}`);
  }

  if (toneId === 'dark') {
    blocks.push('.ib{background-image:radial-gradient(rgba(220,215,205,.10) 1px, transparent 1.5px);}');
  }

  if (style.glow === false) {
    blocks.push('.ib-glow{box-shadow:none;border-color:var(--ib-line);}');
  }

  return blocks.join('');
}
