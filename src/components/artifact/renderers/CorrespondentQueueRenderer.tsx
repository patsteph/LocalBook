/**
 * CorrespondentQueueRenderer — interactive in-chat approval queue card.
 *
 * Phase I/J (2026-06-09): when @correspondent show queue runs, the chat
 * reply embeds a ```json-correspondent-queue fence carrying this payload.
 * The renderer mirrors the Settings → Correspondent queue surface but
 * lives inside the chat bubble — same accept/dismiss/reroute actions,
 * no command-typing for the follow-up step.
 *
 * Optimistic UI: dismissed/approved items fade out immediately; on
 * failure we restore them with an error chip.
 */
import React, { useMemo, useState } from 'react';
import { Check, X, Mail, ChevronDown, ChevronRight } from 'lucide-react';
import type { RendererProps } from '../../../types/artifact';
import { correspondentService, type QueueItem } from '../../../services/correspondent';

const GROUP_THRESHOLD = 3; // P3.1 (locked H.1): sender groups ≥3 collapse

interface CorrespondentQueuePayload {
  items: QueueItem[];
  notebooks: Array<{ id: string; title: string }>;
  empty_message?: string;
}

type ItemState = 'idle' | 'busy' | 'approved' | 'dismissed' | 'error';

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

export const CorrespondentQueueRenderer: React.FC<RendererProps<CorrespondentQueuePayload>> = ({ artifact }) => {
  const payload = artifact.payload || ({} as CorrespondentQueuePayload);
  const items = payload.items || [];
  const notebooks = payload.notebooks || [];

  const [override, setOverride] = useState<Record<string, string>>({});
  const [state, setState] = useState<Record<string, ItemState>>({});
  const [errorMsg, setErrorMsg] = useState<Record<string, string>>({});
  // P3.1 (2026-06-10) — per-sender-group expansion state
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>({});
  const [groupBusy, setGroupBusy] = useState<Record<string, boolean>>({});

  // Group items by sender; groups ≥ GROUP_THRESHOLD are presented as a
  // collapsible section with bulk actions (H.1 locked: ≥3 from same
  // sender). Smaller groups + singletons render as today.
  const { senderGroups, ungroupedItems } = useMemo(() => {
    const bySender: Record<string, QueueItem[]> = {};
    for (const it of items) {
      const key = (it.sender || '').trim() || '(unknown sender)';
      (bySender[key] ||= []).push(it);
    }
    const groups: Array<{ sender: string; items: QueueItem[] }> = [];
    const ungrouped: QueueItem[] = [];
    for (const [sender, group] of Object.entries(bySender)) {
      if (group.length >= GROUP_THRESHOLD) {
        groups.push({ sender, items: group });
      } else {
        ungrouped.push(...group);
      }
    }
    // Sort groups largest-first
    groups.sort((a, b) => b.items.length - a.items.length);
    return { senderGroups: groups, ungroupedItems: ungrouped };
  }, [items]);

  const handleBulkApprove = async (groupSender: string, groupItems: QueueItem[]) => {
    // Honor each item's individual override notebook if the user picked
    // one in the inline picker; otherwise route each to its own top
    // candidate by passing notebook_id=undefined per item.
    setGroupBusy((prev) => ({ ...prev, [groupSender]: true }));
    try {
      // Determine if all items have a consistent target — either same
      // override notebook OR same top_candidate notebook. If so, bulk-
      // approve to that notebook (so backend records sender-bias once
      // per item with the same target). Otherwise fall back to per-
      // item processing where each goes to its own top_candidate.
      const targets = groupItems.map((q) => override[q.item_id] || q.top_candidate?.notebook_id || '');
      const allSame = targets.length > 0 && targets.every((t) => t && t === targets[0]);
      const itemIds = groupItems.map((q) => q.item_id);
      // Mark all items as busy
      setState((prev) => {
        const next = { ...prev };
        for (const id of itemIds) next[id] = 'busy';
        return next;
      });
      const result = await correspondentService.batchApproveQueue(
        itemIds,
        allSame ? targets[0] : undefined,
      );
      setState((prev) => {
        const next = { ...prev };
        for (const r of result.results || []) {
          next[r.item_id] = r.ok ? 'approved' : 'error';
        }
        return next;
      });
      if ((result.failed || 0) > 0) {
        setErrorMsg((prev) => {
          const next = { ...prev };
          for (const r of result.results || []) {
            if (!r.ok && r.reason) next[r.item_id] = r.reason;
          }
          return next;
        });
      }
    } catch (e) {
      setErrorMsg((prev) => {
        const next = { ...prev };
        for (const q of groupItems) next[q.item_id] = e instanceof Error ? e.message : 'Bulk approve failed';
        return next;
      });
      setState((prev) => {
        const next = { ...prev };
        for (const q of groupItems) next[q.item_id] = 'error';
        return next;
      });
    } finally {
      setGroupBusy((prev) => ({ ...prev, [groupSender]: false }));
    }
  };

  const setItemState = (id: string, s: ItemState) =>
    setState((prev) => ({ ...prev, [id]: s }));

  const handleApprove = async (item: QueueItem) => {
    setItemState(item.item_id, 'busy');
    setErrorMsg((prev) => ({ ...prev, [item.item_id]: '' }));
    try {
      const target = override[item.item_id] || undefined;
      const result = await correspondentService.approveQueueItem(item.item_id, target);
      if (result.imap_deleted === false) {
        setErrorMsg((prev) => ({
          ...prev,
          [item.item_id]: 'Ingested but couldn\'t remove from inbox.',
        }));
      }
      setItemState(item.item_id, 'approved');
    } catch (e) {
      setErrorMsg((prev) => ({
        ...prev,
        [item.item_id]: e instanceof Error ? e.message : 'Approve failed',
      }));
      setItemState(item.item_id, 'error');
    }
  };

  const handleDismiss = async (item: QueueItem) => {
    setItemState(item.item_id, 'busy');
    setErrorMsg((prev) => ({ ...prev, [item.item_id]: '' }));
    try {
      await correspondentService.dismissQueueItem(item.item_id);
      setItemState(item.item_id, 'dismissed');
    } catch (e) {
      setErrorMsg((prev) => ({
        ...prev,
        [item.item_id]: e instanceof Error ? e.message : 'Dismiss failed',
      }));
      setItemState(item.item_id, 'error');
    }
  };

  if (items.length === 0) {
    return (
      <div className="not-prose my-3 p-4 rounded-lg border border-orange-200 dark:border-orange-800 bg-orange-50/40 dark:bg-orange-900/10">
        <div className="flex items-center gap-2 text-sm text-orange-700 dark:text-orange-300">
          <Mail className="w-4 h-4" />
          <span>{payload.empty_message || 'Approval queue is empty.'}</span>
        </div>
      </div>
    );
  }

  const renderItem = (q: QueueItem) => {
    const s = state[q.item_id] || 'idle';
    const err = errorMsg[q.item_id];
    if (s === 'approved' || s === 'dismissed') {
      const verb = s === 'approved' ? 'Approved' : 'Dismissed';
      return (
        <div
          key={q.item_id}
          className="p-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 text-xs text-gray-500 dark:text-gray-400"
        >
          <span className="line-through">{q.subject}</span>
          <span className="ml-2 text-emerald-600 dark:text-emerald-400">✓ {verb}</span>
          {err && <span className="ml-2 text-amber-600 dark:text-amber-400">· {err}</span>}
        </div>
      );
    }
    return (
      <div
        key={q.item_id}
        className="rounded-lg border border-gray-200 dark:border-gray-700 p-3 bg-white dark:bg-gray-800"
      >
            <div className="flex items-start justify-between gap-3">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  {q.kind === 'forward' && (
                    <span className="text-[10px] uppercase tracking-wide text-amber-700 bg-amber-100 dark:bg-amber-900/40 rounded px-1.5 py-0.5">
                      forward
                    </span>
                  )}
                  <p className="text-sm font-medium text-gray-800 dark:text-gray-100 truncate">
                    {q.subject || '(no subject)'}
                  </p>
                </div>
                <p className="text-xs text-gray-500 dark:text-gray-400 truncate">
                  from {q.sender} · <span title={q.created_at}>{relativeTime(q.created_at)}</span>
                </p>
                {q.summary && (
                  <p className="text-xs text-gray-600 dark:text-gray-300 mt-1 italic line-clamp-2">
                    {q.summary}
                  </p>
                )}
                {q.top_candidate && (
                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-1.5">
                    Best match: <span className="font-medium">{q.top_candidate.notebook_name}</span>{' '}
                    <span className="text-gray-400">
                      ({(q.top_candidate.confidence * 100).toFixed(0)}%)
                    </span>
                  </p>
                )}
                {(q.sender_corrections ?? 0) > 0 ? (
                  <p className="text-xs text-emerald-700 dark:text-emerald-400 mt-1">
                    ✓ {q.sender_corrections} prior approval{q.sender_corrections === 1 ? '' : 's'} for this sender — should auto-route soon.
                  </p>
                ) : (q.sender_correction_total ?? 0) === 0 ? (
                  <p className="text-xs text-gray-400 dark:text-gray-500 mt-1">
                    First time seeing this sender. Your choice teaches the router.
                  </p>
                ) : null}
                {notebooks.length > 0 && (
                  <div className="mt-2 flex items-center gap-2">
                    <label className="text-[10px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
                      Route to
                    </label>
                    <select
                      value={override[q.item_id] ?? (q.top_candidate?.notebook_id || '')}
                      onChange={(e) =>
                        setOverride((prev) => ({ ...prev, [q.item_id]: e.target.value }))
                      }
                      disabled={s === 'busy'}
                      className="text-xs rounded border border-gray-300 dark:border-gray-600 bg-white dark:bg-gray-700 px-2 py-1 text-gray-800 dark:text-gray-200 flex-1 min-w-0 disabled:opacity-50"
                    >
                      {!q.top_candidate && <option value="">— pick a notebook —</option>}
                      {notebooks.map((nb) => (
                        <option key={nb.id} value={nb.id}>
                          {nb.title}
                          {q.top_candidate?.notebook_id === nb.id ? ' (best match)' : ''}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
                {err && s !== 'busy' && (
                  <p className="mt-1.5 text-xs text-red-600 dark:text-red-400">⚠ {err}</p>
                )}
              </div>
              <div className="flex flex-col gap-1 flex-shrink-0">
                <button
                  onClick={() => handleApprove(q)}
                  disabled={s === 'busy' || (!q.top_candidate && !override[q.item_id])}
                  className="px-2 py-1 text-xs rounded-lg bg-green-600 hover:bg-green-700 text-white disabled:opacity-50 flex items-center gap-1"
                  title="Ingest into the selected notebook"
                >
                  <Check className="w-3 h-3" /> {s === 'busy' ? '...' : 'Approve'}
                </button>
                <button
                  onClick={() => handleDismiss(q)}
                  disabled={s === 'busy'}
                  className="px-2 py-1 text-xs rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 disabled:opacity-50 flex items-center gap-1"
                >
                  <X className="w-3 h-3" /> Dismiss
                </button>
              </div>
            </div>
          </div>
        );
      };

  return (
    <div className="not-prose my-3 space-y-2">
      <div className="flex items-center gap-2 text-xs font-semibold text-orange-700 dark:text-orange-300 uppercase tracking-wide">
        <Mail className="w-3.5 h-3.5" />
        Pending approvals · {items.length}
      </div>

      {/* Sender groups ≥3 — collapsible bulk-action sections (H.1 locked) */}
      {senderGroups.map((group) => {
        const groupKey = group.sender;
        const isExpanded = !!expandedGroups[groupKey];
        const isBusy = !!groupBusy[groupKey];
        // Check if all items target the same notebook (for "approve all to X" label)
        const targets = group.items.map((q) => override[q.item_id] || q.top_candidate?.notebook_id || '');
        const sameTarget = targets.length > 0 && targets.every((t) => t && t === targets[0]);
        const targetNotebook = sameTarget
          ? group.items[0].top_candidate?.notebook_name || '—'
          : null;
        return (
          <div
            key={`group-${groupKey}`}
            className="rounded-lg border border-orange-200 dark:border-orange-800 bg-orange-50/50 dark:bg-orange-900/10"
          >
            <div className="flex items-center justify-between p-3">
              <button
                onClick={() => setExpandedGroups((prev) => ({ ...prev, [groupKey]: !prev[groupKey] }))}
                className="flex items-center gap-2 flex-1 min-w-0 text-left"
              >
                {isExpanded
                  ? <ChevronDown className="w-4 h-4 text-orange-700 dark:text-orange-400 flex-shrink-0" />
                  : <ChevronRight className="w-4 h-4 text-orange-700 dark:text-orange-400 flex-shrink-0" />}
                <div className="min-w-0">
                  <p className="text-sm font-medium text-gray-800 dark:text-gray-100 truncate">
                    {group.sender}
                  </p>
                  <p className="text-xs text-gray-600 dark:text-gray-300">
                    {group.items.length} pending
                    {targetNotebook && (
                      <> · all → <span className="font-medium">{targetNotebook}</span></>
                    )}
                  </p>
                </div>
              </button>
              <button
                onClick={() => handleBulkApprove(groupKey, group.items)}
                disabled={isBusy || !isExpanded}
                className="ml-3 px-3 py-1.5 text-xs rounded-lg bg-green-600 hover:bg-green-700 text-white disabled:opacity-40 disabled:cursor-not-allowed flex items-center gap-1 flex-shrink-0"
                title={isExpanded
                  ? (targetNotebook
                      ? `Approve all ${group.items.length} to ${targetNotebook}`
                      : `Approve all ${group.items.length} — each routes to its own best match`)
                  : 'Expand to view + bulk approve'}
              >
                <Check className="w-3 h-3" />
                {isBusy ? '…' : `Approve all ${group.items.length}`}
              </button>
            </div>
            {isExpanded && (
              <div className="px-3 pb-3 space-y-2">
                {group.items.map(renderItem)}
              </div>
            )}
          </div>
        );
      })}

      {/* Ungrouped — singletons + small (<3) groups render as today */}
      {ungroupedItems.map(renderItem)}
    </div>
  );
};

export default CorrespondentQueueRenderer;
