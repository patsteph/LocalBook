/**
 * MentalModelPanel — curator's "what I think you're doing" view per notebook.
 *
 * Curator Phase 3a (2026-05-13). Renders inside CuratorPanel. Surfaces
 * the curator's inferred mental model with 6 inline-editable fields.
 * User can edit individual values + pin fields to lock them against
 * future auto-inference.
 *
 * 2026-06-15: open/close is now controlled by the parent (lightbulb in
 * CuratorPanel banner). Panel still mounts when closed so it can fetch
 * once and report confidence back via onConfidenceChange (drives the
 * lightbulb tint). Polling only runs while open.
 */
import { useCallback, useEffect, useState } from 'react';
import {
  Pin,
  PinOff,
  RefreshCw,
  Loader2,
  Lightbulb,
  Scale,
} from 'lucide-react';
import { API_BASE_URL, localFetch } from '../../services/api';

interface MentalModel {
  notebook_id: string;
  thesis: string;
  goals: string[];
  audience: string;
  stage: string;
  blocked_on: string;
  recent_focus: string;
  pinned_fields: string[];
  confidence: number;
  last_inferred_at: string | null;
  last_user_edit_at: string | null;
  exists?: boolean;
}

interface MentalModelPanelProps {
  notebookId: string | null;
  isOpen: boolean;
  onConfidenceChange?: (confidence: number | null, hasModel: boolean) => void;
}

interface StanceCounts {
  supports: number;
  contradicts: number;
  tangential: number;
  off_topic: number;
}

interface DissentingSource {
  source_id: string;
  title?: string;
  confidence: number;
  rationale: string;
}

interface StancePayload {
  notebook_id: string;
  counts: StanceCounts;
  top_dissent: DissentingSource[];
  total: number;
}

function DissentMeter({
  stances,
  refreshing,
  onRescoreAll,
  hasThesis,
}: {
  stances: StancePayload | null;
  refreshing: boolean;
  onRescoreAll: () => void;
  hasThesis: boolean;
}) {
  const [expanded, setExpanded] = useState(false);

  // Phase 3b hotfix (2026-05-13): always render the section once a thesis
  // exists, even when total=0. Previously we hid the meter and the user
  // never saw the feature OR the manual trigger button.
  if (!hasThesis) return null;

  const counts = stances?.counts ?? { supports: 0, contradicts: 0, tangential: 0, off_topic: 0 };
  const total = stances?.total ?? 0;
  const supportPct = total > 0 ? (counts.supports / total) * 100 : 0;
  const tangentialPct = total > 0 ? (counts.tangential / total) * 100 : 0;
  const contradictsPct = total > 0 ? (counts.contradicts / total) * 100 : 0;
  const top_dissent = stances?.top_dissent ?? [];
  // Off_topic intentionally excluded from the bar — those are noise, not signal.

  return (
    <div className="mt-3 pt-3 border-t border-gray-200 dark:border-gray-700/50">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <Scale className="h-3.5 w-3.5 text-gray-500 dark:text-gray-400" />
          <span className="text-[11px] font-medium text-gray-700 dark:text-gray-200 uppercase tracking-wide">
            Evidence balance
          </span>
          {total > 0 && (
            <span className="text-[10px] text-gray-500 dark:text-gray-400">
              ({total} scored)
            </span>
          )}
        </div>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onRescoreAll();
          }}
          disabled={refreshing}
          className="flex items-center gap-1 text-[10px] text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white hover:underline cursor-pointer disabled:opacity-50"
          aria-label={total > 0 ? "Re-score all sources" : "Score sources now"}
        >
          {refreshing ? (
            <Loader2 className="h-3 w-3 animate-spin" />
          ) : null}
          {total > 0 ? "Re-score all" : "Score sources now"}
        </button>
      </div>

      {total === 0 && (
        <div className="text-[11px] text-gray-500 dark:text-gray-400 italic py-1">
          No sources scored against this thesis yet. New sources are scored
          automatically; or click "Score sources now" to do it manually.
        </div>
      )}

      {total > 0 && (
        <>
          {/* Bar visualization */}
          <div className="flex h-2 rounded-full overflow-hidden bg-gray-200 dark:bg-gray-700 mb-2">
            {supportPct > 0 && (
              <div
                className="bg-emerald-500 dark:bg-emerald-600"
                style={{ width: `${supportPct}%` }}
                title={`${counts.supports} supporting`}
              />
            )}
            {tangentialPct > 0 && (
              <div
                className="bg-gray-400 dark:bg-gray-500"
                style={{ width: `${tangentialPct}%` }}
                title={`${counts.tangential} tangential`}
              />
            )}
            {contradictsPct > 0 && (
              <div
                className="bg-amber-500 dark:bg-amber-600"
                style={{ width: `${contradictsPct}%` }}
                title={`${counts.contradicts} contradicting`}
              />
            )}
          </div>

          {/* Counts */}
          <div className="flex items-center gap-3 text-[11px] flex-wrap">
            <span className="flex items-center gap-1 text-emerald-700 dark:text-emerald-400">
              <span className="inline-block w-2 h-2 rounded-full bg-emerald-500 dark:bg-emerald-600" />
              {counts.supports} supporting
            </span>
            <span className="flex items-center gap-1 text-gray-600 dark:text-gray-400">
              <span className="inline-block w-2 h-2 rounded-full bg-gray-400 dark:bg-gray-500" />
              {counts.tangential} tangential
            </span>
            <span className="flex items-center gap-1 text-amber-700 dark:text-amber-400">
              <span className="inline-block w-2 h-2 rounded-full bg-amber-500 dark:bg-amber-600" />
              {counts.contradicts} contradicting
            </span>
            {counts.off_topic > 0 && (
              <span className="text-[10px] text-gray-400 dark:text-gray-500 italic">
                ({counts.off_topic} off-topic)
              </span>
            )}
          </div>
        </>
      )}

      {/* Dissent expander */}
      {top_dissent.length > 0 && (
        <div className="mt-2">
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="text-[11px] text-amber-700 dark:text-amber-400 hover:underline cursor-pointer"
          >
            {expanded ? '▼ Hide' : '▶ Show'} contradicting sources ({top_dissent.length})
          </button>
          {expanded && (
            <div className="mt-1.5 space-y-1.5 pl-3 border-l-2 border-amber-500/40 dark:border-amber-400/40">
              {top_dissent.map((s, i) => (
                <div key={i} className="text-[11px]">
                  <div className="font-medium text-gray-800 dark:text-gray-200 truncate">
                    {s.title || s.source_id}
                  </div>
                  <div className="text-gray-600 dark:text-gray-400 italic">
                    {s.rationale}{' '}
                    <span className="text-[10px] text-gray-500 dark:text-gray-500">
                      ({Math.round((s.confidence || 0) * 100)}% confidence)
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

type EditableField = 'thesis' | 'goals' | 'audience' | 'stage' | 'blocked_on' | 'recent_focus';

const FIELD_LABEL: Record<EditableField, string> = {
  thesis: 'Thesis',
  goals: 'Goals',
  audience: 'Audience',
  stage: 'Stage',
  blocked_on: 'Blocked on',
  recent_focus: 'Recent focus',
};

const FIELD_PLACEHOLDER: Record<EditableField, string> = {
  thesis: 'What you\'re trying to claim or learn (one sentence)',
  goals: 'Comma-separated short-term goals',
  audience: 'Who this is for',
  stage: 'exploration / gathering / synthesis / drafting / done',
  blocked_on: 'Anything stuck',
  recent_focus: 'What you\'ve been zooming in on',
};

export function MentalModelPanel({ notebookId, isOpen, onConfidenceChange }: MentalModelPanelProps) {
  const [model, setModel] = useState<MentalModel | null>(null);
  const [stances, setStances] = useState<StancePayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [rescoring, setRescoring] = useState(false);
  const [editingField, setEditingField] = useState<EditableField | null>(null);
  const [draftValue, setDraftValue] = useState('');

  const fetchModel = useCallback(async () => {
    if (!notebookId) return;
    setLoading(true);
    try {
      const [modelRes, stancesRes] = await Promise.all([
        localFetch(`${API_BASE_URL}/curator/notebooks/${encodeURIComponent(notebookId)}/mental-model`),
        localFetch(`${API_BASE_URL}/curator/notebooks/${encodeURIComponent(notebookId)}/stances`),
      ]);
      if (modelRes.ok) {
        const data = (await modelRes.json()) as MentalModel;
        setModel(data);
      }
      if (stancesRes.ok) {
        const data = (await stancesRes.json()) as StancePayload;
        setStances(data);
      }
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, [notebookId]);

  const handleRescoreAll = async () => {
    if (!notebookId || rescoring) return;
    setRescoring(true);
    try {
      await localFetch(
        `${API_BASE_URL}/curator/notebooks/${encodeURIComponent(notebookId)}/stances/rescore-all`,
        { method: 'POST' }
      );
      // Rescore runs in background; the 10s poll will pick up changes.
    } catch {
      // silent
    } finally {
      // Re-enable after a short delay so the user can't spam the button
      window.setTimeout(() => setRescoring(false), 2000);
    }
  };

  useEffect(() => {
    void fetchModel();
  }, [fetchModel]);

  // Poll while open so that inference fired in the background
  // (event-bus triggered after a source-add) gets reflected without a
  // manual reload. 10s interval is cheap (~one GET) and matches the
  // ~5-15s typical inference latency with phi4-mini.
  useEffect(() => {
    if (!notebookId || !isOpen) return;
    const id = window.setInterval(() => {
      void fetchModel();
    }, 10000);
    return () => window.clearInterval(id);
  }, [notebookId, isOpen, fetchModel]);

  // Bubble confidence + has-model up to the banner lightbulb so it can tint.
  useEffect(() => {
    if (!onConfidenceChange) return;
    const hasModelLocal = !!model && (model.exists !== false || !!model.last_inferred_at);
    onConfidenceChange(hasModelLocal ? (model?.confidence ?? null) : null, hasModelLocal);
  }, [model, onConfidenceChange]);

  const handleRefresh = async () => {
    if (!notebookId || refreshing) return;
    setRefreshing(true);
    try {
      const res = await localFetch(
        `${API_BASE_URL}/curator/notebooks/${encodeURIComponent(notebookId)}/mental-model/infer`,
        { method: 'POST' }
      );
      if (res.ok) {
        const data = (await res.json()) as MentalModel;
        setModel(data);
      }
    } catch {
      // silent
    } finally {
      setRefreshing(false);
    }
  };

  const saveField = async (field: EditableField, value: string) => {
    if (!notebookId) return;
    const parsedValue: string | string[] =
      field === 'goals'
        ? value.split(',').map((s) => s.trim()).filter(Boolean)
        : value;
    try {
      const res = await localFetch(
        `${API_BASE_URL}/curator/notebooks/${encodeURIComponent(notebookId)}/mental-model`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ field, value: parsedValue }),
        }
      );
      if (res.ok) {
        const data = (await res.json()) as MentalModel;
        setModel(data);
      }
    } catch {
      // silent
    }
  };

  const togglePin = async (field: EditableField) => {
    if (!notebookId || !model) return;
    const wasPinned = model.pinned_fields.includes(field);
    const current = field === 'goals' ? (model.goals || []).join(', ') : (model[field] as string);
    try {
      const res = await localFetch(
        `${API_BASE_URL}/curator/notebooks/${encodeURIComponent(notebookId)}/mental-model`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            field,
            value:
              field === 'goals'
                ? current.split(',').map((s) => s.trim()).filter(Boolean)
                : current,
            pin: !wasPinned,
          }),
        }
      );
      if (res.ok) {
        const data = (await res.json()) as MentalModel;
        setModel(data);
      }
    } catch {
      // silent
    }
  };

  if (!notebookId || !isOpen) return null;

  const hasModel = !!model && (model.exists !== false || !!model.last_inferred_at);
  const fieldOrder: EditableField[] = ['thesis', 'stage', 'goals', 'audience', 'recent_focus', 'blocked_on'];

  // Curator Phase 4: confidence tier drives visual treatment of the panel.
  // strong ≥ 0.85, medium 0.5–0.85, weak < 0.5. Tier affects header tint
  // and field rendering (italicized + grey for weak; bold for strong).
  const confidence = model?.confidence || 0;
  const tier: 'strong' | 'medium' | 'weak' | 'none' = hasModel
    ? confidence >= 0.85
      ? 'strong'
      : confidence >= 0.5
        ? 'medium'
        : 'weak'
    : 'none';
  const tierBorderClass =
    tier === 'strong'
      ? 'border-emerald-300 dark:border-emerald-700'
      : tier === 'weak'
        ? 'border-amber-300 dark:border-amber-700'
        : 'border-gray-300 dark:border-gray-700';
  const tierBgClass =
    tier === 'strong'
      ? 'bg-emerald-50/40 dark:bg-emerald-900/10'
      : tier === 'weak'
        ? 'bg-amber-50/40 dark:bg-amber-900/10'
        : 'bg-gray-50 dark:bg-gray-800/40';
  const tierLabel =
    tier === 'strong'
      ? 'high confidence'
      : tier === 'medium'
        ? 'medium confidence'
        : tier === 'weak'
          ? 'tentative — correct me'
          : '';

  return (
    <div className={`my-3 rounded-md border ${tierBorderClass} ${tierBgClass} overflow-hidden`}>
      {/* Static header (no chevron — open/close is owned by the banner lightbulb) */}
      <div className="w-full flex items-center justify-between px-3 py-2 select-none">
        <div className="flex items-center gap-2 min-w-0">
          <Lightbulb className="h-3.5 w-3.5 text-amber-500 dark:text-amber-400" />
          <span className="text-xs font-medium text-gray-800 dark:text-gray-200">
            What I think you're doing
          </span>
          {hasModel && tierLabel && (
            <span
              className={`text-[11px] ${
                tier === 'strong'
                  ? 'text-emerald-700 dark:text-emerald-400'
                  : tier === 'weak'
                    ? 'text-amber-700 dark:text-amber-400 italic'
                    : 'text-gray-500 dark:text-gray-400'
              }`}
              title={`Curator confidence: ${Math.round(confidence * 100)}%`}
            >
              {tierLabel}
            </span>
          )}
        </div>
        {hasModel && (
          <button
            type="button"
            onClick={() => void handleRefresh()}
            disabled={refreshing}
            className="flex items-center gap-1 px-2 py-0.5 rounded text-[11px] text-gray-600 dark:text-gray-300 hover:text-gray-900 dark:hover:text-white hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
            aria-label="Refresh inference"
          >
            {refreshing ? (
              <Loader2 className="h-3 w-3 animate-spin" />
            ) : (
              <RefreshCw className="h-3 w-3" />
            )}
            Refresh
          </button>
        )}
      </div>

      {/* Body */}
      <div className="px-3 pb-3 pt-1 border-t border-gray-200 dark:border-gray-700/50">
          {loading && !model ? (
            <div className="py-3 text-[11px] text-gray-500 dark:text-gray-400 italic">
              <Loader2 className="inline h-3 w-3 mr-1.5 animate-spin" />
              Loading…
            </div>
          ) : !hasModel ? (
            <div className="py-3 space-y-2">
              <div className="text-[11px] text-gray-500 dark:text-gray-400 italic">
                Curator will form a mental model after a few sources have been added to this notebook.
              </div>
              <button
                type="button"
                onClick={() => void handleRefresh()}
                disabled={refreshing}
                className="flex items-center gap-1.5 px-2 py-1 rounded text-[11px] text-gray-700 dark:text-gray-200 bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 hover:bg-gray-100 dark:hover:bg-gray-600 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                aria-label="Run inference now"
              >
                {refreshing ? (
                  <Loader2 className="h-3 w-3 animate-spin" />
                ) : (
                  <RefreshCw className="h-3 w-3" />
                )}
                Try inference now
              </button>
              <p className="text-[10px] text-gray-400 dark:text-gray-500">
                Requires at least 5 sources in this notebook.
              </p>
            </div>
          ) : (
            <div className="space-y-2 pt-1">
              {fieldOrder.map((field) => {
                const isPinned = model?.pinned_fields.includes(field) || false;
                const isEditing = editingField === field;
                const displayValue =
                  field === 'goals'
                    ? (model?.goals || []).join(', ')
                    : ((model?.[field] as string) || '');
                return (
                  <div key={field} className="flex items-start gap-2">
                    <div className="flex-shrink-0 w-24 pt-0.5 flex items-center gap-1">
                      <span className="text-[11px] text-gray-500 dark:text-gray-400 uppercase tracking-wide">
                        {FIELD_LABEL[field]}
                      </span>
                      <button
                        type="button"
                        onClick={() => void togglePin(field)}
                        className="text-gray-400 dark:text-gray-500 hover:text-amber-500 dark:hover:text-amber-400 transition-colors cursor-pointer"
                        aria-label={isPinned ? 'Unpin field' : 'Pin field (lock against auto-inference)'}
                        title={isPinned ? 'Pinned — won\'t be overwritten by inference' : 'Pin to lock this value'}
                      >
                        {isPinned ? (
                          <Pin className="h-3 w-3 text-amber-500 dark:text-amber-400" />
                        ) : (
                          <PinOff className="h-3 w-3" />
                        )}
                      </button>
                    </div>
                    <div className="flex-1 min-w-0">
                      {isEditing ? (
                        <input
                          type="text"
                          autoFocus
                          value={draftValue}
                          onChange={(e) => setDraftValue(e.target.value)}
                          onBlur={() => {
                            void saveField(field, draftValue);
                            setEditingField(null);
                          }}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                              e.currentTarget.blur();
                            } else if (e.key === 'Escape') {
                              setEditingField(null);
                            }
                          }}
                          placeholder={FIELD_PLACEHOLDER[field]}
                          className="w-full px-2 py-1 text-xs bg-white dark:bg-gray-900 border border-blue-400 dark:border-blue-500 rounded text-gray-800 dark:text-gray-200 focus:outline-none focus:ring-1 focus:ring-blue-500"
                        />
                      ) : (
                        <div
                          role="button"
                          tabIndex={0}
                          onClick={() => {
                            setDraftValue(displayValue);
                            setEditingField(field);
                          }}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' || e.key === ' ') {
                              e.preventDefault();
                              setDraftValue(displayValue);
                              setEditingField(field);
                            }
                          }}
                          className={`w-full px-2 py-1 text-xs bg-transparent border border-transparent hover:border-gray-300 dark:hover:border-gray-600 rounded cursor-pointer min-h-[26px] ${
                            tier === 'strong'
                              ? 'text-gray-900 dark:text-gray-100 font-medium'
                              : tier === 'weak'
                                ? 'text-gray-600 dark:text-gray-400 italic'
                                : 'text-gray-700 dark:text-gray-200'
                          }`}
                        >
                          {displayValue || (
                            <span className="italic text-gray-400 dark:text-gray-500">
                              {hasModel
                                ? '(curator hasn\'t formed this yet)'
                                : FIELD_PLACEHOLDER[field]}
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
              {/* Dissent meter — Curator Phase 3b */}
              <DissentMeter
                stances={stances}
                refreshing={rescoring}
                onRescoreAll={() => void handleRescoreAll()}
                hasThesis={!!(model?.thesis && model.thesis.trim())}
              />
            </div>
          )}
        </div>
    </div>
  );
}
