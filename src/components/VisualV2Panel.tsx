/**
 * VisualV2Panel — Visual System v2 surface.
 *
 * Self-contained: handles its own state, capability detection, generation,
 * and rendering. Dropped into VisualPanel.tsx as a collapsible "Beta v2"
 * section so existing visual generation is untouched.
 *
 * Calls the /visual/v2/* composer endpoints. Renders native SVG and shows
 * tier badge + 5-axis critic scores + retry indicator.
 */
import React, { useCallback, useEffect, useState } from 'react';
import {
  visualService,
  V2Capability,
  V2ComposedVisual,
  V2CriticScore,
  V2TierEvent,
} from '../services/visual';
import { API_BASE_URL, localFetch } from '../services/api';
import { Button } from './shared/Button';
import { LoadingSpinner } from './shared/LoadingSpinner';
import { SVGRenderer } from './shared/SVGRenderer';
import { FeedbackThumbs } from './shared/FeedbackThumbs';

interface VisualV2PanelProps {
  notebookId: string;
}

export const VisualV2Panel: React.FC<VisualV2PanelProps> = ({ notebookId }) => {
  const [capability, setCapability] = useState<V2Capability | null>(null);
  const [capLoading, setCapLoading] = useState(true);
  const [topic, setTopic] = useState('');
  const [loading, setLoading] = useState(false);
  const [tier, setTier] = useState<V2TierEvent | null>(null);
  const [visual, setVisual] = useState<V2ComposedVisual | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [criticExpanded, setCriticExpanded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    visualService
      .v2GetCapability()
      .then((cap) => {
        if (!cancelled) {
          setCapability(cap);
          setCapLoading(false);
        }
      })
      .catch(() => {
        if (!cancelled) setCapLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleGenerate = useCallback(async () => {
    if (!topic.trim() || loading) return;
    setLoading(true);
    setError(null);
    setTier(null);
    setVisual(null);

    try {
      await visualService.v2ComposeStream(
        notebookId,
        topic,
        (info) => setTier(info),
        () => { /* critic event handled via visual.critic_score on result */ },
        (v) => setVisual(v),
        () => setLoading(false),
        (msg) => {
          setError(msg);
          setLoading(false);
        },
      );
    } catch (e: any) {
      setError(e?.message || 'Generation failed');
      setLoading(false);
    }
  }, [notebookId, topic, loading]);

  const downloadPng = useCallback(async () => {
    if (!visual?.svg_markup) return;
    try {
      // Must use localFetch — the endpoint requires the X-LocalBook-Token
      // header injected by api/localFetch. Plain fetch returns 401 silently.
      const response = await localFetch(`${API_BASE_URL}/visual/v2/render/png`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ svg_markup: visual.svg_markup }),
      });
      if (!response.ok) {
        console.error(`PNG download failed: ${response.status} ${response.statusText}`);
        return;
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${(visual.title || 'visual').replace(/[^a-z0-9]+/gi, '_')}.png`;
      // Anchor must be in the DOM for click() to fire reliably in the Tauri WebView
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error('PNG download error:', err);
    }
  }, [visual]);

  if (capLoading) {
    return (
      <div className="text-xs text-gray-500 dark:text-gray-400 p-4">
        Loading v2 capability…
      </div>
    );
  }

  if (!capability) {
    return (
      <div className="text-xs text-red-500 dark:text-red-400 p-4">
        v2 capability unavailable
      </div>
    );
  }

  const pathLabel = (() => {
    if (capability.can_freeform_gemma) return 'Gemma freeform SVG';
    if (capability.can_freeform_olmo) return 'Olmo freeform (Phase 2)';
    return 'Template path (Tier D)';
  })();

  return (
    <div className="space-y-3">
      {/* Capability summary */}
      <div className="bg-indigo-50 dark:bg-indigo-900/20 border border-indigo-200 dark:border-indigo-800 rounded-lg p-3">
        <div className="flex items-start justify-between gap-2">
          <div>
            <div className="text-xs font-medium text-indigo-700 dark:text-indigo-300">
              ✨ Visual System v2
            </div>
            <div className="text-[11px] text-indigo-600 dark:text-indigo-400 mt-0.5">
              {capability.setup === 'setup_b' ? 'Setup B' : 'Setup A'} ·{' '}
              {capability.total_ram_gb} GB · {capability.concurrency_mode} ·{' '}
              Path: {pathLabel}
            </div>
            {capability.warn_user && (
              <div className="text-[11px] text-amber-600 dark:text-amber-400 mt-1">
                ⚠ Low RAM — Klein hero images may be skipped on this machine
              </div>
            )}
          </div>
          <div className="flex gap-1 flex-wrap">
            {capability.can_freeform_gemma && <Pill color="indigo" label="Gemma" />}
            {capability.can_diffusion_klein && <Pill color="purple" label="Klein" />}
            {capability.can_critic_gemma_vision && <Pill color="green" label="Critic" />}
          </div>
        </div>
      </div>

      {/* Topic input */}
      <div>
        <label className="text-xs font-medium text-gray-700 dark:text-gray-300 block mb-1">
          What should this visual show?
        </label>
        <textarea
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder="A three-tier microservices architecture with API gateway, four backend services, and a Redis cache…"
          className="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-2 focus:ring-indigo-400"
          rows={4}
        />
      </div>

      <Button
        onClick={handleGenerate}
        disabled={loading || !topic.trim()}
        className="w-full"
      >
        {loading ? (
          <span className="flex items-center justify-center gap-2">
            <LoadingSpinner size="sm" />
            {tier ? `Generating via ${tier.path}…` : 'Starting…'}
          </span>
        ) : (
          '✨ Generate (v2 composer)'
        )}
      </Button>

      {error && (
        <div className="bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 p-3 rounded-lg text-sm">
          {error}
        </div>
      )}

      {/* Result */}
      {visual && visual.success && (
        <div className="space-y-2">
          {/* Header row: title + badges */}
          <div className="flex items-start justify-between gap-2">
            <div>
              <h4 className="text-sm font-semibold text-gray-900 dark:text-gray-100">
                {visual.title}
              </h4>
              {visual.description && (
                <p className="text-[11px] text-gray-500 dark:text-gray-400 mt-0.5">
                  {visual.description}
                </p>
              )}
            </div>
            <div className="flex gap-1 flex-wrap shrink-0">
              <Pill color="slate" label={visual.path} />
              {visual.retry_count > 0 && (
                <Pill color="amber" label={`refined ×${visual.retry_count}`} />
              )}
              {visual.generation_ms > 0 && (
                <Pill color="slate" label={`${(visual.generation_ms / 1000).toFixed(1)}s`} />
              )}
            </div>
          </div>

          {/* Critic scores collapsible */}
          {visual.critic_score && (
            <div className="bg-gray-50 dark:bg-gray-800 rounded-lg overflow-hidden">
              <button
                onClick={() => setCriticExpanded((x) => !x)}
                className="w-full px-3 py-2 flex items-center justify-between text-xs hover:bg-gray-100 dark:hover:bg-gray-700"
              >
                <span className="font-medium text-gray-700 dark:text-gray-300">
                  Critic: {visual.critic_score.overall.toFixed(2)} overall
                  {visual.critic_score.overall >= 0.7 ? ' ✓' : ' ⚠'}
                </span>
                <span className="text-gray-400">{criticExpanded ? '▼' : '▶'}</span>
              </button>
              {criticExpanded && <CriticBody score={visual.critic_score} />}
            </div>
          )}

          {/* Rendered SVG */}
          {visual.svg_markup && (
            <SVGRenderer svg={visual.svg_markup} title={visual.title} />
          )}

          {/* Actions */}
          <div className="flex gap-2 items-center justify-between">
            <FeedbackThumbs
              kind="curator_feature"
              subjectType="studio_visual"
              subjectId={`v2-${visual.template_id || 'freeform'}-${Date.now()}`}
              notebookId={notebookId}
              payload={{
                skill_id: 'visual_v2',
                path: visual.path,
                idiom: visual.template_id,
                critic_overall: visual.critic_score?.overall,
                retry_count: visual.retry_count,
              }}
              size="sm"
            />
            {visual.svg_markup && (
              <button
                onClick={downloadPng}
                className="px-2 py-1 text-xs rounded-lg bg-indigo-100 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300 hover:bg-indigo-200 dark:hover:bg-indigo-800/40"
              >
                📷 Download PNG
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

// ────────────────────────────────────────────────────────────────────
// Subcomponents
// ────────────────────────────────────────────────────────────────────
const Pill: React.FC<{ color: 'indigo' | 'purple' | 'green' | 'amber' | 'slate'; label: string }> = ({
  color,
  label,
}) => {
  const colors = {
    indigo:
      'bg-indigo-100 dark:bg-indigo-900/40 text-indigo-700 dark:text-indigo-300',
    purple:
      'bg-purple-100 dark:bg-purple-900/40 text-purple-700 dark:text-purple-300',
    green:
      'bg-green-100 dark:bg-green-900/40 text-green-700 dark:text-green-300',
    amber:
      'bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300',
    slate:
      'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300',
  } as const;
  return (
    <span className={`px-2 py-0.5 text-[10px] rounded-full ${colors[color]}`}>
      {label}
    </span>
  );
};

const CriticBody: React.FC<{ score: V2CriticScore }> = ({ score }) => {
  const axes = [
    ['Legibility', score.legibility],
    ['Hierarchy', score.hierarchy],
    ['Balance', score.balance],
    ['Color harmony', score.color_harmony],
    ['Message clarity', score.message_clarity],
  ] as const;
  return (
    <div className="px-3 py-2 space-y-2 text-xs">
      <div className="space-y-1">
        {axes.map(([label, value]) => (
          <ScoreBar key={label} label={label} value={value} />
        ))}
      </div>
      {score.weaknesses.length > 0 && (
        <div>
          <div className="text-[10px] font-semibold text-red-600 dark:text-red-400 mt-2">
            ISSUES
          </div>
          <ul className="list-disc pl-4 text-gray-600 dark:text-gray-400 text-[11px] space-y-0.5">
            {score.weaknesses.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}
      {score.suggestions.length > 0 && (
        <div>
          <div className="text-[10px] font-semibold text-indigo-600 dark:text-indigo-400 mt-2">
            SUGGESTED FIXES
          </div>
          <ul className="list-disc pl-4 text-gray-600 dark:text-gray-400 text-[11px] space-y-0.5">
            {score.suggestions.map((s, i) => (
              <li key={i}>{s}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
};

const ScoreBar: React.FC<{ label: string; value: number }> = ({ label, value }) => {
  const pct = Math.round(value * 100);
  const color =
    value >= 0.8 ? 'bg-green-500'
    : value >= 0.6 ? 'bg-indigo-500'
    : value >= 0.4 ? 'bg-amber-500'
    : 'bg-red-500';
  return (
    <div className="flex items-center gap-2">
      <span className="w-28 text-gray-600 dark:text-gray-400 text-[11px]">
        {label}
      </span>
      <span className="w-10 text-gray-900 dark:text-gray-100 font-mono text-[11px]">
        {value.toFixed(2)}
      </span>
      <span className="flex-1 h-2 bg-gray-200 dark:bg-gray-700 rounded overflow-hidden">
        <span
          className={`block h-full ${color}`}
          style={{ width: `${pct}%` }}
        />
      </span>
    </div>
  );
};
