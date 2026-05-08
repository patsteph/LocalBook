import React, { useEffect, useState, useMemo } from 'react';
import { Loader2, X, ExternalLink } from 'lucide-react';
import { sourceService } from '../../services/sources';

/**
 * OutgoingLinksPanel — depth+1 expansion picker.
 *
 * Lists the outgoing links extracted at capture for a single source.
 * User checks the ones they want followed; on submit, the selected URLs
 * are scraped (depth+1, hard cap), each result lands in the notebook's
 * approval queue with parent + cross-notebook hints.
 *
 * Shape contract with the backend:
 *   GET  /sources/{notebookId}/{sourceId}/outgoing-links
 *   POST /sources/{notebookId}/{sourceId}/expand-links
 *
 * Renders nothing (returns null) if the source is itself a depth-1
 * expansion result — the API returns expansion_blocked=true and we
 * refuse to surface a recursion path in the UI.
 */

interface AnnotatedLink {
  url: string;
  text: string;
  context: string;
  already_captured: boolean;
}

interface OutgoingLinksPanelProps {
  notebookId: string;
  sourceId: string;
  /** Called when the user closes the panel without submitting. */
  onClose: () => void;
  /** Called after a successful submit so the parent can refresh / toast. */
  onSubmitted?: (jobId: string, selectedCount: number) => void;
}

export const OutgoingLinksPanel: React.FC<OutgoingLinksPanelProps> = ({
  notebookId,
  sourceId,
  onClose,
  onSubmitted,
}) => {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [links, setLinks] = useState<AnnotatedLink[]>([]);
  const [blocked, setBlocked] = useState(false);
  const [blockReason, setBlockReason] = useState<string | null>(null);
  const [filterUnscraped, setFilterUnscraped] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [submitting, setSubmitting] = useState(false);
  const [submitMsg, setSubmitMsg] = useState<{ ok: boolean; text: string } | null>(null);

  // Fetch the link list once on mount. The annotated `already_captured`
  // flag comes from the backend's cross-notebook dedup index.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const resp = await sourceService.listOutgoingLinks(notebookId, sourceId);
        if (cancelled) return;
        if (resp.expansion_blocked) {
          setBlocked(true);
          setBlockReason(resp.reason || 'Depth+1 expansion only');
          setLinks([]);
        } else {
          setLinks(resp.links || []);
        }
      } catch (e: any) {
        if (!cancelled) setError(e?.message ?? 'Could not load outgoing links');
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [notebookId, sourceId]);

  const visibleLinks = useMemo(
    () => (filterUnscraped ? links.filter(l => !l.already_captured) : links),
    [filterUnscraped, links],
  );

  const toggleAll = (checked: boolean) => {
    if (!checked) {
      setSelected(new Set());
      return;
    }
    // Only select links that aren't already captured — checking those
    // would just produce a "duplicate" skip in the expander.
    setSelected(new Set(visibleLinks.filter(l => !l.already_captured).map(l => l.url)));
  };

  const toggleOne = (url: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(url)) next.delete(url);
      else next.add(url);
      return next;
    });
  };

  const handleSubmit = async () => {
    if (selected.size === 0) return;
    setSubmitting(true);
    setSubmitMsg(null);
    try {
      const resp = await sourceService.expandOutgoingLinks(
        notebookId,
        sourceId,
        Array.from(selected),
      );
      setSubmitMsg({
        ok: true,
        text: `Expansion submitted — job ${resp.job_id}. ${resp.selected_count} URL${resp.selected_count !== 1 ? 's' : ''} queued. Results land in the approval queue.`,
      });
      onSubmitted?.(resp.job_id, resp.selected_count);
      // Clear selection so the user can immediately tell the action
      // landed; leave the panel open so they can see the toast.
      setSelected(new Set());
    } catch (e: any) {
      const detail = e?.response?.data?.detail ?? e?.message ?? 'Submit failed';
      setSubmitMsg({ ok: false, text: typeof detail === 'string' ? detail : JSON.stringify(detail) });
    } finally {
      setSubmitting(false);
    }
  };

  // Headless when the source is a depth-1 expansion result — no UI
  // needed, just dismiss. The caller can fall back to a tooltip.
  if (blocked) {
    return (
      <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={onClose}>
        <div className="bg-white dark:bg-gray-800 rounded-xl p-5 max-w-md w-full" onClick={e => e.stopPropagation()}>
          <div className="flex items-center justify-between mb-2">
            <h3 className="font-semibold text-gray-900 dark:text-white">Expansion not available</h3>
            <button onClick={onClose} className="text-gray-400 hover:text-gray-600">
              <X size={18} />
            </button>
          </div>
          <p className="text-sm text-gray-600 dark:text-gray-300">{blockReason}</p>
        </div>
      </div>
    );
  }

  const eligibleCount = visibleLinks.filter(l => !l.already_captured).length;
  const allEligibleSelected = eligibleCount > 0 && eligibleCount === selected.size;

  return (
    <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="bg-white dark:bg-gray-800 rounded-xl shadow-xl max-w-2xl w-full max-h-[80vh] flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-5 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
          <div>
            <h3 className="font-semibold text-gray-900 dark:text-white">
              Outgoing links{links.length > 0 && ` (${links.length})`}
            </h3>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
              Pick the links you want followed. Each scraped article goes to the approval queue (never auto-added).
            </p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300">
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="px-5 py-3 overflow-y-auto flex-1">
          {loading && (
            <div className="flex items-center justify-center py-12 text-gray-400">
              <Loader2 size={20} className="animate-spin mr-2" />
              Loading links…
            </div>
          )}

          {!loading && error && (
            <div className="rounded-lg bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-300 p-3 text-sm">
              {error}
            </div>
          )}

          {!loading && !error && links.length === 0 && (
            <div className="text-center py-10 text-sm text-gray-500 dark:text-gray-400">
              No outgoing links were captured with this source. Older captures
              from before the depth+1 feature won't have any.
            </div>
          )}

          {!loading && !error && links.length > 0 && (
            <>
              <div className="flex items-center justify-between mb-3 text-xs">
                <label className="flex items-center gap-2 text-gray-600 dark:text-gray-300 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={filterUnscraped}
                    onChange={e => setFilterUnscraped(e.target.checked)}
                  />
                  Hide already-captured ({links.length - links.filter(l => !l.already_captured).length})
                </label>
                <label className="flex items-center gap-2 text-gray-600 dark:text-gray-300 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={allEligibleSelected}
                    onChange={e => toggleAll(e.target.checked)}
                  />
                  Select all eligible ({eligibleCount})
                </label>
              </div>

              <ul className="space-y-1.5">
                {visibleLinks.map(link => {
                  const checked = selected.has(link.url);
                  const disabled = link.already_captured;
                  return (
                    <li
                      key={link.url}
                      className={`flex items-start gap-3 p-2.5 rounded-lg border ${
                        disabled
                          ? 'border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/40 opacity-60'
                          : checked
                          ? 'border-blue-400 dark:border-blue-600 bg-blue-50 dark:bg-blue-900/20'
                          : 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 hover:border-gray-300'
                      }`}
                    >
                      <input
                        type="checkbox"
                        checked={checked}
                        disabled={disabled}
                        onChange={() => toggleOne(link.url)}
                        className="mt-0.5 shrink-0"
                      />
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2">
                          <span className="font-medium text-sm text-gray-900 dark:text-white truncate">
                            {link.text || new URL(link.url).hostname}
                          </span>
                          {disabled && (
                            <span className="px-1.5 py-0.5 text-[10px] font-medium rounded bg-gray-200 dark:bg-gray-700 text-gray-600 dark:text-gray-300">
                              already captured
                            </span>
                          )}
                        </div>
                        <a
                          href={link.url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-xs text-blue-600 dark:text-blue-400 hover:underline inline-flex items-center gap-1 truncate max-w-full"
                          onClick={e => e.stopPropagation()}
                        >
                          {link.url} <ExternalLink size={10} />
                        </a>
                        {link.context && (
                          <p className="text-xs text-gray-500 dark:text-gray-400 mt-1 line-clamp-2">
                            “{link.context}”
                          </p>
                        )}
                      </div>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="px-5 py-3 border-t border-gray-200 dark:border-gray-700 flex items-center justify-between gap-3">
          <div className="min-w-0 flex-1">
            {submitMsg && (
              <span
                className={`text-xs ${
                  submitMsg.ok ? 'text-emerald-600 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'
                }`}
              >
                {submitMsg.text}
              </span>
            )}
            {!submitMsg && selected.size > 0 && (
              <span className="text-xs text-gray-500 dark:text-gray-400">
                {selected.size} selected. Approval queue will catch results.
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={onClose}
              className="px-3 py-1.5 text-sm rounded-lg text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
            >
              Close
            </button>
            <button
              onClick={handleSubmit}
              disabled={selected.size === 0 || submitting}
              className={`px-4 py-1.5 text-sm font-medium rounded-lg transition-colors ${
                selected.size === 0 || submitting
                  ? 'bg-gray-200 dark:bg-gray-700 text-gray-400 cursor-not-allowed'
                  : 'bg-blue-600 hover:bg-blue-700 text-white'
              }`}
            >
              {submitting ? <Loader2 size={14} className="animate-spin" /> : `Expand ${selected.size}`}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
