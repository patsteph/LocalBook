/**
 * CorrespondentHotClustersRenderer — interactive cards for hot/cold
 * article clusters.
 *
 * Phase 2 (2026-06-09). Per C.1 decision (locked): cards default, with
 * built-in Deep-read CTA that fires @research deep_dive and a
 * Show-articles CTA that fires @correspondent show cluster <label>.
 *
 * Sender-diversity gate (per design C tradeoff): the Deep-read CTA is
 * disabled if fewer than 3 unique senders contribute to the cluster
 * (one prolific sender's repeated topic isn't a true "hot" trend).
 */
import React from 'react';
import { TrendingUp, TrendingDown, FileText, Telescope } from 'lucide-react';
import type { RendererProps } from '../../../types/artifact';

interface HotClusterItem {
  label: string;
  size: number;
  recent_size: number;
  baseline_size: number;
  delta: number;
  sender_count: number;
  notebook_count: number;
  sample_senders: string[];
}

interface HotClustersPayload {
  polarity: 'hot' | 'cold';
  items: HotClusterItem[];
}

export const CorrespondentHotClustersRenderer: React.FC<RendererProps<HotClustersPayload>> = ({ artifact }) => {
  const payload = artifact.payload || ({} as HotClustersPayload);
  const polarity = payload.polarity || 'hot';
  const items = payload.items || [];

  const fireChat = (text: string) => {
    window.dispatchEvent(new CustomEvent('lb:chatPrompt', { detail: { text } }));
  };

  if (items.length === 0) {
    return (
      <div className="not-prose my-3 p-4 rounded-lg border border-orange-200 dark:border-orange-800 bg-orange-50/40 dark:bg-orange-900/10">
        <p className="text-sm text-orange-700 dark:text-orange-300">
          No {polarity} clusters right now.
        </p>
      </div>
    );
  }

  const headerLabel = polarity === 'hot' ? 'Trending up' : 'Cooling off';
  const HeaderIcon = polarity === 'hot' ? TrendingUp : TrendingDown;

  return (
    <div className="not-prose my-3 space-y-2">
      <div className="flex items-center gap-2 text-xs font-semibold text-orange-700 dark:text-orange-300 uppercase tracking-wide">
        <HeaderIcon className="w-3.5 h-3.5" />
        {headerLabel} · {items.length}
      </div>
      {items.map((c, idx) => {
        const senderDiversityOK = c.sender_count >= 3;
        return (
          <div
            key={`${idx}-${c.label}`}
            className="rounded-lg border border-gray-200 dark:border-gray-700 p-3 bg-white dark:bg-gray-800"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-gray-800 dark:text-gray-100">
                  {c.label}
                </p>
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                  {c.size} article{c.size === 1 ? '' : 's'} · {c.sender_count} sender{c.sender_count === 1 ? '' : 's'} · {c.notebook_count} notebook{c.notebook_count === 1 ? '' : 's'}
                </p>
                {c.sample_senders.length > 0 && (
                  <p className="text-xs text-gray-400 dark:text-gray-500 mt-1 truncate">
                    incl. {c.sample_senders.join(', ')}
                  </p>
                )}
                <div className="flex items-center gap-3 mt-2 text-xs">
                  <span className="text-gray-600 dark:text-gray-300">
                    <span className="font-semibold">{c.recent_size}</span> last 7d
                  </span>
                  <span className="text-gray-400">·</span>
                  <span className="text-gray-600 dark:text-gray-300">
                    <span className="font-semibold">{c.baseline_size}</span> prior 7d
                  </span>
                  <span className="text-gray-400">·</span>
                  <span className={`font-semibold ${c.delta >= 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-amber-600 dark:text-amber-400'}`}>
                    {c.delta >= 0 ? '↑' : '↓'} {Math.abs(c.delta)}
                  </span>
                </div>
                {!senderDiversityOK && polarity === 'hot' && (
                  <p className="text-[10px] text-gray-400 dark:text-gray-500 mt-1.5 italic">
                    Low sender diversity — could be one source over-amplifying. Verify before deep-reading.
                  </p>
                )}
              </div>
              <div className="flex flex-col gap-1 flex-shrink-0">
                <button
                  onClick={() => fireChat(`@research deep dive ${c.label}`)}
                  disabled={!senderDiversityOK && polarity === 'hot'}
                  className="px-2 py-1 text-xs rounded-lg bg-emerald-600 hover:bg-emerald-700 text-white disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-1"
                  title={senderDiversityOK
                    ? 'Kick off a multi-hop research deep dive on this theme'
                    : 'Sender diversity too low — read first before deep dive'}
                >
                  <Telescope className="w-3 h-3" /> Deep read
                </button>
                <button
                  onClick={() => fireChat(`@correspondent show articles from ${c.sample_senders[0] || ''}`)}
                  disabled={!c.sample_senders[0]}
                  className="px-2 py-1 text-xs rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 disabled:opacity-50 flex items-center gap-1"
                >
                  <FileText className="w-3 h-3" /> Articles
                </button>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
};

export default CorrespondentHotClustersRenderer;
