/**
 * LibraryView — the user's archive of generated content for a notebook.
 *
 * Sibling main view to Chat / Constellation / Timeline / Curator.
 * Reads from existing per-type stores (content / audio / video / quiz /
 * notes / sources) and renders a type-grouped accordion. Click an item →
 * opens it on the canvas as a tombstone + switches view back to chat.
 *
 * The canvas is never destroyed by Library navigation — items load
 * *onto* the canvas where they sit alongside whatever else is there.
 *
 * Created 2026-06-02.
 */
import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { onEvent } from '../../lib/events';
import {
  FileText, Mic, Video, Palette, Target, StickyNote, Search,
  ChevronDown, ChevronRight, Trash2, Download,
} from 'lucide-react';
import { contentService, ContentGeneration } from '../../services/content';
import { audioService } from '../../services/audio';
import { videoService, VideoGeneration } from '../../services/video';
import { sourceService } from '../../services/sources';
import { quizService } from '../../services/quiz';
import { visualService } from '../../services/visual';
import { API_BASE_URL } from '../../services/api';
import { AudioGeneration, Source } from '../../types';

// ─── Types ──────────────────────────────────────────────────────────────────
type LibraryItemKind = 'document' | 'audio' | 'video' | 'visual' | 'quiz' | 'note';

interface LibraryItem {
  id: string;
  kind: LibraryItemKind;
  title: string;
  preview: string;
  createdAt: string;          // ISO string
  raw: unknown;                // the original record so the open handler can rehydrate
}

interface LibraryViewProps {
  notebookId: string | null;
  /** Open this item on the canvas and return the user to Chat. */
  onOpenItem: (item: LibraryItem) => void;
}

// Per-kind delete dispatcher. Returns true on success so the caller can
// optimistically remove the item from the in-memory list. Notes are stored
// as sources with type='note' so they go through the source delete path.
async function deleteByKind(notebookId: string, item: LibraryItem): Promise<boolean> {
  try {
    switch (item.kind) {
      case 'document':
        await contentService.delete(item.id);
        return true;
      case 'audio':
        await audioService.delete(item.id);
        return true;
      case 'video':
        await videoService.delete(item.id);
        return true;
      case 'note':
        await sourceService.delete(notebookId, item.id);
        return true;
      case 'visual':
        await visualService.deleteItem(item.id);
        return true;
      case 'quiz':
        await quizService.delete(item.id);
        return true;
      default:
        return false;
    }
  } catch (err) {
    console.error(`[library] failed to delete ${item.kind}/${item.id}:`, err);
    return false;
  }
}

// Per-kind download dispatcher. Each kind picks a sensible default format
// without prompting (Tier 5 goal: a Download button that "just works").
//  - document/note: markdown
//  - audio: original mp3/wav
//  - video: mp4 stream
//  - visual: SVG (Klein/Mermaid fallback handled server-side)
//  - quiz: markdown
// 2026-06-30: every kind now routes its Blob through exportService.downloadBlob
// (native save dialog + fs.writeFile). The previous `<a download>` anchor +
// `window.open` approaches silently no-op in the Tauri/WKWebView once an `await`
// breaks the synchronous user-gesture context — which is why only the document
// case (synchronous, in-memory blob) appeared to "work" while the rest didn't.
async function downloadByKind(item: LibraryItem): Promise<void> {
  const { exportService } = await import('../../services/export');
  const { localFetch } = await import('../../services/api');
  const safe = (s: string) => (s || 'download').replace(/[^a-z0-9-_ ]/gi, '_');

  switch (item.kind) {
    case 'document': {
      const raw = item.raw as ContentGeneration;
      const blob = new Blob([raw.content || ''], { type: 'text/markdown' });
      await exportService.downloadBlob(blob, `${safe(raw.skill_name || raw.topic || 'document')}.md`);
      return;
    }
    case 'note': {
      const src = item.raw as any;
      // localFetch is token-auth'd; a tokenless GET would 401 at the middleware.
      const resp = await localFetch(`${API_BASE_URL}/sources/${src.notebook_id}/${src.id || src.source_id}/download`);
      if (!resp.ok) throw new Error('Failed to download note');
      await exportService.downloadBlob(await resp.blob(), `${safe(item.title || 'note')}.md`);
      return;
    }
    case 'audio': {
      const resp = await localFetch(audioService.getDownloadUrl(item.id));
      if (!resp.ok) throw new Error('Failed to download audio');
      await exportService.downloadBlob(await resp.blob(), `${safe(item.title || 'audio')}.mp3`);
      return;
    }
    case 'video': {
      const resp = await localFetch(videoService.getStreamUrl(item.id));
      if (!resp.ok) throw new Error('Failed to download video');
      await exportService.downloadBlob(await resp.blob(), `${safe(item.title || 'video')}.mp4`);
      return;
    }
    case 'visual':
      await visualService.download(item.id, 'svg');
      return;
    case 'quiz':
      await quizService.download(item.id);
      return;
  }
}

// ─── Kind metadata ──────────────────────────────────────────────────────────
const KIND_META: Record<LibraryItemKind, { label: string; icon: React.ReactNode; accent: string }> = {
  document: { label: 'Documents',  icon: <FileText className="w-3.5 h-3.5" />, accent: 'text-blue-600 dark:text-blue-400' },
  audio:    { label: 'Audio',      icon: <Mic className="w-3.5 h-3.5" />,      accent: 'text-purple-600 dark:text-purple-400' },
  video:    { label: 'Video',      icon: <Video className="w-3.5 h-3.5" />,    accent: 'text-red-600 dark:text-red-400' },
  visual:   { label: 'Visuals',    icon: <Palette className="w-3.5 h-3.5" />,  accent: 'text-amber-600 dark:text-amber-400' },
  quiz:     { label: 'Quizzes',    icon: <Target className="w-3.5 h-3.5" />,   accent: 'text-emerald-600 dark:text-emerald-400' },
  note:     { label: 'Notes',      icon: <StickyNote className="w-3.5 h-3.5" />, accent: 'text-orange-600 dark:text-orange-400' },
};

const ALL_KINDS: LibraryItemKind[] = ['document', 'audio', 'video', 'visual', 'quiz', 'note'];

// ─── Component ──────────────────────────────────────────────────────────────
export const LibraryView: React.FC<LibraryViewProps> = ({ notebookId, onOpenItem }) => {
  const [items, setItems] = useState<LibraryItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [activeKind, setActiveKind] = useState<LibraryItemKind | 'all'>('all');
  // Expansion state per-filter. In 'all' mode, sections start collapsed (just
  // type name + count) so the view feels light at-a-glance — user expands
  // the type they care about. In a specific-kind filter, that kind is auto-
  // expanded. Per-user preferences persist via localStorage for the 'all'
  // mode only; specific-kind mode is always auto-expanded.
  const [expandedInAll, setExpandedInAll] = useState<Set<LibraryItemKind>>(() => {
    try {
      const raw = localStorage.getItem('lb-library-expanded-v2');
      if (raw) return new Set(JSON.parse(raw) as LibraryItemKind[]);
    } catch {}
    // Default: nothing expanded in 'all' mode. Lightweight overview first.
    return new Set();
  });
  useEffect(() => {
    try { localStorage.setItem('lb-library-expanded-v2', JSON.stringify([...expandedInAll])); } catch {}
  }, [expandedInAll]);

  const isExpanded = useCallback((kind: LibraryItemKind) => {
    // In specific-kind filter mode the section is always shown expanded.
    if (activeKind === kind) return true;
    if (activeKind !== 'all') return false;
    return expandedInAll.has(kind);
  }, [activeKind, expandedInAll]);

  const toggleExpanded = useCallback((kind: LibraryItemKind) => {
    if (activeKind !== 'all') {
      // In single-kind mode the section IS the entire view — collapsing it
      // would be confusing. Treat the click as a no-op.
      return;
    }
    setExpandedInAll(prev => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind); else next.add(kind);
      return next;
    });
  }, [activeKind]);

  // Load everything in parallel — each store is independent, so we don't
  // serialize. Individual failures don't take down the view.
  const loadLibrary = useCallback(async () => {
    if (!notebookId) {
      setItems([]);
      return;
    }
    setLoading(true);
    setError(null);

    const results = await Promise.allSettled([
      contentService.list(notebookId),
      audioService.list(notebookId),
      videoService.list(notebookId),
      sourceService.list(notebookId),
      quizService.list(notebookId),
      visualService.list(notebookId),
    ]);

    const collected: LibraryItem[] = [];

    if (results[0].status === 'fulfilled') {
      (results[0].value as ContentGeneration[]).forEach(c => {
        collected.push({
          id: c.content_id,
          kind: 'document',
          title: c.skill_name || c.topic || 'Document',
          preview: (c.content || '').replace(/[#*_`]/g, '').slice(0, 140),
          createdAt: c.created_at || '',
          raw: c,
        });
      });
    }
    if (results[1].status === 'fulfilled') {
      (results[1].value as AudioGeneration[]).forEach(a => {
        collected.push({
          id: a.audio_id,
          kind: 'audio',
          title: (a as any).topic || 'Audio',
          preview: (a.script || '').slice(0, 140),
          createdAt: a.created_at || '',
          raw: a,
        });
      });
    }
    if (results[2].status === 'fulfilled') {
      (results[2].value as VideoGeneration[]).forEach(v => {
        collected.push({
          id: v.video_id,
          kind: 'video',
          title: v.topic || 'Video',
          preview: `${v.format_type || ''}  ·  ${v.duration_minutes || '?'}m`,
          createdAt: v.created_at || '',
          raw: v,
        });
      });
    }
    // Sources: split into notes vs. other-uploads. Notes (source_type='note')
    // are the saved-from-chat items + the user-typed notes.
    if (results[3].status === 'fulfilled') {
      (results[3].value as Source[]).forEach(s => {
        const meta = (s as any).metadata || s;
        const sourceType = meta.type || meta.source_type || '';
        if (sourceType === 'note') {
          collected.push({
            id: (s as any).id || (s as any).source_id || '',
            kind: 'note',
            title: (s as any).filename || (s as any).title || 'Note',
            preview: ((s as any).content || '').slice(0, 140),
            createdAt: (s as any).created_at || (s as any).timestamp || '',
            raw: s,
          });
        }
      });
    }

    // Quizzes (Tier 5).
    if (results[4].status === 'fulfilled') {
      (results[4].value as any[]).forEach(q => {
        const numQ = (q.questions || []).length || q.num_questions || 0;
        collected.push({
          id: q.quiz_id,
          kind: 'quiz',
          title: q.topic || 'Quiz',
          preview: `${numQ} question${numQ === 1 ? '' : 's'}  ·  ${q.difficulty || 'medium'}`,
          createdAt: q.created_at || '',
          raw: q,
        });
      });
    }
    // Visuals (Tier 5). Use the title where available, falling back to topic.
    if (results[5].status === 'fulfilled') {
      (results[5].value as any[]).forEach(v => {
        const score = v.critic_overall;
        collected.push({
          id: v.visual_id,
          kind: 'visual',
          title: v.title || v.topic || 'Visual',
          preview: score != null ? `score ${Number(score).toFixed(2)}` : (v.template_id || ''),
          createdAt: v.created_at || '',
          raw: v,
        });
      });
    }

    collected.sort((a, b) => (b.createdAt || '').localeCompare(a.createdAt || ''));
    setItems(collected);
    setLoading(false);
  }, [notebookId]);

  useEffect(() => {
    loadLibrary();
  }, [loadLibrary]);

  // Listen for completion events from other surfaces so Library auto-refreshes.
  useEffect(() => {
    const names = ['sourcesUpdated', 'notesUpdated', 'contentUpdated', 'audioUpdated', 'videoUpdated', 'visualsUpdated', 'quizzesUpdated'] as const;
    const offs = names.map(n => onEvent(n, () => loadLibrary()));
    return () => offs.forEach(off => off());
  }, [loadLibrary]);

  // Filtered + grouped view.
  const grouped = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    const filtered = items.filter(it => {
      if (activeKind !== 'all' && it.kind !== activeKind) return false;
      if (q && !it.title.toLowerCase().includes(q) && !it.preview.toLowerCase().includes(q)) return false;
      return true;
    });
    const byKind: Record<LibraryItemKind, LibraryItem[]> = {
      document: [], audio: [], video: [], visual: [], quiz: [], note: [],
    };
    filtered.forEach(it => { byKind[it.kind].push(it); });
    return byKind;
  }, [items, searchQuery, activeKind]);

  const totalCount = items.length;
  const filteredCount = Object.values(grouped).reduce((s, arr) => s + arr.length, 0);

  // Count per kind (regardless of filter) — used in the section header
  // even when the section itself is collapsed so the user always sees totals.
  const totalByKind = useMemo(() => {
    const counts: Record<LibraryItemKind, number> = {
      document: 0, audio: 0, video: 0, visual: 0, quiz: 0, note: 0,
    };
    items.forEach(it => { counts[it.kind] += 1; });
    return counts;
  }, [items]);

  const handleDelete = useCallback(async (item: LibraryItem) => {
    if (!notebookId) return;
    const ok = window.confirm(`Delete ${item.title}? This can't be undone.`);
    if (!ok) return;
    // Optimistic removal — drop from in-memory list immediately so the user
    // gets a snappy response. On failure, restore + show a toast.
    const prev = items;
    setItems(items.filter(i => !(i.kind === item.kind && i.id === item.id)));
    const success = await deleteByKind(notebookId, item);
    if (!success) {
      setItems(prev);
      // No toast helper here yet — log only. Adding toast routing through
      // a prop would be cleaner; deferring until we see how often this fails.
    }
  }, [notebookId, items]);

  if (!notebookId) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500 dark:text-gray-400">
        Select a notebook to see its Library.
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-white dark:bg-gray-900">
      {/* Header: search + filter chips */}
      <div className="flex-shrink-0 px-4 py-3 border-b border-gray-200 dark:border-gray-700 space-y-2">
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" />
          <input
            type="search"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search your library…"
            className="w-full pl-8 pr-3 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded-md bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 placeholder-gray-400 focus:outline-none focus:ring-1 focus:ring-blue-400"
          />
        </div>
        <div className="flex flex-wrap gap-1 items-center">
          <button
            onClick={() => setActiveKind('all')}
            className={`px-2 py-0.5 text-[10px] rounded border transition-colors ${
              activeKind === 'all'
                ? 'border-gray-400 dark:border-gray-500 text-gray-800 dark:text-gray-200 bg-gray-100 dark:bg-gray-800'
                : 'border-gray-200/50 dark:border-gray-700/50 text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800'
            }`}
          >
            All
          </button>
          {ALL_KINDS.map(k => (
            <button
              key={k}
              onClick={() => setActiveKind(k)}
              className={`px-2 py-0.5 text-[10px] rounded border transition-colors flex items-center gap-1 ${
                activeKind === k
                  ? 'border-gray-400 dark:border-gray-500 text-gray-800 dark:text-gray-200 bg-gray-100 dark:bg-gray-800'
                  : 'border-gray-200/50 dark:border-gray-700/50 text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800'
              }`}
            >
              <span className={KIND_META[k].accent}>{KIND_META[k].icon}</span>
              <span>{KIND_META[k].label}</span>
            </button>
          ))}
          <span className="ml-auto text-[10px] text-gray-400 dark:text-gray-500">
            {filteredCount === totalCount ? `${totalCount} items` : `${filteredCount} of ${totalCount}`}
          </span>
        </div>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-2 py-2">
        {loading && (
          <div className="px-4 py-8 text-center text-xs text-gray-400 dark:text-gray-500">Loading library…</div>
        )}
        {error && (
          <div className="px-4 py-4 text-xs text-red-500">{error}</div>
        )}
        {!loading && !error && totalCount === 0 && (
          <div className="px-4 py-10 text-center text-xs text-gray-400 dark:text-gray-500">
            Nothing in this notebook's Library yet.<br />
            Generate something from the Studio bar to start filling it up.
          </div>
        )}
        {!loading && !error && totalCount > 0 && (
          <div className="space-y-2">
            {ALL_KINDS.map(kind => {
              // Hide the section entirely when a non-'all' filter is active
              // and this isn't the selected kind.
              if (activeKind !== 'all' && activeKind !== kind) return null;
              const sectionItems = grouped[kind];
              const sectionTotal = totalByKind[kind];
              const expanded = isExpanded(kind);
              const meta = KIND_META[kind];
              // In 'all' mode + no search, hide sections with 0 items entirely
              // so the lightweight overview doesn't list "Visuals (0)".
              if (activeKind === 'all' && !searchQuery && sectionTotal === 0) return null;
              const inSingleKindMode = activeKind === kind;

              return (
                <section key={kind} className="border border-gray-200/40 dark:border-gray-700/40 rounded-md overflow-hidden">
                  {/* Section header — clickable in 'all' mode to expand/collapse;
                     non-interactive in single-kind mode (section IS the view). */}
                  <button
                    onClick={() => toggleExpanded(kind)}
                    disabled={inSingleKindMode}
                    className={`w-full flex items-center gap-2 px-2.5 py-1.5 text-left bg-gray-50/60 dark:bg-gray-800/40 transition-colors ${
                      inSingleKindMode
                        ? 'cursor-default'
                        : 'hover:bg-gray-100/60 dark:hover:bg-gray-800/70 cursor-pointer'
                    }`}
                  >
                    {!inSingleKindMode && (
                      expanded
                        ? <ChevronDown className="w-3 h-3 text-gray-400" />
                        : <ChevronRight className="w-3 h-3 text-gray-400" />
                    )}
                    <span className={meta.accent}>{meta.icon}</span>
                    <span className="text-xs font-medium text-gray-700 dark:text-gray-200">{meta.label}</span>
                    <span className="text-[10px] text-gray-400 dark:text-gray-500 ml-auto">
                      {sectionItems.length}{sectionItems.length !== sectionTotal ? ` of ${sectionTotal}` : ''}
                    </span>
                  </button>
                  {expanded && (
                    <div className="bg-white dark:bg-gray-900">
                      {sectionItems.length === 0 ? (
                        <div className="px-3 py-3 text-[11px] text-gray-400 dark:text-gray-500 italic">
                          {searchQuery ? 'No matches' : 'Nothing yet'}
                        </div>
                      ) : (
                        sectionItems.map(it => (
                          // Whole row is the primary affordance — click anywhere
                          // (except the action buttons) to open on the canvas.
                          // Action buttons stopPropagation so they don't double-fire.
                          <div
                            key={`${it.kind}:${it.id}`}
                            role="button"
                            tabIndex={0}
                            onClick={() => onOpenItem(it)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter' || e.key === ' ') {
                                e.preventDefault();
                                onOpenItem(it);
                              }
                            }}
                            className="group flex items-start gap-2 px-3 py-2 border-t border-gray-100 dark:border-gray-800 hover:bg-blue-50/30 dark:hover:bg-blue-900/10 transition-colors cursor-pointer focus:outline-none focus:bg-blue-50/40 dark:focus:bg-blue-900/15"
                          >
                            <div className="flex-1 min-w-0">
                              <div className="flex items-center justify-between gap-2">
                                <span className="text-xs font-medium text-gray-800 dark:text-gray-100 truncate">{it.title}</span>
                                <span className="text-[10px] text-gray-400 dark:text-gray-500 flex-shrink-0">
                                  {formatRelative(it.createdAt)}
                                </span>
                              </div>
                              {it.preview && (
                                <p className="mt-0.5 text-[11px] text-gray-500 dark:text-gray-400 leading-snug line-clamp-2">
                                  {it.preview}
                                </p>
                              )}
                            </div>
                            {/* Secondary actions — visible on hover. The row
                               itself opens; these handle download + delete. */}
                            <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition-opacity flex-shrink-0">
                              <button
                                onClick={(e) => {
                                  e.stopPropagation();
                                  downloadByKind(it).catch(err => console.error('[library] download failed:', err));
                                }}
                                className="p-1 rounded text-gray-400 hover:text-emerald-600 dark:hover:text-emerald-400 hover:bg-emerald-50 dark:hover:bg-emerald-900/20"
                                title="Download"
                              >
                                <Download className="w-3.5 h-3.5" />
                              </button>
                              <button
                                onClick={(e) => { e.stopPropagation(); handleDelete(it); }}
                                className="p-1 rounded text-gray-400 hover:text-red-600 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20"
                                title="Delete"
                              >
                                <Trash2 className="w-3.5 h-3.5" />
                              </button>
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  )}
                </section>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};

function formatRelative(iso: string): string {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  if (!t) return '';
  const diff = Date.now() - t;
  if (diff < 60_000) return 'just now';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  if (diff < 604_800_000) return `${Math.floor(diff / 86_400_000)}d ago`;
  return new Date(iso).toLocaleDateString();
}
