import { describe, it, expect } from 'vitest';

import {
  svgAspect,
  intrinsicAspect,
  fitToViewport,
  initialSize,
  initialPosition,
  clampPosition,
  avoidOverlap,
  MAX_FRAC,
  MIN_SIZE,
} from './journeyWindowSizing';
import type { CanvasNode } from '../../services/canvas';

const VP = { w: 1600, h: 1000 };

function node(ref_type: string, snapshot: unknown): CanvasNode {
  return { id: 'n', x: 0, y: 0, kind: 'artifact', ref_type, ref_id: 'r', title: 't', snapshot } as CanvasNode;
}

describe('svgAspect', () => {
  it('reads a viewBox', () => {
    expect(svgAspect('<svg viewBox="0 0 800 400"></svg>')).toBe(2);
    expect(svgAspect('<svg viewBox="0,0,400,800"></svg>')).toBe(0.5);
  });

  it('falls back to width/height attributes', () => {
    expect(svgAspect('<svg width="300" height="150">')).toBe(2);
  });

  it('returns null when there is nothing to read', () => {
    expect(svgAspect('<svg></svg>')).toBeNull();
    expect(svgAspect('')).toBeNull();
    // a zero dimension must not produce Infinity/NaN
    expect(svgAspect('<svg viewBox="0 0 100 0"></svg>')).toBeNull();
  });
});

describe('intrinsicAspect — the shape the drawer ignored', () => {
  it('uses an L4 infographic\'s explicit width/height', () => {
    expect(intrinsicAspect(node('infographic', {
      type: 'json:infographic', payload: { lane: 'L4', width: 1024, height: 512 },
    }))).toBe(2);
  });

  it('falls back to an L3 scene\'s viewBox', () => {
    expect(intrinsicAspect(node('infographic', {
      type: 'json:infographic', payload: { lane: 'L3', scene_svg: '<svg viewBox="0 0 600 300"/>' },
    }))).toBe(2);
  });

  it('reads a plain svg visual', () => {
    expect(intrinsicAspect(node('visual', { type: 'svg', payload: '<svg viewBox="0 0 900 300"/>' }))).toBe(3);
  });

  it('returns null for prose — a document has no intrinsic shape', () => {
    expect(intrinsicAspect(node('document', { type: 'markdown', payload: '# hi' }))).toBeNull();
    expect(intrinsicAspect(node('exploration_query', undefined))).toBeNull();
  });
});

describe('initialSize', () => {
  it('gives an infographic real room — the L2 design system lays out up to 5 columns', () => {
    const s = initialSize(node('infographic', { type: 'json:infographic', payload: {} }), VP);
    // Comfortably wider than the 560px drawer that squashed them.
    expect(s.w).toBeGreaterThanOrEqual(800);
  });

  it('keeps a podcast small — it must not become a second drawer', () => {
    const s = initialSize(node('audio', undefined), VP);
    expect(s.w).toBeLessThanOrEqual(360);
    expect(s.h).toBeLessThanOrEqual(200);
  });

  it('opens a WIDE graphic short and a TALL graphic tall', () => {
    const wide = initialSize(node('visual', { type: 'svg', payload: '<svg viewBox="0 0 1000 250"/>' }), VP);
    const tall = initialSize(node('visual', { type: 'svg', payload: '<svg viewBox="0 0 250 1000"/>' }), VP);
    expect(wide.h).toBeLessThan(tall.h);
    // and the wide one is genuinely landscape
    expect(wide.w).toBeGreaterThan(wide.h);
  });

  it('never exceeds the viewport fraction, however extreme the content', () => {
    const s = initialSize(node('visual', { type: 'svg', payload: '<svg viewBox="0 0 100 100000"/>' }), VP);
    expect(s.w).toBeLessThanOrEqual(Math.floor(VP.w * MAX_FRAC.w));
    expect(s.h).toBeLessThanOrEqual(Math.floor(VP.h * MAX_FRAC.h));
  });

  it('stays usable on a small viewport', () => {
    const s = initialSize(node('infographic', { type: 'json:infographic', payload: {} }), { w: 800, h: 600 });
    expect(s.w).toBeGreaterThanOrEqual(MIN_SIZE.w);
    expect(s.w).toBeLessThanOrEqual(800);
    expect(s.h).toBeLessThanOrEqual(600);
  });
});

describe('fitToViewport', () => {
  it('enforces both the floor and the ceiling', () => {
    expect(fitToViewport({ w: 10, h: 10 }, VP)).toEqual(MIN_SIZE);
    const big = fitToViewport({ w: 99999, h: 99999 }, VP);
    expect(big.w).toBe(Math.floor(VP.w * MAX_FRAC.w));
    expect(big.h).toBe(Math.floor(VP.h * MAX_FRAC.h));
  });
});

describe('positioning', () => {
  it('opens below-right of the click so the chip you clicked stays visible', () => {
    const p = initialPosition({ x: 400, y: 300 }, { w: 320, h: 150 }, VP);
    expect(p).toEqual({ x: 416, y: 316 });
  });

  it('pulls a click near the edge fully back on screen', () => {
    const p = initialPosition({ x: 1590, y: 990 }, { w: 320, h: 150 }, VP);
    expect(p.x + 320).toBeLessThanOrEqual(VP.w);
    expect(p.y + 150).toBeLessThanOrEqual(VP.h);
  });

  it('docks bottom-right when there is no anchor', () => {
    const p = initialPosition(null, { w: 320, h: 150 }, VP);
    expect(p).toEqual({ x: VP.w - 320 - 12, y: VP.h - 150 - 12 });
  });

  it('never returns a negative position, even if the window is bigger than the viewport', () => {
    const p = clampPosition({ x: -500, y: -500 }, { w: 2000, h: 2000 }, VP);
    expect(p.x).toBeGreaterThanOrEqual(0);
    expect(p.y).toBeGreaterThanOrEqual(0);
  });
});

describe('avoidOverlap — a second window must not hide under the first', () => {
  it('cascades off an occupied spot', () => {
    const size = { w: 320, h: 150 };
    const p = avoidOverlap({ x: 400, y: 300 }, [{ x: 400, y: 300 }], size, VP);
    expect(p).not.toEqual({ x: 400, y: 300 });
  });

  it('leaves a free spot alone', () => {
    const size = { w: 320, h: 150 };
    expect(avoidOverlap({ x: 400, y: 300 }, [{ x: 900, y: 700 }], size, VP)).toEqual({ x: 400, y: 300 });
  });

  it('terminates even when every cascade step is also taken', () => {
    const size = { w: 320, h: 150 };
    const taken = Array.from({ length: 8 }, (_, i) => ({ x: 400 + i * 28, y: 300 + i * 28 }));
    expect(() => avoidOverlap({ x: 400, y: 300 }, taken, size, VP)).not.toThrow();
  });
});
