/**
 * InfographicArtifactRenderer — renders `json:infographic` artifacts for the
 * two shipped lanes:
 *
 *   L2 (structured diagram): the stored `body_html` is design-system markup.
 *     It is sanitized (DOMPurify, html+svg profile, no scripts/styles) and
 *     injected into a Shadow DOM together with the ONE hand-authored
 *     stylesheet (`INFOGRAPHIC_L2_CSS`). Because the artifact stores only the
 *     body — never a copy of the CSS — editing the stylesheet restyles every
 *     saved infographic (HARD RULE §2.4, retroactive).
 *
 *   L1 (annotated chart): the recharts `ChartConfig` is rendered by the shared
 *     `ChartRenderer`, wrapped in a CSS annotation layer (corner brackets,
 *     coral callout with glow, inline series labels, midpoint note). Anchors
 *     are computed here, never emitted by the model (HARD RULE §2.1).
 *
 * Fails open: an unknown/empty payload renders a legible placeholder, never
 * throws (HARD RULE §2.5).
 */
import { useEffect, useRef } from 'react';
import DOMPurify from 'dompurify';
import type { RendererProps } from '../../../types/artifact';
import { ChartRenderer, type ChartConfig } from '../../shared/ChartRenderer';
import { INFOGRAPHIC_L2_CSS } from './infographicDesignSystem';
import { buildRestyleCss, accentHex, type InfographicStyle } from './infographicRestyle';

export interface InfographicAnchor {
  x: number; // 0..1 fraction of the plot rect (left -> right)
  y: number; // 0..1 fraction of the plot rect (top -> bottom)
}

export interface InfographicAnnotations {
  callout_text?: string;
  callout_subtext?: string;
  primary_label?: string;
  baseline_label?: string;
  midpoint_note?: string;
  // DATA ANCHORS (0..1 plot-rect fractions) computed from the real series data
  // by the builder — the model never emits coordinates (HARD RULE §2.1). Absent
  // on older artifacts, in which case the fixed fallback positions are used.
  anchors?: {
    callout?: InfographicAnchor;
    primary?: InfographicAnchor;
    baseline?: InfographicAnchor;
    midpoint?: InfographicAnchor;
  };
  // Real source id bound to the callout number (Hard Rule §2.6). "" when there
  // is no provenance (a bare callout rather than a dangling superscript).
  callout_cite?: string;
}

export interface InfographicSource {
  n: number;
  source_id?: string | null;
  title: string;
}

export interface InfographicPayload {
  lane: 'L1' | 'L2' | 'L3' | 'L4';
  archetype: string;
  body_html?: string;
  // L3 (scene): the hand-drawn scene SVG is the artifact SOURCE (never a raster,
  // HARD RULE §2.4). Rendered inline as an <img> data-uri — no bundle-heavy
  // Excalidraw dependency, and the export path rasterizes the same SVG.
  scene_svg?: string;
  chart?: ChartConfig;
  annotations?: InfographicAnnotations;
  citations?: { id: number; label: string; cite?: string }[];
  sources?: InfographicSource[];
  degraded?: boolean;
  degrade_reason?: string;
  // L4 (decorative): a TEXTLESS Klein raster as a data URI. Any title rides
  // as a DOM overlay (`title_overlay`) — never baked into the pixels (§2.2).
  image?: string;
  width?: number;
  height?: number;
  title_overlay?: string;
  // Restyle overrides (accent / tone / scale / glow). Set by the tombstone;
  // rides in the payload so export mirrors the on-screen restyle.
  style?: InfographicStyle;
  // L4-accent (gated, off by default): the decorative "glow crystal" variant
  // name; for L2/L3 the spot is baked into body_html/scene_svg, for L1 the
  // inner SVG (currentColor) rides in `accent_svg` for the renderer to overlay.
  accent?: string;
  accent_svg?: string;
}

const INK = '#1b1a18';
const BRACKET = 'rgba(52,50,46,.5)';

// Sanitizer: allow the html + svg the skeletons emit; block scripts/styles.
const SANITIZE_CONFIG = {
  USE_PROFILES: { html: true, svg: true },
  FORBID_TAGS: ['script', 'style', 'iframe', 'object', 'embed', 'link', 'meta', 'base', 'form'],
  FORBID_ATTR: ['style'],
  ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|tel):|#|\/|[^a-z]|[a-z+.-]+(?:[^a-z+.\-:]|$))/i,
};

// ── L2: Shadow-DOM + injected stylesheet ──────────────────────────────
function L2Body({ html, style }: { html: string; style?: InfographicStyle }) {
  const hostRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const shadow = host.shadowRoot ?? host.attachShadow({ mode: 'open' });
    const clean = DOMPurify.sanitize(html, SANITIZE_CONFIG);
    // Base stylesheet + restyle overrides (empty for the default). The
    // overrides come AFTER so their `.ib { --ib-* }` tokens win the cascade.
    shadow.innerHTML = `<style>${INFOGRAPHIC_L2_CSS}${buildRestyleCss(style)}</style>${clean}`;
  }, [html, style]);
  return <div ref={hostRef} />;
}

// ── L1: corner brackets (inline styles; no shared CSS dependency) ──────
function Bracket({ pos }: { pos: 'tl' | 'tr' | 'bl' | 'br' }) {
  const size = 20;
  const base: React.CSSProperties = { position: 'absolute', width: size, height: size, pointerEvents: 'none' };
  const b = `2px solid ${BRACKET}`;
  const map: Record<string, React.CSSProperties> = {
    tl: { top: 8, left: 8, borderTop: b, borderLeft: b },
    tr: { top: 8, right: 8, borderTop: b, borderRight: b },
    bl: { bottom: 8, left: 8, borderBottom: b, borderLeft: b },
    br: { bottom: 8, right: 8, borderBottom: b, borderRight: b },
  };
  return <div style={{ ...base, ...map[pos] }} />;
}

// Plot-rect insets (fractions of the bordered chart box) used to map a 0..1
// DATA anchor to a CSS percentage. Approximates the recharts plotting area
// (y-axis on the left, x-axis labels at the bottom).
const L1_PLOT = { l: 0.13, r: 0.955, t: 0.10, b: 0.84 };
const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

function anchorPct(a?: InfographicAnchor): { x: number; y: number } | null {
  if (!a || typeof a.x !== 'number' || typeof a.y !== 'number') return null;
  return {
    x: (L1_PLOT.l + a.x * (L1_PLOT.r - L1_PLOT.l)) * 100,
    y: (L1_PLOT.t + a.y * (L1_PLOT.b - L1_PLOT.t)) * 100,
  };
}

function L1Chart({
  chart,
  annotations,
  height,
  accent,
  cite,
  accentSvg,
}: {
  chart: ChartConfig;
  annotations: InfographicAnnotations;
  height: number;
  accent: string;
  cite?: string;
  accentSvg?: string;
}) {
  const ann = annotations || {};
  const anchors = ann.anchors || {};

  // DATA-ANCHORED annotation positions (fall back to the historical fixed
  // percentages when an older artifact carries no anchors).
  const calloutPt = anchorPct(anchors.callout);
  const primaryPt = anchorPct(anchors.primary);
  const baselinePt = anchorPct(anchors.baseline);
  const midPt = anchorPct(anchors.midpoint);

  const calRight = calloutPt ? calloutPt.x >= 50 : true;
  const calBoxTop = calloutPt ? clamp(calloutPt.y - 4, 3, 44) : undefined;
  // Where the leader line touches the callout box (approx inner corner).
  const leaderBox = calloutPt
    ? { x: calRight ? 72 : 28, y: (calBoxTop ?? 6) + 11 }
    : null;

  return (
    <div
      style={{
        position: 'relative',
        background: '#f4f2ee',
        backgroundImage: 'radial-gradient(rgba(52,50,46,.10) 1px, transparent 1.5px)',
        backgroundSize: '22px 22px',
        borderRadius: 18,
        border: '1px solid #d8d5cd',
        padding: 18,
        overflow: 'hidden',
      }}
    >
      <Bracket pos="tl" />
      <Bracket pos="tr" />
      <Bracket pos="bl" />
      <Bracket pos="br" />
      <div style={{ filter: `drop-shadow(0 0 5px ${accent}73)` }}>
        <ChartRenderer config={chart} height={height} darkMode={false} />
      </div>

      {/* L4-accent decorative spot (gated, off by default), tucked bottom-left */}
      {accentSvg && (
        <div
          aria-hidden
          style={{ position: 'absolute', bottom: 10, left: 12, width: 96, height: 96, color: accent, opacity: 0.85, pointerEvents: 'none' }}
          dangerouslySetInnerHTML={{ __html: `<svg viewBox="0 0 100 100" style="width:100%;height:100%;overflow:visible">${accentSvg}</svg>` }}
        />
      )}

      {/* Data-anchored leader: line from the callout box to the primary series
          endpoint dot (the callout points at a REAL data point). */}
      {calloutPt && leaderBox && ann.callout_text && (
        <svg
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }}
        >
          <line x1={leaderBox.x} y1={leaderBox.y} x2={calloutPt.x} y2={calloutPt.y} stroke={accent} strokeWidth={0.4} strokeDasharray="1.4 1.4" vectorEffect="non-scaling-stroke" />
          <circle cx={calloutPt.x} cy={calloutPt.y} r={0.9} fill={accent} vectorEffect="non-scaling-stroke" />
        </svg>
      )}

      {ann.callout_text && (
        <div
          style={{
            position: 'absolute', maxWidth: 240,
            top: calBoxTop !== undefined ? `${calBoxTop}%` : 22,
            ...(calRight ? { right: '4%' } : { left: '4%' }),
            background: '#fff', border: `2px solid ${accent}`, borderRadius: 12,
            padding: '9px 13px',
            boxShadow: `0 0 0 1px ${accent}2e, 0 0 20px 1px ${accent}66`,
          }}
        >
          <b style={{ display: 'block', fontSize: 15, fontWeight: 800, color: INK, lineHeight: 1.15 }}>
            {ann.callout_text}
            {cite && <sup style={{ color: accent, fontSize: '.62em', fontWeight: 800, marginLeft: 3 }}>{cite}</sup>}
          </b>
          {ann.callout_subtext && (
            <small style={{ display: 'block', fontSize: 11, color: '#8b8880', marginTop: 2 }}>
              {ann.callout_subtext}
            </small>
          )}
        </div>
      )}
      {ann.primary_label && (
        <div
          style={{
            position: 'absolute', fontSize: 14, fontWeight: 800, color: accent, transform: 'translate(-50%,-50%)',
            left: primaryPt ? `${primaryPt.x}%` : '44%',
            top: primaryPt ? `${clamp(primaryPt.y - 6, 6, 90)}%` : '46%',
          }}
        >
          {ann.primary_label}
        </div>
      )}
      {ann.baseline_label && (
        <div
          style={{
            position: 'absolute', fontSize: 14, fontWeight: 800, color: INK, transform: 'translate(-50%,-50%)',
            left: baselinePt ? `${clamp(baselinePt.x, 8, 92)}%` : '93%',
            top: baselinePt ? `${clamp(baselinePt.y + 6, 8, 92)}%` : '85%',
          }}
        >
          {ann.baseline_label}
        </div>
      )}
      {ann.midpoint_note && (
        <div
          style={{
            position: 'absolute', maxWidth: 160, fontSize: 12.5, fontWeight: 600, color: '#4a4844',
            left: midPt ? `${clamp(midPt.x, 4, 70)}%` : '20%',
            top: midPt ? `${clamp(midPt.y, 10, 88)}%` : '76%',
          }}
        >
          {ann.midpoint_note}
        </div>
      )}
    </div>
  );
}

// ── L4: decorative Klein raster + DOM title overlay ────────────────────
// The image is textless by design (§2.2); the title is a DOM layer on top,
// never baked into the pixels. Corner brackets keep the design-system chrome.
function L4Art({
  image,
  title,
  accent,
}: {
  image: string;
  title?: string;
  accent: string;
}) {
  return (
    <div
      style={{
        position: 'relative',
        background: '#f4f2ee',
        borderRadius: 18,
        border: '1px solid #d8d5cd',
        padding: 8,
        overflow: 'hidden',
      }}
    >
      <div style={{ position: 'relative', borderRadius: 12, overflow: 'hidden', filter: `drop-shadow(0 0 6px ${accent}59)` }}>
        <img
          src={image}
          alt={title || 'decorative image'}
          style={{ display: 'block', width: '100%', height: 'auto', borderRadius: 12 }}
        />
        {title && (
          <div
            style={{
              position: 'absolute', left: 16, bottom: 14, maxWidth: '70%',
              fontWeight: 800, fontSize: 20, lineHeight: 1.15, color: '#fff',
              textShadow: '0 2px 12px rgba(0,0,0,.55)',
              // Defensive clamp: never let a long title dominate the art.
              display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
              overflow: 'hidden',
            }}
          >
            {title}
          </div>
        )}
      </div>
      <Bracket pos="tl" />
      <Bracket pos="tr" />
      <Bracket pos="bl" />
      <Bracket pos="br" />
    </div>
  );
}

// ── L3: hand-drawn scene (SVG source rendered inline via data-uri) ─────
// The SVG is authored server-side from hand-picked stickers (never LLM raw
// markup — the model only supplies escaped text labels), so it is trusted. An
// <img> data-uri keeps it out of the DOM sanitizer path AND preserves the SVG
// filter (feDisplacementMap) that a DOMPurify svg profile would strip.
function L3Scene({ svg }: { svg: string }) {
  const dataUri = `data:image/svg+xml,${encodeURIComponent(svg)}`;
  return (
    <div
      style={{
        position: 'relative',
        background: '#fdfdfb',
        borderRadius: 18,
        border: '1px solid #d8d5cd',
        padding: 10,
        overflow: 'hidden',
      }}
    >
      <img
        src={dataUri}
        alt="hand-drawn scene"
        style={{ display: 'block', width: '100%', height: 'auto' }}
      />
      <Bracket pos="tl" />
      <Bracket pos="tr" />
      <Bracket pos="bl" />
      <Bracket pos="br" />
    </div>
  );
}

// ── Provenance legend (rendered OUTSIDE the shadow DOM; every citation
//    number maps to a real notebook source — HARD RULE §2.6) ────────────
function SourcesLegend({ sources }: { sources?: InfographicSource[] }) {
  const rows = (sources || []).filter((s) => s && s.title);
  if (rows.length === 0) return null;
  return (
    <div className="mt-2 px-1">
      <div className="text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500 mb-1">
        Sources
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        {rows.map((s) => (
          <span key={s.n} className="inline-flex items-baseline gap-1 text-[11px] text-gray-500 dark:text-gray-400">
            <sup className="text-[#e0503a] font-bold">{s.n}</sup>
            <span className="truncate max-w-[220px]" title={s.title}>{s.title}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

export const InfographicArtifactRenderer = ({ artifact, context, className = '' }: RendererProps<InfographicPayload>) => {
  const payload = (artifact.payload || {}) as InfographicPayload;
  const accent = accentHex(payload.style);

  if (payload.lane === 'L3' && payload.scene_svg) {
    return (
      <div className={className}>
        <L3Scene svg={payload.scene_svg} />
        <SourcesLegend sources={payload.sources} />
      </div>
    );
  }

  if (payload.lane === 'L4' && payload.image) {
    return (
      <div className={className}>
        <L4Art image={payload.image} title={payload.title_overlay} accent={accent} />
        <SourcesLegend sources={payload.sources} />
      </div>
    );
  }

  if (payload.lane === 'L1' && payload.chart) {
    const height = context === 'chat-inline' ? 240 : 380;
    // Recolor the primary series to the restyle accent (the coordinate-free
    // annotation chrome already picks it up via `accent`). Non-mutating so the
    // stored payload keeps the builder's original colors.
    const chart: ChartConfig = payload.style?.accent && payload.style.accent !== 'coral'
      ? {
          ...payload.chart,
          series: (payload.chart.series || []).map((s, i) =>
            i === 0 ? { ...s, color: accent } : s,
          ),
        }
      : payload.chart;
    return (
      <div className={className}>
        <L1Chart chart={chart} annotations={payload.annotations || {}} height={height} accent={accent} cite={payload.annotations?.callout_cite} accentSvg={payload.accent_svg} />
        <SourcesLegend sources={payload.sources} />
      </div>
    );
  }

  if (payload.body_html) {
    return (
      <div className={className}>
        <L2Body html={payload.body_html} style={payload.style} />
        <SourcesLegend sources={payload.sources} />
      </div>
    );
  }

  // Fail-open placeholder.
  return (
    <div className={`p-3 rounded-lg border border-dashed border-gray-300 dark:border-gray-600 ${className}`}>
      <p className="text-[11px] text-gray-500 dark:text-gray-400">
        Infographic unavailable{payload.degrade_reason ? `: ${payload.degrade_reason}` : '.'}
      </p>
    </div>
  );
};

export default InfographicArtifactRenderer;
