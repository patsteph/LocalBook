import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { API_BASE_URL, localFetch } from '../../services/api';

/**
 * Model Browser — discovery, as opposed to the Locker's "what is already on disk".
 *
 * Answers three things at a glance for every model: where it came from (flag + vendor),
 * whether it fits THIS Mac (badge), and which role slots it could fill (chips). Sorting by
 * downloads / likes / recency, a details popup with the model card, and one-click download
 * with live progress.
 *
 * Every model is shown, labelled with where it came from. Filtering by origin is the user's
 * decision, offered as a control here rather than imposed by the backend — which is exactly
 * why the attribution has to be right: lineage comes from the model ARCHITECTURE, so a Qwen
 * fine-tune republished under another account still reads as Qwen.
 */

type Sort = 'trendingScore' | 'downloads' | 'likes' | 'lastModified' | 'createdAt';
type RoleFilter = '' | 'main' | 'fast' | 'vision' | 'embedding' | 'image';

/** Who published this checkpoint. The account is a fact; the country is only claimed when
 *  the account is one we actually know — there is no publisher-country signal in HF
 *  metadata, and guessing put a 🇨🇳 flag on a US company's model. */
interface Publisher {
  account: string; vendor: string; country: string; flag: string;
  known: boolean; repackager: boolean;
}
/** What the weights derive from — a separate question from who published them. */
interface Lineage {
  vendor: string; country: string; flag: string; org: string;
  source: '' | 'base_model' | 'architecture' | 'name'; known: boolean;
}
interface Origin {
  publisher: Publisher;
  lineage: Lineage;
  /** Publisher's flag when the account is known, else the lineage flag. Never a guess. */
  flag: string;
  allowed: boolean;
  /** Every country this model touches — what the origin filter matches on. */
  countries: string[];
  vendor: string; country: string; org: string;
  lab?: string;
  verified?: boolean;
}

const LINEAGE_SOURCE: Record<string, string> = {
  base_model: 'the base-model tag the publisher declared',
  architecture: 'the model architecture, which has to match the weights',
  name: 'a naming convention — the weakest signal',
};

function publisherTitle(p: Publisher): string {
  if (p.repackager) {
    return `${p.account} republishes other labs' weights (quantising, converting to MLX). `
      + `The account says nothing about who trained this model — see what it's based on.`;
  }
  if (p.known) return `Published by ${p.vendor}${p.country ? ` (${p.country})` : ''}.`;
  return `Published by the account “${p.account}”. No confirmed country for it, so none is shown.`;
}

function lineageTitle(l: Lineage): string {
  if (!l.known) return 'Could not establish what these weights derive from.';
  return `Weights derive from ${l.vendor}${l.country ? ` (${l.country})` : ''}, `
    + `resolved from ${LINEAGE_SOURCE[l.source] || 'available metadata'}.`;
}
interface Fit { verdict: 'fits' | 'tight' | 'over' | 'unknown'; needed_gb?: number; budget_gb?: number }
interface Caps { text: boolean; vision: boolean; embedding: boolean; image: boolean; audio: boolean }

interface CatalogModel {
  model_id: string;
  name: string;
  owner: string;
  downloads: number;
  likes: number;
  updated: string;
  created: string;
  gated: boolean;
  trending?: number;
  license: string;
  pipeline_tag: string;
  size_gb: number | null;
  size_is_estimate: boolean;
  capabilities: Caps;
  roles: string[];
  origin: Origin;
  installed: boolean;
  tags: string[];
  fit: Fit;
  readme?: string;
  url?: string;
  files?: string[];
}

interface Download { status: string; pct: number | null; downloaded_gb: number; total_gb: number; error?: string }

const SORTS: { id: Sort; label: string }[] = [
  // Trending first: it answers "what is worth looking at today", which is why someone opens
  // this tab. All-time downloads is dominated by long-lived embedders that never change.
  { id: 'trendingScore', label: '🔥 Trending' },
  { id: 'downloads',    label: 'Most downloaded' },
  { id: 'likes',        label: 'Highest rated' },
  { id: 'lastModified', label: 'Recently updated' },
  { id: 'createdAt',    label: 'Newly uploaded' },
];

type OriginFilter = '' | 'CN,AE' | 'CN' | 'AE';

const ORIGINS: { id: OriginFilter; label: string }[] = [
  { id: '',      label: 'All origins' },
  { id: 'CN,AE', label: 'Hide 🇨🇳 + 🇦🇪' },
  { id: 'CN',    label: 'Hide 🇨🇳' },
  { id: 'AE',    label: 'Hide 🇦🇪' },
];

const ROLES: { id: RoleFilter; label: string }[] = [
  { id: '',          label: 'All roles' },
  { id: 'main',      label: 'Main (chat)' },
  { id: 'fast',      label: 'Fast' },
  { id: 'vision',    label: 'Vision' },
  { id: 'embedding', label: 'Embeddings' },
  { id: 'image',     label: 'Image' },
];

const FIT_STYLE: Record<Fit['verdict'], { cls: string; label: string; title: string }> = {
  fits:    { cls: 'bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300',
             label: '✓ Fits', title: 'Comfortably within this Mac’s addressable GPU memory.' },
  tight:   { cls: 'bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300',
             label: '~ Tight', title: 'Fits, but with little headroom — expect pressure at long context.' },
  over:    { cls: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300',
             label: '✕ Too big', title: 'Larger than this Mac’s addressable GPU memory.' },
  unknown: { cls: 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-300',
             label: '? Unknown', title: 'This repo publishes no size metadata.' },
};

const ROLE_CHIP: Record<string, { label: string; cls: string }> = {
  main:      { label: 'Main',   cls: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300' },
  fast:      { label: 'Fast',   cls: 'bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-300' },
  vision:    { label: 'Vision', cls: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-300' },
  embedding: { label: 'Embed',  cls: 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300' },
  image:     { label: 'Image',  cls: 'bg-pink-100 text-pink-700 dark:bg-pink-900/30 dark:text-pink-300' },
};

function compact(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(0)}k`;
  return String(n);
}

function ago(iso: string): string {
  if (!iso) return '—';
  const d = (Date.now() - new Date(iso).getTime()) / 86_400_000;
  if (!isFinite(d)) return '—';
  if (d < 1) return 'today';
  if (d < 30) return `${Math.round(d)}d ago`;
  if (d < 365) return `${Math.round(d / 30)}mo ago`;
  return `${(d / 365).toFixed(1)}y ago`;
}

// Browse state lives OUTSIDE the component so switching tabs does not throw it away.
// LLMStudio unmounts the inactive tab, so a search, sort and scroll position were reset
// every time the user looked at the Locker and came back — losing their place halfway
// through a list. (User report, 2026-08-20.) Module scope is the right home: it is per
// session, needs no provider, and is deliberately NOT persisted — a stale result list on
// next launch would be worse than a fresh fetch.
const browseState: {
  sort: Sort; role: RoleFilter; fitsOnly: boolean; query: string; origin: OriginFilter;
  models: CatalogModel[]; blockedHidden: number; scrollTop: number; loaded: boolean;
} = {
  sort: 'trendingScore', role: '', fitsOnly: false, query: '', origin: '',
  models: [], blockedHidden: 0, scrollTop: 0, loaded: false,
};

export function ModelBrowser() {
  const [models, setModels] = useState<CatalogModel[]>(browseState.models);
  const [loading, setLoading] = useState(!browseState.loaded);
  const [offline, setOffline] = useState<string | null>(null);
  const [sort, setSort] = useState<Sort>(browseState.sort);
  const [role, setRole] = useState<RoleFilter>(browseState.role);
  const [fitsOnly, setFitsOnly] = useState(browseState.fitsOnly);
  const [query, setQuery] = useState(browseState.query);
  const [origin, setOrigin] = useState<OriginFilter>(browseState.origin);
  const [restricted, setRestricted] = useState(0);
  const [hiddenByFilter, setHiddenByFilter] = useState(0);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const [detail, setDetail] = useState<CatalogModel | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [downloads, setDownloads] = useState<Record<string, Download>>({});
  const debounce = useRef<number | undefined>(undefined);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const p = new URLSearchParams({
        sort, limit: '48',
        ...(query ? { q: query } : {}),
        ...(role ? { role } : {}),
        ...(fitsOnly ? { fits_only: 'true' } : {}),
        ...(origin ? { exclude_countries: origin } : {}),
      });
      const res = await localFetch(`${API_BASE_URL}/settings/catalog?${p}`);
      if (!res.ok) throw new Error(`${res.status}`);
      const data = await res.json();
      setOffline(data.offline ? (data.reason || 'Offline') : null);
      setModels(data.models || []);
      setRestricted(data.restricted_count || 0);
      setHiddenByFilter(data.hidden_by_filter || 0);
      browseState.models = data.models || [];
      browseState.blockedHidden = data.blocked_hidden || 0;
      browseState.loaded = true;
    } catch (e: any) {
      setOffline(e?.message ? `Could not load the catalog (${e.message})` : 'Could not load the catalog');
      setModels([]);
    } finally {
      setLoading(false);
    }
  }, [sort, role, fitsOnly, query, origin]);

  // Remember the controls so a remount restores them rather than snapping back to defaults.
  useEffect(() => {
    browseState.sort = sort;
    browseState.role = role;
    browseState.fitsOnly = fitsOnly;
    browseState.query = query;
    browseState.origin = origin;
  }, [sort, role, fitsOnly, query, origin]);

  const first = useRef(true);
  useEffect(() => {
    // Returning to the tab with results already in hand: restore them and the scroll
    // position instead of refetching, which would flash a spinner and jump to the top.
    if (first.current && browseState.loaded) {
      first.current = false;
      setLoading(false);
      requestAnimationFrame(() => {
        if (scrollRef.current) scrollRef.current.scrollTop = browseState.scrollTop;
      });
      return;
    }
    first.current = false;
    window.clearTimeout(debounce.current);
    debounce.current = window.setTimeout(load, query ? 350 : 0);
    return () => window.clearTimeout(debounce.current);
  }, [load, query]);

  // Poll download progress only while something is actually in flight.
  const anyActive = useMemo(
    () => Object.values(downloads).some((d) => d.status === 'downloading'),
    [downloads],
  );
  useEffect(() => {
    if (!anyActive) return;
    const t = window.setInterval(async () => {
      try {
        const r = await localFetch(`${API_BASE_URL}/settings/mlx/downloads`);
        if (r.ok) setDownloads(await r.json());
      } catch { /* transient */ }
    }, 1500);
    return () => window.clearInterval(t);
  }, [anyActive]);

  const startDownload = async (m: CatalogModel) => {
    setDownloads((d) => ({ ...d, [m.model_id]: { status: 'downloading', pct: null, downloaded_gb: 0, total_gb: 0 } }));
    try {
      const r = await localFetch(`${API_BASE_URL}/settings/catalog/download`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_id: m.model_id, tags: m.tags }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        setDownloads((d) => ({ ...d, [m.model_id]: { status: 'error', pct: null, downloaded_gb: 0, total_gb: 0, error: err.detail || `HTTP ${r.status}` } }));
      }
    } catch (e: any) {
      setDownloads((d) => ({ ...d, [m.model_id]: { status: 'error', pct: null, downloaded_gb: 0, total_gb: 0, error: e?.message } }));
    }
  };

  const openCard = async (m: CatalogModel) => {
    setDetail(m);
    setDetailLoading(true);
    try {
      const r = await localFetch(`${API_BASE_URL}/settings/catalog/card?model_id=${encodeURIComponent(m.model_id)}`);
      if (r.ok) {
        const full = await r.json();
        if (!full.error) setDetail(full);
      }
    } catch { /* keep the summary we already have */ } finally {
      setDetailLoading(false);
    }
  };

  return (
    <div
      className="p-4 space-y-3 max-h-[70vh] overflow-y-auto"
      ref={scrollRef}
      onScroll={(e) => { browseState.scrollTop = (e.target as HTMLDivElement).scrollTop; }}
    >
      {/* Controls */}
      <div className="flex flex-wrap items-center gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search Hugging Face for MLX models…"
          className="flex-1 min-w-[200px] px-3 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
        />
        <select
          value={sort}
          onChange={(e) => setSort(e.target.value as Sort)}
          className="px-2 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
        >
          {SORTS.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}
        </select>
        <select
          value={role}
          onChange={(e) => setRole(e.target.value as RoleFilter)}
          className="px-2 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
        >
          {ROLES.map((r) => <option key={r.id || 'all'} value={r.id}>{r.label}</option>)}
        </select>
        <select
          value={origin}
          onChange={(e) => setOrigin(e.target.value as OriginFilter)}
          title="Everything is shown by default. Set a standing rule here if you want certain origins hidden."
          className="px-2 py-1.5 text-sm rounded-lg border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
        >
          {ORIGINS.map((o) => <option key={o.id || 'all'} value={o.id}>{o.label}</option>)}
        </select>
        <label className="flex items-center gap-1.5 text-sm text-gray-600 dark:text-gray-300 select-none">
          <input type="checkbox" checked={fitsOnly} onChange={(e) => setFitsOnly(e.target.checked)} />
          Only what fits
        </label>
      </div>

      {(hiddenByFilter > 0 || restricted > 0) && (
        <div className="text-xs text-gray-500 dark:text-gray-400 px-1">
          {hiddenByFilter > 0
            ? `${hiddenByFilter} hidden by your origin filter.`
            : `${restricted} of these are from 🇨🇳/🇦🇪 origins — the flag on each card shows which.`}
        </div>
      )}

      {offline && (
        <div className="rounded-lg border border-amber-300 dark:border-amber-700 bg-amber-50 dark:bg-amber-900/20 p-3 text-sm text-amber-800 dark:text-amber-200">
          {offline} Models already downloaded still work — the browser is the only part that needs a connection.
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-12">
          <div className="animate-spin rounded-full h-7 w-7 border-b-2 border-blue-600" />
        </div>
      ) : models.length === 0 && !offline ? (
        <div className="text-center py-10 text-sm text-gray-500 dark:text-gray-400">
          Nothing matched. Try a different sort, role, or search term.
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-2">
          {models.map((m) => {
            const dl = downloads[m.model_id];
            const fit = FIT_STYLE[m.fit?.verdict ?? 'unknown'];
            return (
              <div
                key={m.model_id}
                className="rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-3 space-y-2"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <button
                      onClick={() => openCard(m)}
                      className="text-sm font-semibold text-gray-900 dark:text-white truncate hover:underline text-left"
                      title={m.model_id}
                    >
                      {m.name}
                    </button>
                    <div className="text-xs text-gray-500 dark:text-gray-400 truncate">
                      <span title={publisherTitle(m.origin.publisher)}>
                        {m.origin.publisher.known && m.origin.publisher.flag
                          ? `${m.origin.publisher.flag} ` : ''}
                        {m.origin.publisher.vendor}
                      </span>
                      {m.origin.lineage.known && (
                        <span className="ml-2" title={lineageTitle(m.origin.lineage)}>
                          ↳ based on {m.origin.lineage.vendor} {m.origin.lineage.flag}
                        </span>
                      )}
                      {m.license && <span className="ml-2">· {m.license}</span>}
                    </div>
                  </div>
                  <span className={`shrink-0 px-1.5 py-0.5 text-xs rounded font-medium ${fit.cls}`} title={fit.title}>
                    {fit.label}
                  </span>
                </div>

                <div className="flex flex-wrap items-center gap-1">
                  {m.roles.map((r) => (
                    <span key={r} className={`px-1.5 py-0.5 text-xs rounded ${ROLE_CHIP[r]?.cls || 'bg-gray-100 text-gray-600'}`}>
                      {ROLE_CHIP[r]?.label || r}
                    </span>
                  ))}
                  {m.gated && (
                    <span className="px-1.5 py-0.5 text-xs rounded bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300"
                          title="Requires accepting the licence on Hugging Face before it can be downloaded.">
                      🔒 Gated
                    </span>
                  )}
                </div>

                <div className="flex items-center justify-between text-xs text-gray-500 dark:text-gray-400">
                  <span>
                    {m.size_gb != null ? `${m.size_gb} GB` : 'size unknown'}
                    {m.size_gb != null && m.size_is_estimate && (
                      <span title="Computed from the checkpoint's published dtype breakdown; weights only.">
                        {' '}est.
                      </span>
                    )}
                    {' · '}↓{compact(m.downloads)}{' · '}♥{compact(m.likes)}
                    {sort === 'trendingScore' && m.trending ? ` · 🔥${m.trending}` : ''}
                    {' · '}{ago(m.updated)}
                  </span>
                  {m.installed ? (
                    <span className="text-emerald-600 dark:text-emerald-400 font-medium">✓ Downloaded</span>
                  ) : dl?.status === 'downloading' ? (
                    <span className="text-blue-600 dark:text-blue-400 tabular-nums">
                      {dl.pct != null ? `${dl.pct}%` : 'starting…'}
                      {dl.total_gb ? ` · ${dl.downloaded_gb}/${dl.total_gb} GB` : ''}
                    </span>
                  ) : dl?.status === 'error' ? (
                    <span className="text-red-600 dark:text-red-400" title={dl.error}>failed</span>
                  ) : (
                    <button
                      onClick={() => startDownload(m)}
                      title={m.fit?.verdict === 'over'
                        ? 'Bigger than this Mac can address — it will download, but expect it not to run here. Your call.'
                        : 'Download to this Mac'}
                      className={`px-2 py-0.5 rounded font-medium text-white ${
                        m.fit?.verdict === 'over'
                          ? 'bg-gray-500 hover:bg-gray-600'
                          : 'bg-blue-600 hover:bg-blue-700'
                      }`}
                    >
                      ⬇ Get
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}

      {detail && (
        <ModelCard
          model={detail}
          loading={detailLoading}
          onClose={() => setDetail(null)}
          onDownload={() => startDownload(detail)}
          download={downloads[detail.model_id]}
        />
      )}
    </div>
  );
}

function ModelCard({ model, loading, onClose, onDownload, download }: {
  model: CatalogModel; loading: boolean; onClose: () => void;
  onDownload: () => void; download?: Download;
}) {
  const fit = FIT_STYLE[model.fit?.verdict ?? 'unknown'];
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4" onClick={onClose}>
      <div
        className="bg-white dark:bg-gray-800 rounded-xl shadow-xl max-w-2xl w-full max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-3 p-4 border-b border-gray-200 dark:border-gray-700">
          <div className="min-w-0">
            <h3 className="text-base font-semibold text-gray-900 dark:text-white truncate">{model.name}</h3>
            <p className="text-xs text-gray-500 dark:text-gray-400 truncate">{model.model_id}</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 text-xl leading-none">×</button>
        </div>

        <div className="p-4 space-y-3 overflow-y-auto">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs">
            <Stat
              label="Published by"
              value={`${model.origin.publisher.known && model.origin.publisher.flag
                ? `${model.origin.publisher.flag} ` : ''}${model.origin.publisher.vendor}`}
            />
            <Stat
              label="Based on"
              value={model.origin.lineage.known
                ? `${model.origin.lineage.flag} ${model.origin.lineage.vendor}`
                : 'not established'}
            />
            <Stat label="Size" value={model.size_gb != null ? `${model.size_gb} GB` : 'unknown'} />
            <Stat label="Downloads" value={compact(model.downloads)} />
            <Stat label="Likes" value={compact(model.likes)} />
            <Stat label="Updated" value={ago(model.updated)} />
            <Stat label="Created" value={ago(model.created)} />
            <Stat label="Licence" value={model.license || '—'} />
            <Stat label="Pipeline" value={model.pipeline_tag || '—'} />
          </div>

          <div className="flex flex-wrap items-center gap-1.5">
            <span className={`px-2 py-0.5 text-xs rounded font-medium ${fit.cls}`} title={fit.title}>{fit.label}</span>
            {model.fit?.needed_gb != null && model.fit?.budget_gb != null && (
              <span className="text-xs text-gray-500 dark:text-gray-400">
                needs ~{model.fit.needed_gb} GB of {model.fit.budget_gb} GB addressable
              </span>
            )}
          </div>

          <div className="flex flex-wrap gap-1">
            {model.roles.map((r) => (
              <span key={r} className={`px-1.5 py-0.5 text-xs rounded ${ROLE_CHIP[r]?.cls || 'bg-gray-100 text-gray-600'}`}>
                {ROLE_CHIP[r]?.label || r}
              </span>
            ))}
            {model.tags?.slice(0, 8).map((t) => (
              <span key={t} className="px-1.5 py-0.5 text-xs rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">{t}</span>
            ))}
          </div>

          {loading ? (
            <div className="text-xs text-gray-500 dark:text-gray-400">Loading model card…</div>
          ) : model.readme ? (
            <pre className="text-xs whitespace-pre-wrap font-sans text-gray-700 dark:text-gray-300 bg-gray-50 dark:bg-gray-900/50 rounded-lg p-3 max-h-64 overflow-y-auto">
              {model.readme.slice(0, 6000)}
            </pre>
          ) : (
            <div className="text-xs text-gray-500 dark:text-gray-400">No model card published.</div>
          )}
        </div>

        <div className="flex items-center justify-between gap-2 p-4 border-t border-gray-200 dark:border-gray-700">
          {model.url && (
            <a href={model.url} target="_blank" rel="noreferrer"
               className="text-xs text-blue-600 dark:text-blue-400 hover:underline">View on Hugging Face ↗</a>
          )}
          {model.installed ? (
            <span className="text-sm text-emerald-600 dark:text-emerald-400 font-medium">✓ Downloaded</span>
          ) : download?.status === 'downloading' ? (
            <span className="text-sm text-blue-600 dark:text-blue-400 tabular-nums">
              {download.pct != null ? `${download.pct}%` : 'starting…'}
            </span>
          ) : (
            <button
              onClick={onDownload}
              className={`px-3 py-1.5 text-sm rounded-lg font-medium text-white ${
                model.fit?.verdict === 'over'
                  ? 'bg-gray-500 hover:bg-gray-600'
                  : 'bg-blue-600 hover:bg-blue-700'
              }`}
              title={model.fit?.verdict === 'over'
                ? 'Bigger than this Mac can address — it will download, but expect it not to run here. Your call.'
                : 'Download to this Mac'}
            >
              ⬇ Download
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-gray-400 dark:text-gray-500 uppercase tracking-wide">{label}</div>
      <div className="text-gray-800 dark:text-gray-200 truncate" title={value}>{value}</div>
    </div>
  );
}
