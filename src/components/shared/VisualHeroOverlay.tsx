/**
 * VisualHeroOverlay — image-first renderer with a USER-CONTROLLED title
 * overlay for Klein full-bleed visuals.
 *
 * Klein composes the image without any awareness of where text will land,
 * so a baked-in overlay routinely obscures the most important part of the
 * scene. This component renders Klein's image clean and lets the user:
 *   • toggle the overlay on/off (default OFF)
 *   • pick one of 5 positions (4 corners + center)
 *   • edit the title and subtitle text
 *
 * State persists to the canvas item's metadata so user preferences
 * survive reload and idiom-swap.
 */
import React from 'react';
import { SVGRenderer } from './SVGRenderer';
import { useCanvasItems } from '../canvas/CanvasContext';

export type OverlayPosition =
  | 'top-left'
  | 'top-right'
  | 'bottom-left'
  | 'bottom-right'
  | 'center';

interface VisualHeroOverlayProps {
  itemId: string;
  svg: string;
  defaultTitle?: string;
  defaultSubtitle?: string;
  initialEnabled?: boolean;
  initialPosition?: OverlayPosition;
  initialTitle?: string;
  initialSubtitle?: string;
}

const POSITION_STYLES: Record<OverlayPosition, React.CSSProperties> = {
  'top-left':     { top: '6%',  left: '5%',  textAlign: 'left' },
  'top-right':    { top: '6%',  right: '5%', textAlign: 'right' },
  'bottom-left':  { bottom: '8%', left: '5%',  textAlign: 'left' },
  'bottom-right': { bottom: '8%', right: '5%', textAlign: 'right' },
  'center':       { top: '50%', left: '50%', transform: 'translate(-50%, -50%)', textAlign: 'center' },
};

const POSITION_GLYPHS: Record<OverlayPosition, string> = {
  'top-left': '◤',
  'top-right': '◥',
  'center': '●',
  'bottom-left': '◣',
  'bottom-right': '◢',
};

const POSITION_ORDER: OverlayPosition[] = [
  'top-left', 'top-right', 'center', 'bottom-left', 'bottom-right',
];

export const VisualHeroOverlay: React.FC<VisualHeroOverlayProps> = ({
  itemId,
  svg,
  defaultTitle = '',
  defaultSubtitle = '',
  initialEnabled,
  initialPosition,
  initialTitle,
  initialSubtitle,
}) => {
  const { updateCanvasItem } = useCanvasItems();

  const [enabled, setEnabled] = React.useState<boolean>(initialEnabled ?? false);
  const [position, setPosition] = React.useState<OverlayPosition>(
    initialPosition ?? 'bottom-left',
  );
  const [title, setTitle] = React.useState<string>(initialTitle ?? defaultTitle);
  const [subtitle, setSubtitle] = React.useState<string>(initialSubtitle ?? defaultSubtitle);
  const [editing, setEditing] = React.useState<boolean>(false);

  // Persist user choices to canvas metadata so they survive reload.
  // Skip the very first render (state matches initial props already).
  const isFirst = React.useRef(true);
  React.useEffect(() => {
    if (isFirst.current) { isFirst.current = false; return; }
    updateCanvasItem(itemId, {
      metadata: {
        overlayEnabled: enabled,
        overlayPosition: position,
        overlayTitle: title,
        overlaySubtitle: subtitle,
      } as any,
    });
  }, [enabled, position, title, subtitle, itemId, updateCanvasItem]);

  const overlayStyle: React.CSSProperties = {
    position: 'absolute',
    maxWidth: position === 'center' ? '70%' : '55%',
    pointerEvents: 'none',
    ...POSITION_STYLES[position],
  };

  return (
    <div className="space-y-2">
      {/* Image + overlay */}
      <div className="relative">
        <SVGRenderer svg={svg} />
        {enabled && (title || subtitle) && (
          <div style={overlayStyle}>
            <div className="inline-block bg-black/45 backdrop-blur-[2px] px-4 py-3 rounded-lg">
              {title && (
                <div
                  className="text-white text-2xl font-semibold leading-tight"
                  style={{
                    fontFamily: "Georgia, 'Iowan Old Style', 'Palatino Linotype', serif",
                    textShadow: '0 2px 4px rgba(0,0,0,0.55)',
                    letterSpacing: '-0.01em',
                  }}
                >
                  {title}
                </div>
              )}
              {subtitle && (
                <div
                  className="text-white/90 text-sm mt-1"
                  style={{
                    fontFamily: "Inter, 'Helvetica Neue', system-ui, sans-serif",
                    textShadow: '0 1px 3px rgba(0,0,0,0.5)',
                  }}
                >
                  {subtitle}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Controls — title overlay toggle, position, text edit */}
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <button
          type="button"
          onClick={() => setEnabled((v) => !v)}
          className={`px-2 py-1 rounded border transition-colors ${
            enabled
              ? 'bg-indigo-50 border-indigo-300 text-indigo-700 dark:bg-indigo-900/30 dark:border-indigo-600 dark:text-indigo-300'
              : 'bg-gray-50 border-gray-200 text-gray-600 hover:bg-gray-100 dark:bg-gray-800 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-700'
          }`}
        >
          {enabled ? '✓ Title overlay' : 'Add title overlay'}
        </button>

        {enabled && (
          <>
            <div
              className="inline-flex gap-px border border-gray-200 dark:border-gray-600 rounded overflow-hidden"
              role="group"
              aria-label="Overlay position"
            >
              {POSITION_ORDER.map((p) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setPosition(p)}
                  title={p.replace('-', ' ')}
                  className={`px-1.5 py-1 text-base leading-none ${
                    position === p
                      ? 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/40 dark:text-indigo-300'
                      : 'text-gray-400 hover:bg-gray-100 dark:text-gray-500 dark:hover:bg-gray-700'
                  }`}
                >
                  {POSITION_GLYPHS[p]}
                </button>
              ))}
            </div>

            <button
              type="button"
              onClick={() => setEditing((v) => !v)}
              className="text-gray-500 hover:text-indigo-600 dark:text-gray-400 dark:hover:text-indigo-400 underline-offset-2 hover:underline"
            >
              {editing ? 'Done editing' : 'Edit text'}
            </button>
          </>
        )}
      </div>

      {/* Editable text inputs (only when toggled into edit mode) */}
      {enabled && editing && (
        <div className="space-y-1.5 p-2 border border-gray-200 dark:border-gray-700 rounded bg-gray-50 dark:bg-gray-800/50">
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Title"
            className="w-full text-sm px-2 py-1 border border-gray-200 dark:border-gray-600 rounded bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-100"
          />
          <input
            type="text"
            value={subtitle}
            onChange={(e) => setSubtitle(e.target.value)}
            placeholder="Subtitle"
            className="w-full text-xs px-2 py-1 border border-gray-200 dark:border-gray-600 rounded bg-white dark:bg-gray-900 text-gray-700 dark:text-gray-200"
          />
        </div>
      )}
    </div>
  );
};
