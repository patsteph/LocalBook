/**
 * How big should a thread's floating window be?
 *
 * Field feedback (2026-08-18): the focus drawer took a whole side of the screen AND still
 * squashed an infographic, because a fixed-width drawer cannot respect content that has its
 * own natural size. Windows now size to what they hold.
 *
 * Pure on purpose — every function here takes the viewport as an argument rather than reading
 * `window`, so the sizing rules are assertable without a DOM (see the .test.ts alongside).
 */
import type { CanvasNode } from '../../services/canvas';

export interface Size { w: number; h: number }
export interface Point { x: number; y: number }
export interface Viewport { w: number; h: number }

export const MARGIN = 12;
/** Never let a window swallow the canvas — that was the drawer's whole problem. */
export const MAX_FRAC = { w: 0.62, h: 0.86 };
export const MIN_SIZE: Size = { w: 260, h: 120 };

/**
 * Per-type starting size. These are *starting points* — every window is resizable, because no
 * table can know how tall a given document or quiz actually is.
 */
const DEFAULTS: Record<string, Size> = {
  audio: { w: 330, h: 150 },          // the compact player + its status line, nothing more
  video: { w: 420, h: 300 },
  // Infographics are the reason this table exists. The L2 design system lays out 2-, 3- and
  // 5-COLUMN grids (`.ib-compare`, `.ib-fan`, `.ib-stages`) inside 34px padding, so the old
  // 560px drawer left ~490px for a five-column layout — which is what "wasn't even respecting
  // the dimensions of the graphic" looked like. Wide by default, and resizable.
  infographic: { w: 820, h: 620 },
  visual: { w: 560, h: 440 },
  document: { w: 560, h: 640 },       // a readable measure, tall enough to actually read
  quiz: { w: 480, h: 620 },
};
const FALLBACK: Size = { w: 460, h: 400 };

/** Pull `width`/`height` out of an SVG's viewBox (or explicit attributes). */
export function svgAspect(svg: string): number | null {
  if (typeof svg !== 'string') return null;
  const vb = svg.match(/viewBox\s*=\s*["']\s*[-\d.]+[ ,]+[-\d.]+[ ,]+([\d.]+)[ ,]+([\d.]+)/i);
  if (vb) {
    const w = parseFloat(vb[1]);
    const h = parseFloat(vb[2]);
    if (w > 0 && h > 0) return w / h;
  }
  const wm = svg.match(/\bwidth\s*=\s*["']?([\d.]+)/i);
  const hm = svg.match(/\bheight\s*=\s*["']?([\d.]+)/i);
  if (wm && hm) {
    const w = parseFloat(wm[1]);
    const h = parseFloat(hm[1]);
    if (w > 0 && h > 0) return w / h;
  }
  return null;
}

/**
 * The content's OWN aspect ratio, when it has one. This is the bit the drawer ignored:
 * an L4 infographic carries explicit width/height, an L3 scene and a plain visual carry a
 * viewBox. Prose (documents, questions) has no intrinsic shape, so it returns null.
 */
export function intrinsicAspect(node: CanvasNode): number | null {
  const snap = node.snapshot as { type?: string; payload?: unknown } | undefined;
  if (!snap) return null;

  if (snap.type === 'svg' && typeof snap.payload === 'string') return svgAspect(snap.payload);

  if (snap.type === 'json:infographic' && snap.payload && typeof snap.payload === 'object') {
    const p = snap.payload as { width?: number; height?: number; scene_svg?: string };
    if (typeof p.width === 'number' && typeof p.height === 'number' && p.width > 0 && p.height > 0) {
      return p.width / p.height;
    }
    if (typeof p.scene_svg === 'string') return svgAspect(p.scene_svg);
  }
  return null;
}

/** Clamp a size into what the viewport can actually show. */
export function fitToViewport(size: Size, vp: Viewport): Size {
  const maxW = Math.max(MIN_SIZE.w, Math.floor(vp.w * MAX_FRAC.w));
  const maxH = Math.max(MIN_SIZE.h, Math.floor(vp.h * MAX_FRAC.h));
  return {
    w: Math.round(Math.min(Math.max(size.w, MIN_SIZE.w), maxW)),
    h: Math.round(Math.min(Math.max(size.h, MIN_SIZE.h), maxH)),
  };
}

const CHROME_H = 34;   // header strip
const PAD = 16;        // body padding

/**
 * Starting size for a thread's window: the per-type default, reshaped to the content's own
 * aspect ratio when it has one, then fitted to the viewport.
 */
export function initialSize(node: CanvasNode, vp: Viewport): Size {
  const base = DEFAULTS[node.ref_type] ?? FALLBACK;
  const aspect = intrinsicAspect(node);
  if (!aspect) return fitToViewport(base, vp);

  // Keep the type's width and derive the height from the real shape, so a wide banner opens
  // short and a tall poster opens tall instead of both landing in the same box.
  const contentW = base.w - PAD;
  const h = Math.round(contentW / aspect) + CHROME_H + PAD;
  const fitted = fitToViewport({ w: base.w, h }, vp);

  // If the height got clamped, pull the width back in so the aspect ratio survives.
  if (fitted.h < h) {
    const w = Math.round((fitted.h - CHROME_H - PAD) * aspect) + PAD;
    return fitToViewport({ w, h: fitted.h }, vp);
  }
  return fitted;
}

/**
 * Where to open: just below-right of the click so the chip stays visible, nudged fully on
 * screen. With no anchor (keyboard), dock bottom-right.
 */
export function initialPosition(anchor: Point | null | undefined, size: Size, vp: Viewport): Point {
  const raw = anchor ? { x: anchor.x + 16, y: anchor.y + 16 } : { x: vp.w, y: vp.h };
  return clampPosition(raw, size, vp);
}

export function clampPosition(p: Point, size: Size, vp: Viewport): Point {
  const maxX = Math.max(MARGIN, vp.w - size.w - MARGIN);
  const maxY = Math.max(MARGIN, vp.h - size.h - MARGIN);
  return {
    x: Math.round(Math.min(Math.max(MARGIN, p.x), maxX)),
    y: Math.round(Math.min(Math.max(MARGIN, p.y), maxY)),
  };
}

/**
 * Cascade a new window so it doesn't land exactly on one already open — otherwise opening a
 * second thread looks like nothing happened.
 */
export function avoidOverlap(p: Point, taken: Point[], size: Size, vp: Viewport): Point {
  let out = p;
  for (let i = 0; i < taken.length + 1; i++) {
    if (!taken.some((t) => Math.abs(t.x - out.x) < 24 && Math.abs(t.y - out.y) < 24)) return out;
    out = clampPosition({ x: out.x + 28, y: out.y + 28 }, size, vp);
  }
  return out;
}
