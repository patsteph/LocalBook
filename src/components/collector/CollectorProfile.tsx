import React, { useState, useEffect, useCallback } from 'react';
import { collectorService } from '../../services/collector';
import { openUrl } from '@tauri-apps/plugin-opener';

// ─── Types ───────────────────────────────────────────────────────────────────

interface SubjectInfo {
  name: string;
  ticker?: string;
  industry?: string;
  sector?: string;
  website?: string;
  investor_relations?: string;
  news_page?: string;
  competitors: string[];
  key_people: string[];
}

interface StockQuote {
  ticker: string;
  price: number;
  change: number;
  change_percent: number;
  currency: string;
  market_state: string;
  previous_close: number;
  open: number;
  day_high: number;
  day_low: number;
  volume: number;
  market_cap?: string;
  fifty_two_week_high: number;
  fifty_two_week_low: number;
  name: string;
  exchange: string;
}

interface KeyDate {
  date: string;
  event: string;
  category: string;
  importance: string;
  source: string;
}

interface SourceItem {
  id: string;
  name: string;
  url?: string;
  type: string;
  enabled: boolean;
  health: string;
  items_collected: number;
  avg_response_ms?: number;
}

interface CollectionStats {
  total_runs: number;
  total_items_found: number;
  total_items_approved: number;
  total_items_rejected: number;
  total_items_pending: number;
  avg_items_per_run: number;
  last_collection?: string;
  first_collection?: string;
  avg_duration_ms: number;
  success_rate: number;
}

interface HistoryEntry {
  timestamp: string;
  items_found: number;
  items_approved: number;
  items_pending: number;
  items_rejected: number;
  sources_checked: number;
  duration_ms: number;
  trigger: string;
  error?: string;
}

interface FeedbackInsight {
  type: string;
  icon: string;
  message: string;
  level?: string;
}

interface FeedbackData {
  insights: FeedbackInsight[];
  preferred_topics: string[];
  preferred_sources: string[];
  approval_rate: number;
  capture_count: number;
  highlight_count: number;
}

interface ProfileData {
  subject: SubjectInfo;
  stock?: StockQuote;
  key_dates: KeyDate[];
  sources: SourceItem[];
  focus_areas: string[];
  excluded_topics: string[];
  schedule: { frequency: string; max_items_per_run: number };
  filters: { max_age_days: number; min_relevance: number; language: string };
  settings: { collection_mode: string; approval_mode: string; name: string };
  stats: CollectionStats;
  feedback: FeedbackData;
  created_at: string;
  updated_at: string;
}

interface CollectorProfileProps {
  notebookId: string;
  isOpen: boolean;
  onClose: () => void;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

const HEALTH_ICONS: Record<string, string> = {
  healthy: '🟢',
  degraded: '🟡',
  failing: '🟠',
  dead: '🔴',
  unknown: '⚪',
};

const SOURCE_TYPE_LABELS: Record<string, string> = {
  rss: 'RSS Feed',
  web: 'Web Page',
  news_keyword: 'News Search',
};

const CATEGORY_COLORS: Record<string, string> = {
  earnings: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400',
  meeting: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400',
  conference: 'bg-purple-100 text-purple-700 dark:bg-purple-900/30 dark:text-purple-400',
  product: 'bg-orange-100 text-orange-700 dark:bg-orange-900/30 dark:text-orange-400',
  regulatory: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400',
};

const MODE_LABELS: Record<string, string> = {
  manual: 'Conservative',
  hybrid: 'Balanced',
  automatic: 'Aggressive',
  show_me: 'Manual Review',
  mixed: 'Auto + Review',
  trust_me: 'Full Automatic',
};

function timeAgo(iso: string): string {
  if (!iso) return 'Never';
  const now = Date.now();
  const then = new Date(iso).getTime();
  const diff = now - then;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return 'Just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

function daysUntil(iso: string): string {
  if (!iso || iso === 'TBD') return '';
  const now = new Date();
  const target = new Date(iso);
  const diff = Math.ceil((target.getTime() - now.getTime()) / 86400000);
  if (diff < 0) return `${Math.abs(diff)}d ago`;
  if (diff === 0) return 'Today';
  if (diff === 1) return 'Tomorrow';
  if (diff <= 7) return `${diff} days`;
  if (diff <= 30) return `${Math.ceil(diff / 7)} weeks`;
  return `${Math.ceil(diff / 30)} months`;
}

// ─── Component ───────────────────────────────────────────────────────────────

export const CollectorProfile: React.FC<CollectorProfileProps> = ({
  notebookId,
  isOpen,
  onClose,
}) => {
  const [profile, setProfile] = useState<ProfileData | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState<'overview' | 'sources' | 'history' | 'insights'>('overview');
  const [togglingSource, setTogglingSource] = useState<string | null>(null);

  const loadProfile = useCallback(async () => {
    if (!notebookId) return;
    setLoading(true);
    setError(null);

    try {
      const [profileData, historyData] = await Promise.all([
        collectorService.getProfile(notebookId),
        collectorService.getHistory(notebookId, 15),
      ]);

      setProfile(profileData);
      setHistory(historyData.history || []);
    } catch (err) {
      setError('Connection error');
    } finally {
      setLoading(false);
    }
  }, [notebookId]);

  useEffect(() => {
    if (isOpen) loadProfile();
  }, [isOpen, loadProfile]);

  const handleToggleSource = async (sourceId: string, enabled: boolean) => {
    setTogglingSource(sourceId);
    try {
      await collectorService.toggleSource(notebookId, sourceId, enabled);
      if (profile) {
        setProfile({
          ...profile,
          sources: profile.sources.map((s) =>
            s.id === sourceId ? { ...s, enabled } : s
          ),
        });
      }
    } catch {
      // silent fail
    } finally {
      setTogglingSource(null);
    }
  };

  if (!isOpen) return null;

  // ─── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
      <div className="bg-white dark:bg-gray-800 rounded-xl max-w-3xl w-full mx-4 max-h-[90vh] flex flex-col shadow-2xl">
        {/* Header */}
        <div className="p-4 border-b dark:border-gray-700 flex items-center justify-between shrink-0">
          <div className="flex items-center gap-3">
            <span className="text-2xl">📊</span>
            <div>
              <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">
                {profile?.subject.name || 'Collector'} Profile
              </h2>
              {profile?.subject.ticker && (
                <span className="text-sm text-gray-500 dark:text-gray-400">
                  {profile.subject.ticker} · {profile.subject.industry || profile.subject.sector || ''}
                </span>
              )}
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-2 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg text-gray-500 dark:text-gray-400"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Tab Bar */}
        <div className="flex border-b dark:border-gray-700 px-4 shrink-0">
          {(['overview', 'sources', 'history', 'insights'] as const).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className={`px-4 py-2.5 text-sm font-medium border-b-2 transition-colors capitalize ${
                activeTab === tab
                  ? 'border-blue-500 text-blue-600 dark:text-blue-400'
                  : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300'
              }`}
            >
              {tab === 'overview' && '🏢 '}
              {tab === 'sources' && '📡 '}
              {tab === 'history' && '📈 '}
              {tab === 'insights' && '🧠 '}
              {tab}
            </button>
          ))}
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <svg className="animate-spin h-8 w-8 text-blue-500" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
              </svg>
            </div>
          ) : error ? (
            <div className="text-center py-8 text-red-500 dark:text-red-400">{error}</div>
          ) : profile ? (
            <>
              {activeTab === 'overview' && <OverviewTab profile={profile} />}
              {activeTab === 'sources' && (
                <SourcesTab
                  sources={profile.sources}
                  onToggle={handleToggleSource}
                  togglingSource={togglingSource}
                />
              )}
              {activeTab === 'history' && <HistoryTab history={history} stats={profile.stats} />}
              {activeTab === 'insights' && <InsightsTab feedback={profile.feedback} />}
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
};

// ─── Overview Tab ────────────────────────────────────────────────────────────

const OverviewTab: React.FC<{ profile: ProfileData }> = ({ profile }) => {
  const { subject, stock, key_dates, focus_areas, schedule, filters, settings, stats } = profile;

  return (
    <div className="space-y-5">
      {/* Subject Card + Stock */}
      <div className="bg-gradient-to-r from-blue-50 to-indigo-50 dark:from-blue-900/20 dark:to-indigo-900/20 rounded-xl p-4 border border-blue-100 dark:border-blue-800/40">
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <h3 className="text-xl font-bold text-gray-900 dark:text-gray-100">
              {subject.name}
            </h3>
            <div className="flex flex-wrap items-center gap-2 mt-1">
              {subject.ticker && (
                <span className="text-sm font-mono bg-gray-200 dark:bg-gray-700 text-gray-700 dark:text-gray-300 px-2 py-0.5 rounded">
                  {subject.ticker}
                </span>
              )}
              {subject.industry && (
                <span className="text-sm text-gray-600 dark:text-gray-400">{subject.industry}</span>
              )}
              {subject.sector && subject.sector !== subject.industry && (
                <span className="text-sm text-gray-500 dark:text-gray-500">· {subject.sector}</span>
              )}
            </div>

            {/* Links — open in system default browser */}
            <div className="flex gap-3 mt-2">
              {subject.website && (
                <button
                  onClick={() => openUrl(subject.website!).catch(() => window.open(subject.website, '_blank'))}
                  className="text-xs text-blue-600 dark:text-blue-400 hover:underline cursor-pointer bg-transparent border-none p-0">
                  Website ↗
                </button>
              )}
              {subject.investor_relations && (
                <button
                  onClick={() => openUrl(subject.investor_relations!).catch(() => window.open(subject.investor_relations, '_blank'))}
                  className="text-xs text-blue-600 dark:text-blue-400 hover:underline cursor-pointer bg-transparent border-none p-0">
                  Investor Relations ↗
                </button>
              )}
              {subject.news_page && (
                <button
                  onClick={() => openUrl(subject.news_page!).catch(() => window.open(subject.news_page, '_blank'))}
                  className="text-xs text-blue-600 dark:text-blue-400 hover:underline cursor-pointer bg-transparent border-none p-0">
                  News ↗
                </button>
              )}
            </div>

            {/* Key People */}
            {subject.key_people.length > 0 && (
              <div className="mt-2 text-xs text-gray-600 dark:text-gray-400">
                <span className="font-medium">Key People:</span> {subject.key_people.join(', ')}
              </div>
            )}

            {/* Competitors */}
            {subject.competitors.length > 0 && (
              <div className="mt-1 text-xs text-gray-600 dark:text-gray-400">
                <span className="font-medium">Competitors:</span> {subject.competitors.join(', ')}
              </div>
            )}
          </div>

          {/* Stock Quote */}
          {stock && (
            <div className="text-right ml-4 shrink-0">
              <div className="text-2xl font-bold text-gray-900 dark:text-gray-100">
                ${stock.price.toFixed(2)}
              </div>
              <div className={`text-sm font-medium ${stock.change >= 0 ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'}`}>
                {stock.change >= 0 ? '+' : ''}{stock.change.toFixed(2)} ({stock.change_percent >= 0 ? '+' : ''}{stock.change_percent.toFixed(2)}%)
              </div>
              <div className="text-xs text-gray-500 dark:text-gray-500 mt-1">
                {stock.market_state === 'REGULAR' ? '🟢 Market Open' :
                 stock.market_state === 'PRE' ? '🟡 Pre-Market' :
                 stock.market_state === 'POST' ? '🟡 After-Hours' : '⚪ Market Closed'}
              </div>
              <div className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
                {stock.exchange} · Vol: {(stock.volume / 1e6).toFixed(1)}M
              </div>
              {stock.market_cap && (
                <div className="text-xs text-gray-400 dark:text-gray-500">
                  MCap: {stock.market_cap}
                </div>
              )}
              <div className="text-xs text-gray-400 dark:text-gray-500 mt-1">
                52w: ${stock.fifty_two_week_low.toFixed(0)} – ${stock.fifty_two_week_high.toFixed(0)}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Collection Settings Card */}
      <div className="grid grid-cols-2 gap-3">
        <div className="bg-gray-50 dark:bg-gray-900/50 rounded-lg p-3 border border-gray-200 dark:border-gray-700">
          <div className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">Collection Mode</div>
          <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 mt-1">
            {MODE_LABELS[settings.collection_mode] || settings.collection_mode}
          </div>
        </div>
        <div className="bg-gray-50 dark:bg-gray-900/50 rounded-lg p-3 border border-gray-200 dark:border-gray-700">
          <div className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">Approval Mode</div>
          <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 mt-1">
            {MODE_LABELS[settings.approval_mode] || settings.approval_mode}
          </div>
        </div>
        <div className="bg-gray-50 dark:bg-gray-900/50 rounded-lg p-3 border border-gray-200 dark:border-gray-700">
          <div className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">Check Frequency</div>
          <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 mt-1 capitalize">
            {schedule.frequency} · Max {schedule.max_items_per_run}/run
          </div>
        </div>
        <div className="bg-gray-50 dark:bg-gray-900/50 rounded-lg p-3 border border-gray-200 dark:border-gray-700">
          <div className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide">Filters</div>
          <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 mt-1">
            {filters.max_age_days}d max · {(filters.min_relevance * 100).toFixed(0)}% relevance
          </div>
        </div>
      </div>

      {/* Focus Areas */}
      {focus_areas.length > 0 && (
        <div>
          <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Focus Areas</h4>
          <div className="flex flex-wrap gap-2">
            {focus_areas.map((area, i) => (
              <span key={i} className="px-3 py-1 bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 rounded-full text-sm">
                {subject.name && !area.toLowerCase().includes(subject.name.toLowerCase())
                  ? `${subject.name} ${area}`
                  : area}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Quick Stats */}
      {stats.total_runs > 0 && (
        <div className="grid grid-cols-4 gap-2">
          <StatBox label="Runs" value={stats.total_runs} />
          <StatBox label="Found" value={stats.total_items_found} />
          <StatBox label="Approved" value={stats.total_items_approved} color="green" />
          <StatBox label="Filtered" value={stats.total_items_rejected} color="gray" />
        </div>
      )}

      {/* Key Dates */}
      {key_dates.length > 0 && (
        <div>
          <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Key Dates</h4>
          <div className="space-y-2">
            {key_dates.map((kd, i) => {
              const isPast = kd.date !== 'TBD' && new Date(kd.date) < new Date();
              return (
                <div key={i} className={`flex items-center gap-3 p-2 rounded-lg border ${
                  isPast
                    ? 'border-gray-200 dark:border-gray-700 opacity-60'
                    : 'border-gray-200 dark:border-gray-700'
                }`}>
                  <div className="text-center w-14 shrink-0">
                    <div className="text-xs font-bold text-gray-900 dark:text-gray-100">
                      {kd.date !== 'TBD' ? new Date(kd.date).toLocaleDateString('en-US', { month: 'short' }) : ''}
                    </div>
                    <div className="text-lg font-bold text-gray-900 dark:text-gray-100">
                      {kd.date !== 'TBD' ? new Date(kd.date).getDate() : 'TBD'}
                    </div>
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">{kd.event}</div>
                    <div className="flex items-center gap-2 mt-0.5">
                      <span className={`text-xs px-1.5 py-0.5 rounded ${CATEGORY_COLORS[kd.category] || 'bg-gray-100 text-gray-600 dark:bg-gray-700 dark:text-gray-400'}`}>
                        {kd.category}
                      </span>
                      {kd.source === 'sec' && (
                        <span className="text-xs text-gray-400">SEC</span>
                      )}
                    </div>
                  </div>
                  <div className="text-xs text-gray-500 dark:text-gray-400 shrink-0">
                    {daysUntil(kd.date)}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
};

// ─── Sources Tab ─────────────────────────────────────────────────────────────

const SourcesTab: React.FC<{
  sources: SourceItem[];
  onToggle: (id: string, enabled: boolean) => void;
  togglingSource: string | null;
}> = ({ sources, onToggle, togglingSource }) => {
  const enabled = sources.filter((s) => s.enabled);
  const disabled = sources.filter((s) => !s.enabled);

  const renderSourceRow = (source: SourceItem) => (
    <div
      key={source.id}
      className={`flex items-center gap-3 p-3 rounded-lg border transition-colors ${
        source.enabled
          ? 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800'
          : 'border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/50 opacity-60'
      }`}
    >
      <span className="text-sm" title={source.health}>
        {HEALTH_ICONS[source.health] || '⚪'}
      </span>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">
            {source.name}
          </span>
          <span className="text-xs text-gray-400 dark:text-gray-500">
            {SOURCE_TYPE_LABELS[source.type] || source.type}
          </span>
        </div>
        {source.url && (
          <div className="text-xs text-gray-400 dark:text-gray-500 truncate">{source.url}</div>
        )}
        {source.items_collected > 0 && (
          <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
            {source.items_collected} items collected
            {source.avg_response_ms ? ` · ${Math.round(source.avg_response_ms)}ms avg` : ''}
          </div>
        )}
      </div>

      {/* Toggle */}
      <button
        onClick={() => onToggle(source.id, !source.enabled)}
        disabled={togglingSource === source.id}
        className={`relative w-10 h-5 rounded-full transition-colors shrink-0 ${
          source.enabled
            ? 'bg-blue-500'
            : 'bg-gray-300 dark:bg-gray-600'
        } ${togglingSource === source.id ? 'opacity-50' : ''}`}
      >
        <span
          className={`absolute top-[2px] left-[2px] w-4 h-4 bg-white rounded-full transition-transform shadow ${
            source.enabled ? 'translate-x-5' : 'translate-x-0'
          }`}
        />
      </button>
    </div>
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300">
          Monitored Sources ({enabled.length} active)
        </h4>
        <span className="text-xs text-gray-400 dark:text-gray-500">
          {sources.length} total
        </span>
      </div>

      {sources.length === 0 ? (
        <div className="text-center py-8 text-gray-500 dark:text-gray-400">
          <p>No sources configured yet.</p>
          <p className="text-sm mt-1">Run the Collector wizard to discover sources.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {enabled.map(renderSourceRow)}
          {disabled.length > 0 && (
            <>
              <div className="text-xs text-gray-400 dark:text-gray-500 pt-2 border-t dark:border-gray-700">
                Paused ({disabled.length})
              </div>
              {disabled.map(renderSourceRow)}
            </>
          )}
        </div>
      )}
    </div>
  );
};

// ─── History Tab ─────────────────────────────────────────────────────────────

const HistoryTab: React.FC<{ history: HistoryEntry[]; stats: CollectionStats }> = ({ history, stats }) => {
  return (
    <div className="space-y-5">
      {/* Stats Summary */}
      {stats.total_runs > 0 && (
        <div className="grid grid-cols-3 gap-3">
          <StatBox label="Total Runs" value={stats.total_runs} />
          <StatBox label="Items Found" value={stats.total_items_found} />
          <StatBox label="Success Rate" value={`${stats.success_rate}%`} color={stats.success_rate > 80 ? 'green' : 'yellow'} />
          <StatBox label="Approved" value={stats.total_items_approved} color="green" />
          <StatBox label="Pending" value={stats.total_items_pending} color="blue" />
          <StatBox label="Avg Duration" value={`${(stats.avg_duration_ms / 1000).toFixed(1)}s`} />
        </div>
      )}

      {/* Timeline */}
      <div>
        <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">Collection Timeline</h4>
        {history.length === 0 ? (
          <div className="text-center py-8 text-gray-500 dark:text-gray-400">
            <p>No collection runs yet.</p>
            <p className="text-sm mt-1">Click "Collect Now" to start.</p>
          </div>
        ) : (
          <div className="space-y-1">
            {history.map((entry, i) => (
              <div key={i} className="flex items-center gap-3 p-2 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-700/50">
                {/* Timeline dot */}
                <div className="flex flex-col items-center w-3">
                  <div className={`w-2.5 h-2.5 rounded-full ${
                    entry.error ? 'bg-red-400' :
                    entry.items_found > 0 ? 'bg-green-400' : 'bg-gray-300 dark:bg-gray-600'
                  }`} />
                  {i < history.length - 1 && <div className="w-px h-6 bg-gray-200 dark:bg-gray-700" />}
                </div>

                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-gray-900 dark:text-gray-100">
                      {entry.items_found} items
                    </span>
                    {entry.items_approved > 0 && (
                      <span className="text-xs text-green-600 dark:text-green-400">
                        +{entry.items_approved} approved
                      </span>
                    )}
                    {entry.items_pending > 0 && (
                      <span className="text-xs text-blue-600 dark:text-blue-400">
                        {entry.items_pending} pending
                      </span>
                    )}
                    {entry.error && (
                      <span className="text-xs text-red-500">Error</span>
                    )}
                  </div>
                  <div className="text-xs text-gray-400 dark:text-gray-500">
                    {entry.trigger} · {entry.sources_checked} sources · {(entry.duration_ms / 1000).toFixed(1)}s
                  </div>
                </div>

                <div className="text-xs text-gray-400 dark:text-gray-500 shrink-0">
                  {timeAgo(entry.timestamp)}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

// ─── Insights Tab ────────────────────────────────────────────────────────────

const InsightsTab: React.FC<{ feedback: FeedbackData }> = ({ feedback }) => {
  const hasData = feedback.insights.length > 0 || feedback.preferred_topics.length > 0;

  return (
    <div className="space-y-5">
      <div className="flex items-center gap-2 mb-2">
        <span className="text-lg">🧠</span>
        <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300">
          What I've Learned From Your Feedback
        </h4>
      </div>

      {!hasData ? (
        <div className="text-center py-8 bg-gray-50 dark:bg-gray-900/50 rounded-xl border border-gray-200 dark:border-gray-700">
          <span className="text-3xl mb-3 block">📝</span>
          <p className="text-gray-600 dark:text-gray-400 font-medium">No feedback data yet</p>
          <p className="text-sm text-gray-500 dark:text-gray-500 mt-1 max-w-xs mx-auto">
            As you approve, reject, capture, and highlight content, the Collector learns your preferences and adapts.
          </p>
        </div>
      ) : (
        <>
          {/* Insight Cards */}
          {feedback.insights.length > 0 && (
            <div className="space-y-2">
              {feedback.insights.map((insight, i) => (
                <div key={i} className={`flex items-start gap-3 p-3 rounded-lg border ${
                  insight.type === 'approval_rate'
                    ? insight.level === 'high'
                      ? 'border-green-200 dark:border-green-800/40 bg-green-50 dark:bg-green-900/20'
                      : insight.level === 'medium'
                        ? 'border-yellow-200 dark:border-yellow-800/40 bg-yellow-50 dark:bg-yellow-900/20'
                        : 'border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/50'
                    : 'border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/50'
                }`}>
                  <span className="text-lg shrink-0">
                    {insight.icon === 'trending_up' ? '📈' :
                     insight.icon === 'star' ? '⭐' :
                     insight.icon === 'activity' ? '📥' :
                     insight.icon === 'highlight' ? '🖍️' :
                     insight.icon === 'check_circle' ? '✅' : '💡'}
                  </span>
                  <p className="text-sm text-gray-700 dark:text-gray-300">{insight.message}</p>
                </div>
              ))}
            </div>
          )}

          {/* Preferred Topics */}
          {feedback.preferred_topics.length > 0 && (
            <div>
              <h5 className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">
                Topics You Engage With Most
              </h5>
              <div className="flex flex-wrap gap-2">
                {feedback.preferred_topics.map((topic, i) => (
                  <span key={i} className="px-3 py-1 bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300 rounded-full text-sm border border-blue-200 dark:border-blue-800/40">
                    {topic}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Preferred Sources */}
          {feedback.preferred_sources.length > 0 && (
            <div>
              <h5 className="text-xs font-medium text-gray-500 dark:text-gray-400 uppercase tracking-wide mb-2">
                Your Trusted Sources
              </h5>
              <div className="flex flex-wrap gap-2">
                {feedback.preferred_sources.map((source, i) => (
                  <span key={i} className="px-3 py-1 bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-300 rounded-full text-sm border border-green-200 dark:border-green-800/40">
                    {source}
                  </span>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
};

// ─── Shared Components ───────────────────────────────────────────────────────

const StatBox: React.FC<{
  label: string;
  value: string | number;
  color?: 'green' | 'blue' | 'yellow' | 'gray';
}> = ({ label, value, color }) => {
  const colorClasses = {
    green: 'text-green-600 dark:text-green-400',
    blue: 'text-blue-600 dark:text-blue-400',
    yellow: 'text-yellow-600 dark:text-yellow-400',
    gray: 'text-gray-500 dark:text-gray-400',
  };

  return (
    <div className="bg-gray-50 dark:bg-gray-900/50 rounded-lg p-3 border border-gray-200 dark:border-gray-700 text-center">
      <div className={`text-xl font-bold ${color ? colorClasses[color] : 'text-gray-900 dark:text-gray-100'}`}>
        {value}
      </div>
      <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">{label}</div>
    </div>
  );
};

export default CollectorProfile;
