/**
 * FloatingMediaPlayer — a small, draggable player that floats OVER the canvas.
 *
 * Field feedback (2026-08-18): opening a podcast in the full-height focus drawer took a whole
 * side of the screen, when the point of playing it from the map is to keep exploring while it
 * runs. Media therefore gets its own compact surface instead of the drawer.
 *
 * Two things make this work that a player mounted inside a node cannot:
 *   1. It renders OUTSIDE `<ReactFlow>`, so panning, zooming, collapsing a topic card, or a
 *      Populate that replaces every derived node can never unmount it mid-sentence.
 *   2. It is positioned in SCREEN space, not flow space, so it doesn't drift or scale away
 *      while you move around the map.
 *
 * Opens near wherever the user was working, then clamps itself into view; drag the header to
 * move it. Position is view state — never persisted.
 */
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { X, GripHorizontal } from 'lucide-react';

import { AudioCanvasPlayer } from '../chat/AudioCanvasPlayer';
import { videoService } from '../../services/video';
import type { CanvasNode } from '../../services/canvas';

const W = 320;               // compact by design — it must not become a second drawer
const MARGIN = 12;
const EST_H = { audio: 132, video: 260 };

export interface MediaTarget {
  node: CanvasNode;
  /** Where the user clicked, in screen coords — the player opens next to it. */
  anchor?: { x: number; y: number } | null;
}

interface Props {
  target: MediaTarget;
  notebookId: string;
  onClose: () => void;
}

/** Keep the whole player on screen no matter where the click was. */
function clamp(x: number, y: number, h: number): { x: number; y: number } {
  const maxX = Math.max(MARGIN, window.innerWidth - W - MARGIN);
  const maxY = Math.max(MARGIN, window.innerHeight - h - MARGIN);
  return {
    x: Math.min(Math.max(MARGIN, x), maxX),
    y: Math.min(Math.max(MARGIN, y), maxY),
  };
}

export const FloatingMediaPlayer: React.FC<Props> = ({ target, notebookId, onClose }) => {
  const { node, anchor } = target;
  const isVideo = node.ref_type === 'video';
  const estH = isVideo ? EST_H.video : EST_H.audio;

  const [pos, setPos] = useState(() =>
    anchor
      // Slightly below-right of the click so the chip you clicked stays visible.
      ? clamp(anchor.x + 16, anchor.y + 16, estH)
      // No anchor (keyboard, or a click we couldn't locate) → bottom-right dock.
      : clamp(window.innerWidth, window.innerHeight, estH),
  );

  // Re-open on a different item → reposition to that item.
  useEffect(() => {
    setPos(anchor ? clamp(anchor.x + 16, anchor.y + 16, estH) : clamp(window.innerWidth, window.innerHeight, estH));
  }, [node.id, anchor, estH]);

  // ── Drag by the header ──
  const dragRef = useRef<{ dx: number; dy: number } | null>(null);
  const onPointerDown = useCallback((e: React.PointerEvent) => {
    dragRef.current = { dx: e.clientX - pos.x, dy: e.clientY - pos.y };
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  }, [pos]);
  const onPointerMove = useCallback((e: React.PointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    setPos(clamp(e.clientX - d.dx, e.clientY - d.dy, estH));
  }, [estH]);
  const onPointerUp = useCallback(() => { dragRef.current = null; }, []);

  // A window resize can strand the player off-screen.
  useEffect(() => {
    const onResize = () => setPos((p) => clamp(p.x, p.y, estH));
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [estH]);

  return (
    <div
      // `fixed`, not absolute: screen space, immune to the canvas transform.
      className="fixed z-40 overflow-hidden rounded-xl border border-gray-200 bg-white shadow-2xl dark:border-gray-700 dark:bg-gray-800"
      style={{ left: pos.x, top: pos.y, width: W }}
      role="dialog"
      aria-label={isVideo ? 'Video player' : 'Podcast player'}
    >
      <div
        className="flex cursor-grab items-center gap-1.5 border-b border-gray-100 bg-gray-50/80 px-2 py-1.5 active:cursor-grabbing dark:border-gray-700 dark:bg-gray-900/50"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
      >
        <GripHorizontal className="h-3 w-3 flex-shrink-0 text-gray-400" />
        <span
          className="min-w-0 flex-1 truncate text-[11px] font-semibold text-gray-700 dark:text-gray-200"
          title={node.title}
        >
          {node.title || (isVideo ? 'Video' : 'Podcast')}
        </span>
        <button
          type="button"
          onClick={onClose}
          className="flex-shrink-0 rounded p-0.5 text-gray-400 hover:bg-gray-200 hover:text-gray-600 dark:hover:bg-gray-700"
          title="Close player"
          aria-label="Close player"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <div className="p-2">
        {isVideo ? (
          <video
            className="w-full rounded-lg bg-black"
            controls
            autoPlay
            preload="metadata"
            src={videoService.getStreamUrl(node.ref_id)}
          >
            <track kind="captions" />
          </video>
        ) : (
          // Reused unchanged — it owns its own status polling and playback.
          <AudioCanvasPlayer
            audioId={node.ref_id}
            notebookId={notebookId}
            title={node.title || 'Podcast'}
          />
        )}
      </div>
    </div>
  );
};
