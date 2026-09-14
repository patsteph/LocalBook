/**
 * FloatingWindow — a small, draggable, resizable window that floats OVER the canvas.
 *
 * Replaces the full-height focus drawer (field feedback 2026-08-18: "it takes up one whole side
 * of the screen ... all things should be in floating windows"). The drawer had a second, related
 * problem: a fixed width cannot respect content that has its own natural size, so infographics
 * came out squashed. Windows size to what they hold (`journeyWindowSizing`) and can be resized.
 *
 * Renders OUTSIDE `<ReactFlow>` and positions in SCREEN space (`fixed`), so panning, zooming,
 * collapsing a card, or a Populate that rebuilds every derived node can never disturb it — that
 * is what lets a podcast keep playing while you carry on exploring.
 *
 * Geometry only. It knows nothing about threads, artifacts or media.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { X, GripHorizontal } from 'lucide-react';

import {
  clampPosition,
  fitToViewport,
  MIN_SIZE,
  type Point,
  type Size,
} from './journeyWindowSizing';

interface Props {
  title: string;
  /** Small muted line above the title (the thread's type). */
  eyebrow?: string;
  initialPosition: Point;
  initialSize: Size;
  /** Higher = in front. The canvas raises the window you last touched. */
  z: number;
  onFocus: () => void;
  onClose: () => void;
  children: React.ReactNode;
  /** Suppresses the body's padding + scroll for content that manages its own box (media). */
  bare?: boolean;
  /** Only the front-most window answers Esc — otherwise one keypress closes them all. */
  isTop?: boolean;
}

const viewport = () => ({ w: window.innerWidth, h: window.innerHeight });

export const FloatingWindow: React.FC<Props> = ({
  title, eyebrow, initialPosition, initialSize, z, onFocus, onClose, children, bare, isTop,
}) => {
  const [pos, setPos] = useState<Point>(initialPosition);
  const [size, setSize] = useState<Size>(initialSize);

  // drag = moving the window; resize = the bottom-right grip. Refs, not state: these fire on
  // every pointermove and must not queue a render each time.
  const dragRef = useRef<{ dx: number; dy: number } | null>(null);
  const resizeRef = useRef<{ x: number; y: number; w: number; h: number } | null>(null);

  const onHeaderDown = useCallback((e: React.PointerEvent) => {
    onFocus();
    // 🐛 Do NOT start a drag from a control inside the header. Reported from testing: the popup
    // "wouldn't close out when I hit the x". Cause: pointerdown bubbles from the button up to
    // this handler, which then called `setPointerCapture`. Capturing the pointer retargets the
    // following pointerup, so the browser never synthesises a `click` on the button and its
    // onClick never runs. The button looked dead while the window dragged perfectly.
    if ((e.target as HTMLElement).closest('button')) return;
    dragRef.current = { dx: e.clientX - pos.x, dy: e.clientY - pos.y };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  }, [pos, onFocus]);

  const onHeaderMove = useCallback((e: React.PointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    setPos(clampPosition({ x: e.clientX - d.dx, y: e.clientY - d.dy }, size, viewport()));
  }, [size]);

  const onGripDown = useCallback((e: React.PointerEvent) => {
    e.stopPropagation();
    onFocus();
    resizeRef.current = { x: e.clientX, y: e.clientY, w: size.w, h: size.h };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
  }, [size, onFocus]);

  const onGripMove = useCallback((e: React.PointerEvent) => {
    const r = resizeRef.current;
    if (!r) return;
    setSize(fitToViewport(
      { w: r.w + (e.clientX - r.x), h: r.h + (e.clientY - r.y) },
      viewport(),
    ));
  }, []);

  const endGesture = useCallback(() => {
    dragRef.current = null;
    resizeRef.current = null;
  }, []);

  // A window resize can strand a floating window off-screen or oversize it.
  useEffect(() => {
    const onResize = () => {
      const vp = viewport();
      setSize((s) => {
        const fitted = fitToViewport(s, vp);
        setPos((p) => clampPosition(p, fitted, vp));
        return fitted;
      });
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);

  // Esc closes the FRONT-most window only. Every window mounting this listener would mean one
  // keypress wiping out the podcast you left playing behind the document you were reading.
  useEffect(() => {
    if (!isTop) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose, isTop]);

  return (
    <div
      className="fixed flex flex-col overflow-hidden rounded-xl border border-gray-200 bg-white shadow-2xl dark:border-gray-700 dark:bg-gray-800"
      style={{ left: pos.x, top: pos.y, width: size.w, height: size.h, zIndex: z }}
      onPointerDownCapture={onFocus}
      role="dialog"
      aria-label={title}
    >
      <div
        className="flex flex-shrink-0 cursor-grab items-center gap-1.5 border-b border-gray-100 bg-gray-50/80 px-2 py-1.5 active:cursor-grabbing dark:border-gray-700 dark:bg-gray-900/50"
        onPointerDown={onHeaderDown}
        onPointerMove={onHeaderMove}
        onPointerUp={endGesture}
        onPointerCancel={endGesture}
      >
        <GripHorizontal className="h-3 w-3 flex-shrink-0 text-gray-400" />
        <div className="min-w-0 flex-1">
          {eyebrow && (
            <p className="truncate text-[9px] font-medium uppercase leading-none tracking-wide text-gray-400">
              {eyebrow}
            </p>
          )}
          <p className="truncate text-[11px] font-semibold text-gray-700 dark:text-gray-200" title={title}>
            {title}
          </p>
        </div>
        <button
          type="button"
          onPointerDown={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); onClose(); }}
          className="flex-shrink-0 rounded p-0.5 text-gray-400 hover:bg-gray-200 hover:text-gray-600 dark:hover:bg-gray-700"
          title="Close (Esc)"
          aria-label="Close"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className={bare ? 'min-h-0 flex-1' : 'min-h-0 flex-1 overflow-auto p-2.5'}>
        {children}
      </div>

      {/* Resize grip — no table of default sizes can know how tall a given document is. */}
      <div
        className="absolute bottom-0 right-0 h-4 w-4 cursor-nwse-resize"
        onPointerDown={onGripDown}
        onPointerMove={onGripMove}
        onPointerUp={endGesture}
        onPointerCancel={endGesture}
        title="Resize"
        style={{
          background:
            'linear-gradient(135deg, transparent 0 50%, rgba(156,163,175,.75) 50% 62%, transparent 62% 74%, rgba(156,163,175,.75) 74% 86%, transparent 86%)',
        }}
      />
    </div>
  );
};

export { MIN_SIZE };
