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
  /** User-chosen position (persisted in metadata). Takes precedence. */
  initialPosition?: OverlayPosition;
  /** Backend-suggested position from image analysis — used as the default
   *  when the user hasn't yet picked one. Falls back to bottom-left. */
  suggestedPosition?: OverlayPosition;
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

/**
 * Bake the overlay (title + subtitle + scrim) into the base SVG as
 * native SVG elements so it survives standalone export.
 *
 * The frontend renders the overlay as DOM/CSS for live editing — but
 * for export (`.svg` download, embedding elsewhere) we need everything
 * in one self-contained SVG. This walks the base SVG, computes the same
 * positions used by POSITION_STYLES (in viewBox coordinates), and
 * appends a backdrop + text elements before `</svg>`.
 */
function buildExportSvg(
  baseSvg: string,
  title: string,
  subtitle: string,
  position: OverlayPosition,
  enabled: boolean,
): string {
  if (!enabled || (!title && !subtitle)) return baseSvg;

  // Extract viewBox dims; bail if non-standard (caller falls back to raw svg)
  const vbMatch = baseSvg.match(/viewBox="0 0 (\d+(?:\.\d+)?) (\d+(?:\.\d+)?)"/);
  if (!vbMatch) return baseSvg;
  const w = parseFloat(vbMatch[1]);
  const h = parseFloat(vbMatch[2]);

  const padX = Math.max(40, Math.round(w * 0.05));
  const padY = Math.max(40, Math.round(h * 0.08));
  const titleSize = Math.max(36, Math.round(h * 0.072));
  const subtitleSize = Math.max(18, Math.round(h * 0.028));
  const blockMaxW = position === 'center' ? Math.round(w * 0.7) : Math.round(w * 0.55);
  // Block height: padding(24) + title + gap(12) + subtitle + padding(20)
  const blockH = 24 + titleSize + (subtitle ? 12 + subtitleSize : 0) + 20;

  let blockX = padX;
  let blockY = padY;
  switch (position) {
    case 'top-left':     blockX = padX;                       blockY = padY; break;
    case 'top-right':    blockX = w - padX - blockMaxW;       blockY = padY; break;
    case 'bottom-left':  blockX = padX;                       blockY = h - padY - blockH; break;
    case 'bottom-right': blockX = w - padX - blockMaxW;       blockY = h - padY - blockH; break;
    case 'center':       blockX = (w - blockMaxW) / 2;        blockY = (h - blockH) / 2; break;
  }

  const escape = (s: string) =>
    s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

  const textX = blockX + 24;
  const titleBaseline = blockY + 24 + titleSize * 0.82;
  const subtitleBaseline = titleBaseline + Math.round(titleSize * 0.55) + subtitleSize * 0.2;

  const defs =
    `<defs><filter id="exTextShadow" x="-10%" y="-10%" width="120%" height="120%">` +
    `<feDropShadow dx="0" dy="2" stdDeviation="3" flood-opacity="0.55"/>` +
    `</filter></defs>`;

  const backdrop =
    `<rect x="${blockX}" y="${blockY}" width="${blockMaxW}" height="${blockH}" ` +
    `rx="12" fill="rgb(0,0,0)" fill-opacity="0.45"/>`;

  const titleEl = title
    ? `<text x="${textX}" y="${titleBaseline}" ` +
      `font-family="Georgia, 'Iowan Old Style', 'Palatino Linotype', serif" ` +
      `font-size="${titleSize}" font-weight="600" fill="rgb(255,255,255)" ` +
      `filter="url(#exTextShadow)" letter-spacing="-0.01em">` +
      `${escape(title)}</text>`
    : '';

  const subtitleEl = subtitle
    ? `<text x="${textX}" y="${subtitleBaseline}" ` +
      `font-family="Inter, 'Helvetica Neue', system-ui, sans-serif" ` +
      `font-size="${subtitleSize}" font-weight="400" fill="rgb(245,245,245)" ` +
      `fill-opacity="0.92" filter="url(#exTextShadow)">` +
      `${escape(subtitle)}</text>`
    : '';

  return baseSvg.replace('</svg>', defs + backdrop + titleEl + subtitleEl + '</svg>');
}

function downloadSvg(svg: string, suggestedTitle: string) {
  const blob = new Blob([svg], { type: 'image/svg+xml;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = sanitizeFilename(suggestedTitle) + '.svg';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/**
 * Rasterize an SVG to PNG entirely in the browser and trigger a download.
 *
 * Implementation: load the SVG string as an Image via a blob URL, draw
 * onto a canvas at the SVG's native dimensions, encode as PNG. Works
 * because the SVG's embedded base64 PNG is a data URI (no taint), so
 * canvas.toBlob succeeds without CORS issues.
 *
 * No backend round-trip — keeps the export instant and offline-safe.
 */
async function downloadPng(svg: string, suggestedTitle: string): Promise<void> {
  // Pull native pixel dimensions from the viewBox
  const match = svg.match(/viewBox="0 0 (\d+(?:\.\d+)?) (\d+(?:\.\d+)?)"/);
  if (!match) {
    throw new Error('Cannot determine SVG dimensions for PNG export');
  }
  const w = Math.round(parseFloat(match[1]));
  const h = Math.round(parseFloat(match[2]));

  const svgBlob = new Blob([svg], { type: 'image/svg+xml;charset=utf-8' });
  const svgUrl = URL.createObjectURL(svgBlob);

  try {
    // Load the SVG as an Image element
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image();
      el.onload = () => resolve(el);
      el.onerror = () => reject(new Error('SVG failed to render as Image'));
      el.src = svgUrl;
    });

    const canvas = document.createElement('canvas');
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('Canvas 2D context unavailable');

    // White background — PNG is opaque by default; SVGs with transparency
    // could otherwise show ugly black backgrounds in some viewers.
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, w, h);
    ctx.drawImage(img, 0, 0, w, h);

    await new Promise<void>((resolve, reject) => {
      canvas.toBlob((pngBlob) => {
        if (!pngBlob) {
          reject(new Error('PNG encoding failed'));
          return;
        }
        const pngUrl = URL.createObjectURL(pngBlob);
        const a = document.createElement('a');
        a.href = pngUrl;
        a.download = sanitizeFilename(suggestedTitle) + '.png';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        setTimeout(() => URL.revokeObjectURL(pngUrl), 1000);
        resolve();
      }, 'image/png');
    });
  } finally {
    URL.revokeObjectURL(svgUrl);
  }
}

function sanitizeFilename(s: string): string {
  const cleaned = (s || 'visual').toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  return cleaned || 'visual';
}

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
  suggestedPosition,
  initialTitle,
  initialSubtitle,
}) => {
  const { updateCanvasItem } = useCanvasItems();

  const [enabled, setEnabled] = React.useState<boolean>(initialEnabled ?? false);
  // Priority: explicit user choice > backend smart suggestion > safe default
  const [position, setPosition] = React.useState<OverlayPosition>(
    initialPosition ?? suggestedPosition ?? 'bottom-left',
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
            <button
              type="button"
              onClick={() => {
                const exported = buildExportSvg(svg, title, subtitle, position, true);
                downloadSvg(exported, title || 'visual');
              }}
              className="text-gray-500 hover:text-indigo-600 dark:text-gray-400 dark:hover:text-indigo-400 underline-offset-2 hover:underline"
              title="Download as SVG with the title overlay baked in at the chosen position"
            >
              SVG ↓
            </button>
            <button
              type="button"
              onClick={async () => {
                try {
                  const exported = buildExportSvg(svg, title, subtitle, position, true);
                  await downloadPng(exported, title || 'visual');
                } catch (err) {
                  console.error('[png export]', err);
                  // Best-effort fallback: drop the SVG instead so the user
                  // still gets something rather than a silent failure.
                  const exported = buildExportSvg(svg, title, subtitle, position, true);
                  downloadSvg(exported, title || 'visual');
                }
              }}
              className="text-gray-500 hover:text-indigo-600 dark:text-gray-400 dark:hover:text-indigo-400 underline-offset-2 hover:underline"
              title="Download as PNG with the title overlay baked in (rasterized in the browser)"
            >
              PNG ↓
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
