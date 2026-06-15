import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Lightbulb } from 'lucide-react';
import { MarkdownArtifactRenderer } from './artifact/renderers/MarkdownArtifactRenderer';
import { curatorService, type BrainStatus, type AnticipatoryDraftStatus, type VoiceScoreboard, type StudioScoreboard, type SourceReputationRow } from '../services/curatorApi';
import { useEngagement } from '../hooks/useEngagement';
import { MentalModelPanel } from './curator/MentalModelPanel';
import { FeedbackThumbs } from './shared/FeedbackThumbs';
import { useReadTime } from '../hooks/useReadTime';
import { ArtifactRender } from './artifact/RendererRegistry';

// Per-notebook open/closed state for the mental-model panel, persisted to
// sessionStorage so navigating between notebooks doesn't snap it shut.
// 2026-06-15: panel toggle moved from inside the panel into the banner
// lightbulb so the panel is hidden by default until the user asks for it.
const MM_OPEN_KEY = 'lb_curator_mm_open_v1';
const loadMMOpen = (): Record<string, boolean> => {
  try {
    const raw = sessionStorage.getItem(MM_OPEN_KEY);
    return raw ? (JSON.parse(raw) as Record<string, boolean>) : {};
  } catch {
    return {};
  }
};
const saveMMOpen = (state: Record<string, boolean>) => {
  try {
    sessionStorage.setItem(MM_OPEN_KEY, JSON.stringify(state));
  } catch {
    // sessionStorage full or unavailable — silently drop
  }
};

interface CuratorConfig {
  name: string;
  personality: string;
  oversight?: Record<string, any>;
  synthesis?: Record<string, any>;
  voice?: Record<string, any>;
  narrative_voice?: 'smart_colleague' | 'executive_brief' | 'conversational_analyst';
}

// Fix #2 (2026-05-23): voice picker options match VOICE_PROMPTS in
// agents/curator.py — keep names + descriptions in sync if backend changes.
const VOICE_OPTIONS: Array<{
  value: 'smart_colleague' | 'executive_brief' | 'conversational_analyst';
  label: string;
  description: string;
}> = [
  {
    value: 'conversational_analyst',
    label: 'Conversational analyst',
    description: 'Chatty, curious, signposts thinking, invites engagement',
  },
  {
    value: 'smart_colleague',
    label: 'Smart colleague',
    description: 'Observational, opinion-bearing, gently candid',
  },
  {
    value: 'executive_brief',
    label: 'Executive brief',
    description: 'Crisp, status-focused, action-oriented',
  },
];

interface CuratorMessage {
  role: 'user' | 'curator';
  content: string;
  timestamp: Date;
  // 2026-05-23 (universal thumbs): tag a message as a morning brief so the
  // bubble renders FeedbackThumbs scoped to subject_type='brief' with the
  // voice + brief_id metadata Phase 7.2 (self-evaluating briefs) needs.
  isBrief?: boolean;
  briefId?: string;
  // Phase 10 — sanitized HTML dashboard variant. When present, the
  // message renders via <ArtifactRender type='html'> instead of the
  // markdown bubble.
  contentHtml?: string;
}

interface BriefStory {
  title: string;
  source_name?: string;
  url?: string;
  summary?: string;
}

interface BriefNotebook {
  notebook_id: string;
  name: string;
  subject?: string;
  items_added: number;
  pending_approval: number;
  flagged_important: number;
  top_finding: string | null;
  recent_stories?: BriefStory[];
  person_changes?: string[];
  upcoming_key_dates?: string[];
  collection_runs?: number;
  collection_items_found?: number;
  interactions_since?: number;
  chat_queries?: number;
  searches?: number;
  docs_read?: number;
  total_sources?: number;
  sources_this_week?: number;
  unfinished_threads?: string[];
  emerging_topics?: string[];
  one_week_ago_items?: string[];
}

interface MorningBriefData {
  away_duration: string;
  notebooks?: BriefNotebook[];
  cross_notebook_insight?: string | null;
  narrative?: string;
  // Phase 10 — server-composed HTML dashboard variant + consensus audit.
  narrative_html?: string | null;
  consensus_clusters?: unknown[];
  deep_reads_triggered?: { topic_label?: string; notebook_id?: string; query?: string; cluster_id?: string }[];
}

interface CuratorPanelProps {
  notebookId: string | null;
  morningBrief?: MorningBriefData | null;
}

export const CuratorPanel: React.FC<CuratorPanelProps> = ({ notebookId, morningBrief }) => {
  const [config, setConfig] = useState<CuratorConfig | null>(null);
  const [messages, setMessages] = useState<CuratorMessage[]>([]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [nameInput, setNameInput] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const { capture: captureEngagement } = useEngagement();
  // Fix #2/#4 (2026-05-23): settings panel state — voice picker + brain status.
  const [showSettings, setShowSettings] = useState(false);
  const [savingVoice, setSavingVoice] = useState(false);
  const [brainStatus, setBrainStatus] = useState<BrainStatus | null>(null);
  // Fix #3 (2026-05-23): anticipatory draft pill state.
  const [anticipatoryDraft, setAnticipatoryDraft] = useState<AnticipatoryDraftStatus['draft']>(null);
  // 2026-05-23: Phase 7 scoreboard state for the settings panel.
  const [voiceScoreboard, setVoiceScoreboard] = useState<VoiceScoreboard | null>(null);
  const [studioScoreboard, setStudioScoreboard] = useState<StudioScoreboard | null>(null);
  const [sourceReputation, setSourceReputation] = useState<SourceReputationRow[]>([]);

  // 2026-06-15: mental-model panel is hidden by default. Banner lightbulb
  // controls open/close per-notebook; confidence feeds back from the panel
  // to drive the lightbulb tint (emerald=strong, amber=weak, gray=neutral).
  const [mmOpenMap, setMmOpenMap] = useState<Record<string, boolean>>(() => loadMMOpen());
  const mentalModelOpen = notebookId ? !!mmOpenMap[notebookId] : false;
  const [mmConfidence, setMmConfidence] = useState<number | null>(null);
  const [mmHasModel, setMmHasModel] = useState(false);
  // Reset the lightbulb tint when the notebook changes; the panel will
  // refetch and report back.
  useEffect(() => {
    setMmConfidence(null);
    setMmHasModel(false);
  }, [notebookId]);
  const handleMmConfidenceChange = useCallback(
    (confidence: number | null, hasModel: boolean) => {
      setMmConfidence(confidence);
      setMmHasModel(hasModel);
    },
    []
  );
  const toggleMentalModel = useCallback(() => {
    if (!notebookId) return;
    setMmOpenMap((prev) => {
      const next = { ...prev, [notebookId]: !prev[notebookId] };
      saveMMOpen(next);
      return next;
    });
  }, [notebookId]);

  // Fix #4 (2026-05-23): brief read-time tracker. Attaches to the first
  // brief message in the rendered list; fires `brief / read_time` with
  // total visible dwell on unmount or when the brief changes. Powers
  // Phase 7.2 calibration with finer-grained signal than thumbs alone.
  const briefMessage = messages.find(m => m.isBrief);
  const briefRefCallback = useReadTime({
    kind: 'brief',
    signal: 'read_time',
    subjectType: 'morning_brief',
    subjectId: briefMessage?.briefId || 'no_brief',
    notebookId,
    payload: { voice: config?.narrative_voice || 'conversational_analyst' },
  });

  // Curator Phase 2b: capture that the user opened the curator panel.
  // Brain uses this as a signal of which curator surfaces the user
  // actually engages with vs ignores.
  useEffect(() => {
    captureEngagement('curator_feature', 'opened', {
      subject_type: 'panel',
      subject_id: 'curator_panel',
      notebook_id: notebookId || undefined,
    });
  }, [notebookId, captureEngagement]);

  // Inject morning brief as the curator's opening message when navigated from banner
  const briefConsumedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!morningBrief) return;
    // Only consume each brief once (keyed by generated_at or away_duration)
    const briefKey = morningBrief.away_duration + (morningBrief.notebooks?.length || 0);
    if (briefConsumedRef.current === briefKey) return;
    briefConsumedRef.current = briefKey;

    const greeting = new Date().getHours() < 12 ? 'morning' : new Date().getHours() < 17 ? 'afternoon' : 'evening';

    // Use LLM narrative if available — this is the newsletter-quality brief
    // Guard: skip error strings that leaked through as "narrative"
    const narrative = morningBrief.narrative || '';
    const isValidNarrative = narrative.length > 30 && 
      !narrative.startsWith('Request timed out') && 
      !narrative.startsWith('Error:');
    
    if (isValidNarrative) {
      // Phase 10 — when the backend supplied an HTML dashboard, attach it
      // alongside the markdown content. The chat bubble renders the HTML
      // variant via ArtifactRender when present, falls back to markdown
      // otherwise.
      const htmlVariant = (morningBrief.narrative_html || '').trim();
      setMessages([{
        role: 'curator',
        content: `Good ${greeting}! You've been away for ${morningBrief.away_duration}.\n\n${narrative}\n\n---\n*Ask me anything about what happened while you were away.*`,
        contentHtml: htmlVariant || undefined,
        timestamp: new Date(),
        isBrief: true,
        briefId: briefKey,
      }]);
      // Capture brief-open event tagged with the active voice (Phase 7.2).
      captureEngagement('brief', 'opened', {
        subject_type: 'morning_brief',
        subject_id: briefKey,
        payload: {
          voice: config?.narrative_voice || 'conversational_analyst',
          narrative_chars: narrative.length,
          notebook_count: morningBrief.notebooks?.length || 0,
        },
      });
      return;
    }

    // Fallback: build rich structured brief from raw data
    const lines: string[] = [`Good ${greeting}! You've been away for ${morningBrief.away_duration}. Here's what happened:\n`];
    for (const nb of morningBrief.notebooks || []) {
      const label = nb.subject ? `**${nb.name}** (${nb.subject})` : `**${nb.name}**`;
      lines.push(label);

      // Recent stories — specific titles, not just counts
      if (nb.recent_stories && nb.recent_stories.length > 0) {
        for (const story of nb.recent_stories.slice(0, 3)) {
          let storyLine = `  - "${story.title}"`;
          if (story.source_name) storyLine += ` *(${story.source_name})*`;
          if (story.summary) storyLine += ` — ${story.summary.slice(0, 120)}`;
          lines.push(storyLine);
        }
        if (nb.items_added > nb.recent_stories.length) {
          lines.push(`  - ...and ${nb.items_added - nb.recent_stories.length} more`);
        }
      } else if (nb.items_added > 0) {
        lines.push(`  - ${nb.items_added} new source${nb.items_added !== 1 ? 's' : ''} added`);
      }

      // Research velocity
      if (nb.total_sources && nb.sources_this_week && nb.sources_this_week > 0) {
        const prior = nb.total_sources - nb.sources_this_week;
        lines.push(`  - 📊 Library grew from ${prior} to ${nb.total_sources} sources this week (+${nb.sources_this_week})`);
      }

      // User activity
      if (nb.interactions_since && nb.interactions_since > 0) {
        const parts: string[] = [];
        if (nb.chat_queries && nb.chat_queries > 0) parts.push(`${nb.chat_queries} chat${nb.chat_queries !== 1 ? 's' : ''}`);
        if (nb.searches && nb.searches > 0) parts.push(`${nb.searches} search${nb.searches !== 1 ? 'es' : ''}`);
        if (nb.docs_read && nb.docs_read > 0) parts.push(`${nb.docs_read} doc${nb.docs_read !== 1 ? 's' : ''} read`);
        if (parts.length > 0) lines.push(`  - 🔄 Your activity: ${parts.join(', ')}`);
      }

      // People updates
      if (nb.person_changes && nb.person_changes.length > 0) {
        for (const pc of nb.person_changes.slice(0, 3)) {
          lines.push(`  - 👤 ${pc}`);
        }
      }

      // Upcoming key dates
      if (nb.upcoming_key_dates && nb.upcoming_key_dates.length > 0) {
        for (const kd of nb.upcoming_key_dates.slice(0, 2)) {
          lines.push(`  - 📅 ${kd}`);
        }
      }

      // Pending approval
      if (nb.pending_approval > 0) {
        lines.push(`  - ⏳ ${nb.pending_approval} items awaiting your review`);
      }

      // Unfinished threads
      if (nb.unfinished_threads && nb.unfinished_threads.length > 0) {
        lines.push(`  - 💬 **Unfinished threads:**`);
        for (const thread of nb.unfinished_threads.slice(0, 2)) {
          lines.push(`    - "${thread}"`);
        }
      }

      // Emerging topics
      if (nb.emerging_topics && nb.emerging_topics.length > 0) {
        lines.push(`  - 🔮 **Emerging interests:** ${nb.emerging_topics.join(', ')} — new this week`);
      }

      // One week ago
      if (nb.one_week_ago_items && nb.one_week_ago_items.length > 0) {
        lines.push(`  - ⏪ **One week ago** you were reading:`);
        for (const item of nb.one_week_ago_items.slice(0, 2)) {
          lines.push(`    - "${item}"`);
        }
      }

      lines.push('');
    }

    if (morningBrief.cross_notebook_insight) {
      lines.push(`💡 **Cross-notebook insight:** ${morningBrief.cross_notebook_insight}`);
    }
    lines.push('---\n*Ask me anything about what happened while you were away.*');

    setMessages([{
      role: 'curator',
      content: lines.join('\n'),
      timestamp: new Date(),
      isBrief: true,
      briefId: briefKey,
    }]);
    // Capture brief-open event for the fallback path too (Phase 7.2).
    captureEngagement('brief', 'opened', {
      subject_type: 'morning_brief_fallback',
      subject_id: briefKey,
      payload: {
        voice: config?.narrative_voice || 'conversational_analyst',
        notebook_count: morningBrief.notebooks?.length || 0,
      },
    });
  }, [morningBrief, captureEngagement, config?.narrative_voice]);

  // Load config on mount
  useEffect(() => {
    curatorService.getConfig()
      .then((data: any) => {
        if (data) {
          setConfig(data);
          setNameInput(data.name || 'Curator');
        }
      })
      .catch(() => {});
  }, []);

  // Fix #4 (2026-05-23): load brain status when settings panel is opened
  // (no point fetching if user never opens settings — reduces background load).
  // 2026-05-23: also load Phase 7 scoreboards on the same trigger so the
  // user sees readiness data without a second roundtrip.
  useEffect(() => {
    if (!showSettings) return;
    curatorService.getBrainStatus()
      .then(s => { if (s) setBrainStatus(s); })
      .catch(() => {});
    curatorService.getVoiceScoreboard()
      .then(setVoiceScoreboard)
      .catch(() => {});
    curatorService.getStudioScoreboard()
      .then(setStudioScoreboard)
      .catch(() => {});
    if (notebookId) {
      curatorService.getSourceReputation(notebookId)
        .then(r => setSourceReputation(r.sources))
        .catch(() => {});
    } else {
      setSourceReputation([]);
    }
  }, [showSettings, notebookId]);

  // Fix #3 (2026-05-23): poll for an anticipatory draft. Refetches when the
  // active notebook changes. Could be replaced with an SSE subscription on
  // the existing /curator/events/stream if we want push semantics later.
  useEffect(() => {
    if (!notebookId) { setAnticipatoryDraft(null); return; }
    let cancelled = false;
    curatorService.getAnticipatoryDraft(notebookId)
      .then(s => { if (!cancelled) setAnticipatoryDraft(s.draft); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [notebookId]);

  // Fix #2 (2026-05-23): handle voice picker change. Optimistic update
  // followed by PUT. On failure, revert.
  const handleVoiceChange = useCallback(async (newVoice: 'smart_colleague' | 'executive_brief' | 'conversational_analyst') => {
    if (!config || savingVoice || config.narrative_voice === newVoice) return;
    const prev = config.narrative_voice;
    setSavingVoice(true);
    setConfig({ ...config, narrative_voice: newVoice });
    try {
      await curatorService.updateConfig({ narrative_voice: newVoice });
      captureEngagement('curator_feature', 'invoked', {
        subject_type: 'voice_change',
        subject_id: newVoice,
      });
    } catch {
      setConfig({ ...config, narrative_voice: prev });
    } finally {
      setSavingVoice(false);
    }
  }, [config, savingVoice, captureEngagement]);

  // Fix #3 (2026-05-23): open the draft via the existing @curator chat path.
  // We synthesize a "show draft" user turn so the user can see what arrived
  // without needing a new endpoint or UI workflow.
  const handleOpenDraft = useCallback(async () => {
    if (!anticipatoryDraft) return;
    const userMsg: CuratorMessage = {
      role: 'user',
      content: '@curator show draft',
      timestamp: new Date(),
    };
    setMessages(prev => [...prev, userMsg]);
    setLoading(true);
    try {
      const data = await curatorService.chat('show draft', notebookId || undefined);
      setMessages(prev => [...prev, {
        role: 'curator',
        content: data.reply,
        timestamp: new Date(),
      }]);
      // Hide the pill — the draft is being consumed by the @curator path
      // which calls mark_draft_consumed in the brain.
      setAnticipatoryDraft(null);
    } catch {
      setMessages(prev => [...prev, {
        role: 'curator',
        content: 'Couldn\'t load the draft — try `@curator show draft` directly.',
        timestamp: new Date(),
      }]);
    } finally {
      setLoading(false);
    }
  }, [anticipatoryDraft, notebookId]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSendMessage = useCallback(async (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || loading) return;

    const userMsg: CuratorMessage = {
      role: 'user',
      content: input.trim(),
      timestamp: new Date(),
    };
    setMessages(prev => [...prev, userMsg]);
    setInput('');
    setLoading(true);

    try {
      const data = await curatorService.chat(userMsg.content, notebookId || undefined);
      setMessages(prev => [...prev, {
        role: 'curator',
        content: data.reply,
        timestamp: new Date(),
      }]);
    } catch {
      setMessages(prev => [...prev, {
        role: 'curator',
        content: 'Connection error. Please try again.',
        timestamp: new Date(),
      }]);
    } finally {
      setLoading(false);
    }
  }, [input, loading, messages, notebookId]);

  const handleSaveName = useCallback(async () => {
    if (!nameInput.trim()) return;
    try {
      const data = await curatorService.updateConfig({ name: nameInput.trim() } as any);
      setConfig(data.config);
      setEditingName(false);
    } catch {
      // ignore
    }
  }, [nameInput]);

  const curatorName = config?.name || 'Curator';

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex-shrink-0 px-4 py-3 border-b border-gray-200 dark:border-gray-700 bg-gradient-to-r from-indigo-50 to-purple-50 dark:from-indigo-900/20 dark:to-purple-900/20">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-full bg-indigo-600 dark:bg-indigo-500 flex items-center justify-center text-white text-sm font-bold">
              {curatorName.charAt(0).toUpperCase()}
            </div>
            {editingName ? (
              <div className="flex items-center gap-1">
                <input
                  type="text"
                  value={nameInput}
                  onChange={e => setNameInput(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handleSaveName()}
                  className="text-sm font-semibold bg-white dark:bg-gray-800 border border-indigo-300 dark:border-indigo-600 rounded-lg px-2 py-0.5 w-32 focus:outline-none focus:ring-1 focus:ring-indigo-500"
                  autoFocus
                />
                <button
                  onClick={handleSaveName}
                  className="text-xs text-indigo-600 dark:text-indigo-400 hover:text-indigo-800 font-medium"
                >
                  Save
                </button>
                <button
                  onClick={() => { setEditingName(false); setNameInput(curatorName); }}
                  className="text-xs text-gray-400 hover:text-gray-600"
                >
                  Cancel
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-1.5">
                <h3 className="text-sm font-semibold text-indigo-800 dark:text-indigo-200">
                  {curatorName}
                </h3>
                <button
                  onClick={() => { setEditingName(true); setNameInput(curatorName); }}
                  className="text-gray-400 hover:text-indigo-600 dark:hover:text-indigo-400"
                  title="Rename"
                >
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                  </svg>
                </button>
              </div>
            )}
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs text-indigo-500 dark:text-indigo-400 bg-indigo-100 dark:bg-indigo-900/40 px-2 py-0.5 rounded-full">
              Cross-Notebook Advisor
            </span>
            {/* Fix #2/#4 (2026-05-23): settings cog opens voice picker + brain status. */}
            <button
              onClick={() => setShowSettings(v => !v)}
              className={`p-1 rounded transition-colors ${
                showSettings
                  ? 'bg-indigo-200 dark:bg-indigo-800 text-indigo-700 dark:text-indigo-200'
                  : 'text-indigo-500 dark:text-indigo-400 hover:bg-indigo-100 dark:hover:bg-indigo-900/40'
              }`}
              title={showSettings ? 'Hide curator settings' : 'Curator settings & brain status'}
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
            </button>
          </div>
        </div>
        <div className="flex items-end justify-between gap-2 mt-1">
          <p className="text-xs text-gray-500 dark:text-gray-400 ml-10 min-w-0 flex-1">
            {config?.personality || 'Your research advisor with cross-notebook awareness'}
          </p>
          {/* 2026-06-15: lightbulb opens the "what I think you're doing"
              panel. Tint reflects curator confidence so users get an
              ambient cue without having to unfurl. */}
          {notebookId && (() => {
            const tier: 'strong' | 'medium' | 'weak' | 'none' = !mmHasModel || mmConfidence === null
              ? 'none'
              : mmConfidence >= 0.85
                ? 'strong'
                : mmConfidence >= 0.5
                  ? 'medium'
                  : 'weak';
            const tint =
              tier === 'strong'
                ? 'text-emerald-500 dark:text-emerald-400'
                : tier === 'weak'
                  ? 'text-amber-500 dark:text-amber-400'
                  : 'text-gray-400 dark:text-gray-500';
            const title = !mmHasModel
              ? "What I think you're doing — no read yet"
              : tier === 'strong'
                ? `What I think you're doing — high confidence (${Math.round((mmConfidence ?? 0) * 100)}%)`
                : tier === 'weak'
                  ? `What I think you're doing — tentative, correct me (${Math.round((mmConfidence ?? 0) * 100)}%)`
                  : `What I think you're doing — medium confidence (${Math.round((mmConfidence ?? 0) * 100)}%)`;
            return (
              <button
                type="button"
                onClick={toggleMentalModel}
                aria-expanded={mentalModelOpen}
                aria-label={title}
                title={title}
                className={`flex-shrink-0 p-1 rounded transition-colors cursor-pointer ${
                  mentalModelOpen
                    ? 'bg-indigo-200/70 dark:bg-indigo-800/60 ring-1 ring-indigo-300 dark:ring-indigo-600'
                    : 'hover:bg-indigo-100 dark:hover:bg-indigo-900/40'
                }`}
              >
                <Lightbulb className={`w-4 h-4 ${tint}`} />
              </button>
            );
          })()}
        </div>
      </div>

      {/* Fix #2/#4 (2026-05-23): collapsible curator settings + brain status. */}
      {showSettings && (
        <div className="flex-shrink-0 border-b border-indigo-100 dark:border-indigo-800/40 bg-indigo-50/30 dark:bg-indigo-900/10 px-4 py-3 space-y-3">
          {/* Voice picker — Phase 6a UI (was missing) */}
          <div>
            <label className="block text-[10px] font-semibold uppercase tracking-wide text-indigo-600 dark:text-indigo-400 mb-1">
              Curator voice
            </label>
            <select
              value={config?.narrative_voice || 'conversational_analyst'}
              onChange={e => handleVoiceChange(e.target.value as any)}
              disabled={savingVoice}
              className="w-full text-xs px-2 py-1 rounded border border-indigo-200 dark:border-indigo-700 bg-white dark:bg-gray-800 text-gray-800 dark:text-gray-200 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            >
              {VOICE_OPTIONS.map(v => (
                <option key={v.value} value={v.value}>{v.label}</option>
              ))}
            </select>
            <p className="text-[10px] text-gray-500 dark:text-gray-400 mt-1 italic">
              {VOICE_OPTIONS.find(v => v.value === (config?.narrative_voice || 'conversational_analyst'))?.description}
            </p>
          </div>

          {/* Phase 7.2 readiness — voice scoreboard. Empty until briefs accrue.
              When any voice gets ≥2 thumbs_down in 7d, the future auto-rotate
              worker will rotate away from it; for now this is read-only. */}
          {voiceScoreboard && Object.keys(voiceScoreboard.voices).length > 0 && (
            <div>
              <label className="block text-[10px] font-semibold uppercase tracking-wide text-indigo-600 dark:text-indigo-400 mb-1">
                Voice scoreboard <span className="text-gray-400 normal-case">· last {voiceScoreboard.lookback_days}d</span>
              </label>
              <div className="space-y-1">
                {Object.entries(voiceScoreboard.voices).map(([voice, scores]) => (
                  <div key={voice} className="flex items-center justify-between text-[10px] bg-white dark:bg-gray-800 rounded px-2 py-1 border border-indigo-100 dark:border-indigo-800/40">
                    <span className="font-medium text-gray-800 dark:text-gray-200">{voice}</span>
                    <span className="text-gray-500">
                      <span title="opens">👁 {scores.opens}</span>
                      <span className="ml-2 text-emerald-600 dark:text-emerald-400" title="thumbs up">👍 {scores.thumbs_up}</span>
                      <span className="ml-2 text-rose-600 dark:text-rose-400" title="thumbs down">👎 {scores.thumbs_down}</span>
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Phase 7.5 readiness — Studio kind scoreboard. */}
          {studioScoreboard && Object.keys(studioScoreboard.kinds).length > 0 && (
            <div>
              <label className="block text-[10px] font-semibold uppercase tracking-wide text-indigo-600 dark:text-indigo-400 mb-1">
                Studio outputs <span className="text-gray-400 normal-case">· last {studioScoreboard.lookback_days}d</span>
              </label>
              <div className="space-y-1">
                {Object.entries(studioScoreboard.kinds).map(([kind, scores]) => (
                  <div key={kind} className="flex items-center justify-between text-[10px] bg-white dark:bg-gray-800 rounded px-2 py-1 border border-indigo-100 dark:border-indigo-800/40">
                    <span className="font-medium text-gray-800 dark:text-gray-200">{kind.replace('studio_', '')}</span>
                    <span className="text-gray-500">
                      <span title="invoked">🔧 {scores.invoked}</span>
                      <span className="ml-2 text-emerald-600 dark:text-emerald-400">👍 {scores.thumbs_up}</span>
                      <span className="ml-2 text-rose-600 dark:text-rose-400">👎 {scores.thumbs_down}</span>
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Phase 7.6 readiness — source reputation for this notebook. */}
          {notebookId && sourceReputation.length > 0 && (
            <div>
              <label className="block text-[10px] font-semibold uppercase tracking-wide text-indigo-600 dark:text-indigo-400 mb-1">
                Source reputation <span className="text-gray-400 normal-case">· this notebook</span>
              </label>
              <div className="space-y-1 max-h-32 overflow-y-auto">
                {sourceReputation.slice(0, 6).map(s => (
                  <div key={s.source_id} className="text-[10px] bg-white dark:bg-gray-800 rounded px-2 py-1 border border-indigo-100 dark:border-indigo-800/40">
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium text-gray-800 dark:text-gray-200 truncate flex-1 min-w-0" title={s.source_label}>
                        {s.source_label || s.source_id.slice(0, 16)}
                      </span>
                      <span className={`flex-shrink-0 px-1 rounded ${
                        s.rolling_acceptance_rate >= 0.7 ? 'text-emerald-600' :
                        s.rolling_acceptance_rate >= 0.3 ? 'text-amber-600' :
                        'text-rose-600'
                      }`}>
                        {Math.round(s.rolling_acceptance_rate * 100)}%
                      </span>
                    </div>
                    <div className="text-gray-400 mt-0.5">
                      {s.added_count + s.approved_count} kept · {s.rejected_count} rejected · lifetime {Math.round(s.lifetime_acceptance_rate * 100)}%
                    </div>
                  </div>
                ))}
                {sourceReputation.length > 6 && (
                  <div className="text-[10px] text-gray-400 italic">
                    +{sourceReputation.length - 6} more
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Brain status — diagnostic counts */}
          <div>
            <label className="block text-[10px] font-semibold uppercase tracking-wide text-indigo-600 dark:text-indigo-400 mb-1">
              Brain status
            </label>
            {brainStatus ? (
              <div className="grid grid-cols-2 gap-1.5 text-[10px]">
                <div className="bg-white dark:bg-gray-800 rounded px-2 py-1 border border-indigo-100 dark:border-indigo-800/40">
                  <div className="text-gray-500 dark:text-gray-400">Digests</div>
                  <div className="font-semibold text-gray-800 dark:text-gray-200">
                    {brainStatus.digests.length}
                    <span className="ml-1 text-gray-400">
                      ({brainStatus.digests.filter(d => d.has_summary).length} with summary)
                    </span>
                  </div>
                </div>
                <div className="bg-white dark:bg-gray-800 rounded px-2 py-1 border border-indigo-100 dark:border-indigo-800/40">
                  <div className="text-gray-500 dark:text-gray-400">Connections</div>
                  <div className="font-semibold text-gray-800 dark:text-gray-200">
                    {brainStatus.connections.length}
                    <span className="ml-1 text-gray-400">
                      ({brainStatus.connections.filter(c => c.tier === 'strong').length} strong)
                    </span>
                  </div>
                </div>
                {Object.entries(brainStatus.stats || {}).slice(0, 6).map(([k, v]) => (
                  <div key={k} className="bg-white dark:bg-gray-800 rounded px-2 py-1 border border-indigo-100 dark:border-indigo-800/40">
                    <div className="text-gray-500 dark:text-gray-400 truncate">{k.replace(/_/g, ' ')}</div>
                    <div className="font-semibold text-gray-800 dark:text-gray-200">{String(v)}</div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-[10px] text-gray-400 italic">Loading…</div>
            )}
            {/* Fix #9 (2026-05-23): Phase 4 confidence-tier coloring for
                individual connections. Same green/yellow/grey thresholds
                used by MentalModelPanel so the visual language is uniform
                across surfaces. Top 5 shown; rest fold into the count. */}
            {brainStatus && brainStatus.connections.length > 0 && (
              <div className="mt-2 space-y-0.5">
                {brainStatus.connections.slice(0, 5).map(c => {
                  const tierColor =
                    c.tier === 'strong' ? 'text-emerald-700 dark:text-emerald-400 border-emerald-200 dark:border-emerald-700/50' :
                    c.tier === 'weak'   ? 'text-gray-500 dark:text-gray-400 border-gray-200 dark:border-gray-700/50' :
                                          'text-amber-700 dark:text-amber-400 border-amber-200 dark:border-amber-700/50';
                  return (
                    <div key={c.id} className={`text-[10px] px-2 py-1 rounded border-l-2 bg-white dark:bg-gray-800 ${tierColor}`}>
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate flex-1 min-w-0">{c.description}</span>
                        <span className="flex-shrink-0 opacity-70 text-[9px]">{Math.round(c.strength * 100)}%</span>
                      </div>
                    </div>
                  );
                })}
                {brainStatus.connections.length > 5 && (
                  <div className="text-[10px] text-gray-400 italic">+{brainStatus.connections.length - 5} more connections</div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Mental model — Curator Phase 3a. 2026-06-15: panel always mounts
          when a notebook is selected so it can fetch the current confidence
          for the banner lightbulb tint. Body only renders when isOpen. */}
      {notebookId && (
        <div className="flex-shrink-0 px-4">
          <MentalModelPanel
            notebookId={notebookId}
            isOpen={mentalModelOpen}
            onConfidenceChange={handleMmConfidenceChange}
          />
        </div>
      )}

      {/* Fix #3 (2026-05-23): anticipatory draft pill. Phase 6a's
          maybe_fire_anticipatory_draft creates drafts in the background;
          before this pill they were invisible unless the user happened to
          type "@curator show draft". Now we surface them proactively. */}
      {anticipatoryDraft && (
        <div className="flex-shrink-0 px-4 pt-2">
          <button
            type="button"
            onClick={handleOpenDraft}
            disabled={loading}
            className="w-full text-left px-3 py-2 rounded-lg bg-gradient-to-r from-indigo-100 to-purple-100 dark:from-indigo-900/30 dark:to-purple-900/30 border border-indigo-200 dark:border-indigo-700 hover:from-indigo-200 hover:to-purple-200 dark:hover:from-indigo-900/50 dark:hover:to-purple-900/50 transition-colors group disabled:opacity-50 disabled:cursor-wait"
            title="Open the draft the curator prepared in the background"
          >
            <div className="flex items-center gap-2">
              <span className="text-base">✨</span>
              <div className="flex-1 min-w-0">
                <div className="text-[11px] font-semibold text-indigo-700 dark:text-indigo-300 flex items-center gap-1.5">
                  Draft ready
                  <span className="text-[9px] font-normal bg-indigo-200/60 dark:bg-indigo-800/60 px-1.5 py-0.5 rounded-full">
                    {anticipatoryDraft.kind}
                  </span>
                </div>
                <div className="text-[10px] text-gray-600 dark:text-gray-400 truncate mt-0.5">
                  {anticipatoryDraft.preview.slice(0, 100)}…
                </div>
              </div>
              <span className="text-[10px] text-indigo-500 dark:text-indigo-400 group-hover:translate-x-0.5 transition-transform">
                Open →
              </span>
            </div>
          </button>
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-3">
        {messages.length === 0 && (
          <div className="text-center mt-8 space-y-3">
            <div className="w-12 h-12 rounded-full bg-indigo-100 dark:bg-indigo-900/40 flex items-center justify-center mx-auto">
              <svg className="w-6 h-6 text-indigo-600 dark:text-indigo-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
              </svg>
            </div>
            <p className="text-sm text-gray-600 dark:text-gray-300 font-medium">
              Chat with {curatorName}
            </p>
            <p className="text-xs text-gray-400 dark:text-gray-500 max-w-xs mx-auto">
              Ask about connections across your notebooks, get a devil's advocate perspective, or discuss research strategy.
            </p>
            <div className="flex flex-wrap justify-center gap-1.5 mt-2">
              {[
                'What patterns do you see across my notebooks?',
                'Play devil\'s advocate on my current research',
                'What gaps should I fill?',
              ].map((suggestion, i) => (
                <button
                  key={i}
                  onClick={() => setInput(suggestion)}
                  className="text-xs px-2.5 py-1 bg-indigo-50 dark:bg-indigo-900/30 text-indigo-700 dark:text-indigo-300 rounded-full hover:bg-indigo-100 dark:hover:bg-indigo-800/40 border border-indigo-200 dark:border-indigo-700 transition-colors"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div
            key={i}
            ref={msg.isBrief ? briefRefCallback : undefined}
            className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
          >
            <div
              className={`max-w-[85%] rounded-lg p-3 ${
                msg.role === 'user'
                  ? 'bg-blue-600 text-white'
                  : 'bg-indigo-50 dark:bg-indigo-900/30 text-indigo-900 dark:text-indigo-100 border border-indigo-200 dark:border-indigo-700/50'
              }`}
            >
              {msg.role === 'curator' && (
                <div className="flex items-center gap-1.5 mb-1">
                  <div className="w-4 h-4 rounded-full bg-indigo-600 dark:bg-indigo-500 flex items-center justify-center text-white text-[8px] font-bold">
                    {curatorName.charAt(0).toUpperCase()}
                  </div>
                  <span className="text-xs font-semibold text-indigo-600 dark:text-indigo-400">
                    {curatorName}
                  </span>
                </div>
              )}
              {msg.role === 'curator' ? (
                msg.contentHtml ? (
                  // Phase 10 — HTML dashboard variant. Dispatches via the
                  // artifact registry (strict HtmlArtifactRenderer, since
                  // the dashboard is server-composed and we want the
                  // strict sanitization guarantees).
                  // K3 (2026-06-09): the dashboard no longer contains
                  // the LLM narrative (markdown was being rendered as
                  // raw text). We render the markdown narrative *below*
                  // the dashboard so headings + emphasis work properly.
                  <>
                    <ArtifactRender
                      artifact={{
                        id: msg.briefId || msg.timestamp.toISOString(),
                        type: 'html',
                        payload: msg.contentHtml,
                        title: 'Morning brief',
                      }}
                      context="canvas-full"
                    />
                    <div className="mt-4">
                      <MarkdownArtifactRenderer
                        artifact={{ id: `${msg.timestamp.toISOString()}-narrative`, type: 'markdown', payload: msg.content }}
                        context="chat-inline"
                      />
                    </div>
                  </>
                ) : (
                  <MarkdownArtifactRenderer
                    artifact={{ id: msg.timestamp.toISOString(), type: 'markdown', payload: msg.content }}
                    context="chat-inline"
                  />
                )
              ) : (
                <p className="text-sm whitespace-pre-wrap">{msg.content}</p>
              )}
              {/* 2026-05-23: thumbs on curator responses. Briefs get a more
                  prominent tagged subject_type so Phase 7.2 can slice by
                  voice; other curator messages still get thumbs but tagged
                  more generically. */}
              {msg.role === 'curator' && msg.content.length > 20 && (
                <div className="mt-1 flex items-center justify-end opacity-60 hover:opacity-100 transition-opacity">
                  <FeedbackThumbs
                    kind="brief"
                    subjectType={msg.isBrief ? 'morning_brief' : 'curator_chat_reply'}
                    subjectId={msg.briefId || msg.timestamp.toISOString()}
                    notebookId={notebookId}
                    payload={{
                      voice: config?.narrative_voice || 'conversational_analyst',
                      length: msg.content.length,
                    }}
                  />
                </div>
              )}
            </div>
          </div>
        ))}

        {loading && (
          <div className="flex justify-start">
            <div className="bg-indigo-50 dark:bg-indigo-900/30 border border-indigo-200 dark:border-indigo-700/50 rounded-lg p-3 max-w-[85%]">
              <div className="flex items-center gap-1.5 mb-1">
                <div className="w-4 h-4 rounded-full bg-indigo-600 dark:bg-indigo-500 flex items-center justify-center text-white text-[8px] font-bold">
                  {curatorName.charAt(0).toUpperCase()}
                </div>
                <span className="text-xs font-semibold text-indigo-600 dark:text-indigo-400">
                  {curatorName}
                </span>
              </div>
              <span className="text-sm text-indigo-400 animate-pulse">Thinking...</span>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <form onSubmit={handleSendMessage} className="flex-shrink-0 p-3 border-t border-gray-200 dark:border-gray-700">
        <div className="flex gap-2">
          <input
            type="text"
            value={input}
            onChange={e => setInput(e.target.value)}
            placeholder={`Ask ${curatorName} anything...`}
            className="flex-1 text-sm bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-2 focus:outline-none focus:ring-1 focus:ring-blue-500 dark:text-gray-100 placeholder-gray-400"
            disabled={loading}
          />
          <button
            type="submit"
            disabled={loading || !input.trim()}
            className="px-3 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
            </svg>
          </button>
        </div>
      </form>
    </div>
  );
};

export default CuratorPanel;
