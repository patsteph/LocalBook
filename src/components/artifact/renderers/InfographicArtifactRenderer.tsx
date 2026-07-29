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

export interface InfographicAnnotations {
  callout_text?: string;
  callout_subtext?: string;
  primary_label?: string;
  baseline_label?: string;
  midpoint_note?: string;
}

export interface InfographicPayload {
  lane: 'L1' | 'L2';
  archetype: string;
  body_html?: string;
  chart?: ChartConfig;
  annotations?: InfographicAnnotations;
  citations?: { id: number; label: string }[];
  degraded?: boolean;
  degrade_reason?: string;
}

const ACCENT = '#e0503a';
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
function L2Body({ html }: { html: string }) {
  const hostRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    const shadow = host.shadowRoot ?? host.attachShadow({ mode: 'open' });
    const clean = DOMPurify.sanitize(html, SANITIZE_CONFIG);
    shadow.innerHTML = `<style>${INFOGRAPHIC_L2_CSS}</style>${clean}`;
  }, [html]);
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

function L1Chart({
  chart,
  annotations,
  height,
}: {
  chart: ChartConfig;
  annotations: InfographicAnnotations;
  height: number;
}) {
  const ann = annotations || {};
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
      }}
    >
      <Bracket pos="tl" />
      <Bracket pos="tr" />
      <Bracket pos="bl" />
      <Bracket pos="br" />
      <div style={{ filter: `drop-shadow(0 0 5px rgba(224,80,58,.45))` }}>
        <ChartRenderer config={chart} height={height} darkMode={false} />
      </div>

      {ann.callout_text && (
        <div
          style={{
            position: 'absolute', top: 22, right: 26, maxWidth: 240,
            background: '#fff', border: `2px solid ${ACCENT}`, borderRadius: 12,
            padding: '9px 13px',
            boxShadow: `0 0 0 1px rgba(224,80,58,.18), 0 0 20px 1px rgba(224,80,58,.4)`,
          }}
        >
          <b style={{ display: 'block', fontSize: 15, fontWeight: 800, color: INK, lineHeight: 1.15 }}>
            {ann.callout_text}
          </b>
          {ann.callout_subtext && (
            <small style={{ display: 'block', fontSize: 11, color: '#8b8880', marginTop: 2 }}>
              {ann.callout_subtext}
            </small>
          )}
        </div>
      )}
      {ann.primary_label && (
        <div style={{ position: 'absolute', top: '46%', left: '44%', fontSize: 14, fontWeight: 800, color: ACCENT }}>
          {ann.primary_label}
        </div>
      )}
      {ann.baseline_label && (
        <div style={{ position: 'absolute', bottom: '15%', right: '7%', fontSize: 14, fontWeight: 800, color: INK }}>
          {ann.baseline_label}
        </div>
      )}
      {ann.midpoint_note && (
        <div style={{ position: 'absolute', bottom: '24%', left: '20%', maxWidth: 160, fontSize: 12.5, fontWeight: 600, color: '#4a4844' }}>
          {ann.midpoint_note}
        </div>
      )}
    </div>
  );
}

export const InfographicArtifactRenderer = ({ artifact, context, className = '' }: RendererProps<InfographicPayload>) => {
  const payload = (artifact.payload || {}) as InfographicPayload;

  if (payload.lane === 'L1' && payload.chart) {
    const height = context === 'chat-inline' ? 240 : 380;
    return (
      <div className={className}>
        <L1Chart chart={payload.chart} annotations={payload.annotations || {}} height={height} />
      </div>
    );
  }

  if (payload.body_html) {
    return (
      <div className={className}>
        <L2Body html={payload.body_html} />
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
