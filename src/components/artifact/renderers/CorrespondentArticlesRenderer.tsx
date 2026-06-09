/**
 * CorrespondentArticlesRenderer — in-chat list of extracted newsletter
 * articles.
 *
 * Phase 1 of Tier 2 (2026-06-09). The `@correspondent show articles`
 * intent emits a `json-correspondent-articles` fence with this payload.
 * Per-article actions in this slice are limited to "Open source"
 * (deep-links into the source viewer for the parent newsletter); read,
 * tag, deep-read CTAs land in Phase 2 once we have per-article summaries
 * + clustering wired up.
 */
import React from 'react';
import { Mail, FileText } from 'lucide-react';
import type { RendererProps } from '../../../types/artifact';

interface ArticleItem {
  id: string;
  title: string;
  sender?: string | null;
  summary?: string | null;
  position?: number;
  source_id: string;
  notebook_id: string;
  created_at?: string;
  topic_tags?: string[];
}

interface CorrespondentArticlesPayload {
  items: ArticleItem[];
  empty_message?: string;
}

function relativeTime(iso: string | null | undefined): string {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return '';
  const diffMs = Date.now() - t;
  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

export const CorrespondentArticlesRenderer: React.FC<RendererProps<CorrespondentArticlesPayload>> = ({ artifact }) => {
  const payload = artifact.payload || ({} as CorrespondentArticlesPayload);
  const items = payload.items || [];

  const handleOpenSource = (item: ArticleItem) => {
    // Dispatch the same global event the chat citation-click path uses
    // so the source viewer opens for the parent newsletter. The article
    // position rides along in case the viewer wants to scroll-to.
    window.dispatchEvent(new CustomEvent('lb:openSource', {
      detail: {
        sourceId: item.source_id,
        notebookId: item.notebook_id,
        articlePosition: item.position,
      },
    }));
  };

  if (items.length === 0) {
    return (
      <div className="not-prose my-3 p-4 rounded-lg border border-orange-200 dark:border-orange-800 bg-orange-50/40 dark:bg-orange-900/10">
        <div className="flex items-center gap-2 text-sm text-orange-700 dark:text-orange-300">
          <Mail className="w-4 h-4" />
          <span>{payload.empty_message || 'No extracted articles yet.'}</span>
        </div>
      </div>
    );
  }

  return (
    <div className="not-prose my-3 space-y-2">
      <div className="flex items-center gap-2 text-xs font-semibold text-orange-700 dark:text-orange-300 uppercase tracking-wide">
        <FileText className="w-3.5 h-3.5" />
        Articles · {items.length}
      </div>
      {items.map((a) => (
        <button
          key={a.id}
          onClick={() => handleOpenSource(a)}
          className="w-full text-left rounded-lg border border-gray-200 dark:border-gray-700 p-3 bg-white dark:bg-gray-800 hover:bg-orange-50 dark:hover:bg-orange-900/10 transition-colors"
        >
          <div className="flex items-start justify-between gap-3">
            <div className="flex-1 min-w-0">
              <p className="text-sm font-medium text-gray-800 dark:text-gray-100 line-clamp-2">
                {a.title || '(untitled)'}
              </p>
              <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                {a.sender ? <span>from <span className="font-medium">{a.sender}</span></span> : null}
                {a.created_at ? <span> · <span title={a.created_at}>{relativeTime(a.created_at)}</span></span> : null}
                {typeof a.position === 'number' && a.position > 0 ? (
                  <span> · article {a.position + 1}</span>
                ) : null}
              </p>
              {a.summary && (
                <p className="text-xs text-gray-600 dark:text-gray-300 mt-1.5 italic line-clamp-2">
                  {a.summary}
                </p>
              )}
              {a.topic_tags && a.topic_tags.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1">
                  {a.topic_tags.slice(0, 5).map((tag) => (
                    <span
                      key={tag}
                      className="text-[10px] uppercase tracking-wide text-orange-700 bg-orange-100 dark:bg-orange-900/40 dark:text-orange-300 rounded px-1.5 py-0.5"
                    >
                      {tag}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
        </button>
      ))}
    </div>
  );
};

export default CorrespondentArticlesRenderer;
