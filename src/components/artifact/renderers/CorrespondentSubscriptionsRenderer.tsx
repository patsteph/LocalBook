/**
 * CorrespondentSubscriptionsRenderer — interactive in-chat subscription
 * proposals card.
 *
 * Phase I/J (2026-06-09): emits the same UI as Settings → Correspondent
 * → Suggested subscriptions, but inside a chat bubble. Handles both
 * kind='subscription' (RSS feed) and kind='entity' (entity-watch).
 */
import React, { useState } from 'react';
import { Check, X, Mail } from 'lucide-react';
import type { RendererProps } from '../../../types/artifact';
import { correspondentService, type SubscriptionProposal } from '../../../services/correspondent';

interface CorrespondentSubscriptionsPayload {
  items: SubscriptionProposal[];
  empty_message?: string;
}

type ItemState = 'idle' | 'busy' | 'approved' | 'dismissed' | 'error';

export const CorrespondentSubscriptionsRenderer: React.FC<RendererProps<CorrespondentSubscriptionsPayload>> = ({ artifact }) => {
  const payload = artifact.payload || ({} as CorrespondentSubscriptionsPayload);
  const items = payload.items || [];

  const [state, setState] = useState<Record<string, ItemState>>({});
  const [errorMsg, setErrorMsg] = useState<Record<string, string>>({});

  const setItemState = (id: string, s: ItemState) =>
    setState((prev) => ({ ...prev, [id]: s }));

  const handleApprove = async (item: SubscriptionProposal) => {
    setItemState(item.id, 'busy');
    setErrorMsg((prev) => ({ ...prev, [item.id]: '' }));
    try {
      await correspondentService.approveSubscription(item.id);
      setItemState(item.id, 'approved');
    } catch (e) {
      setErrorMsg((prev) => ({
        ...prev,
        [item.id]: e instanceof Error ? e.message : 'Subscribe failed',
      }));
      setItemState(item.id, 'error');
    }
  };

  const handleDismiss = async (item: SubscriptionProposal) => {
    setItemState(item.id, 'busy');
    setErrorMsg((prev) => ({ ...prev, [item.id]: '' }));
    try {
      await correspondentService.dismissSubscription(item.id);
      setItemState(item.id, 'dismissed');
    } catch (e) {
      setErrorMsg((prev) => ({
        ...prev,
        [item.id]: e instanceof Error ? e.message : 'Dismiss failed',
      }));
      setItemState(item.id, 'error');
    }
  };

  if (items.length === 0) {
    return (
      <div className="not-prose my-3 p-4 rounded-lg border border-orange-200 dark:border-orange-800 bg-orange-50/40 dark:bg-orange-900/10">
        <div className="flex items-center gap-2 text-sm text-orange-700 dark:text-orange-300">
          <Mail className="w-4 h-4" />
          <span>{payload.empty_message || 'No subscription or entity-watch proposals waiting.'}</span>
        </div>
      </div>
    );
  }

  return (
    <div className="not-prose my-3 space-y-2">
      <div className="flex items-center gap-2 text-xs font-semibold text-orange-700 dark:text-orange-300 uppercase tracking-wide">
        <Mail className="w-3.5 h-3.5" />
        Proposals · {items.length}
      </div>
      {items.map((s) => {
        const itemState = state[s.id] || 'idle';
        const err = errorMsg[s.id];
        if (itemState === 'approved' || itemState === 'dismissed') {
          const verb = itemState === 'approved'
            ? (s.kind === 'entity' ? 'Watching' : 'Subscribed')
            : 'Dismissed';
          return (
            <div
              key={s.id}
              className="p-2 rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50 text-xs text-gray-500 dark:text-gray-400"
            >
              <span className="line-through">{s.title}</span>
              <span className="ml-2 text-emerald-600 dark:text-emerald-400">✓ {verb}</span>
            </div>
          );
        }
        const isEntity = s.kind === 'entity';
        return (
          <div
            key={s.id}
            className={`rounded-lg border p-3 ${
              isEntity
                ? 'border-purple-200 dark:border-purple-800 bg-purple-50/40 dark:bg-purple-900/10'
                : 'border-amber-200 dark:border-amber-800 bg-amber-50/40 dark:bg-amber-900/10'
            }`}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  {isEntity ? (
                    <>
                      <span className="text-[10px] uppercase tracking-wide text-purple-700 bg-purple-100 dark:bg-purple-900/40 rounded px-1.5 py-0.5">
                        entity watch
                      </span>
                      <span className="text-[10px] uppercase tracking-wide text-gray-500 bg-gray-100 dark:bg-gray-800 rounded px-1.5 py-0.5">
                        {s.entity_type}
                      </span>
                    </>
                  ) : (
                    <>
                      <span className="text-[10px] uppercase tracking-wide text-amber-700 bg-amber-100 dark:bg-amber-900/40 rounded px-1.5 py-0.5">
                        {s.kind_label}
                      </span>
                      <span className="text-[10px] uppercase tracking-wide text-gray-500 bg-gray-100 dark:bg-gray-800 rounded px-1.5 py-0.5">
                        {s.source_type}
                      </span>
                    </>
                  )}
                </div>
                <p className="text-sm font-medium text-gray-800 dark:text-gray-100 truncate mt-1">{s.title}</p>
                {isEntity ? (
                  <>
                    {s.source_email?.summary && (
                      <p className="text-xs text-gray-600 dark:text-gray-300 mt-1 italic line-clamp-2">
                        {s.source_email.summary}
                      </p>
                    )}
                    {s.source_email?.sender && (
                      <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                        mentioned by {s.source_email.sender}
                      </p>
                    )}
                  </>
                ) : (
                  <>
                    <p className="text-xs text-gray-500 dark:text-gray-400 truncate">
                      {s.feed_url || s.url}
                    </p>
                    {s.source_email?.subject && (
                      <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                        mentioned in <em>{s.source_email.subject}</em>
                        {s.source_email.sender && <span> · from {s.source_email.sender}</span>}
                      </p>
                    )}
                  </>
                )}
                {err && itemState !== 'busy' && (
                  <p className="mt-1.5 text-xs text-red-600 dark:text-red-400">⚠ {err}</p>
                )}
              </div>
              <div className="flex flex-col gap-1 flex-shrink-0">
                <button
                  onClick={() => handleApprove(s)}
                  disabled={itemState === 'busy'}
                  className={`px-2 py-1 text-xs rounded-lg text-white disabled:opacity-50 flex items-center gap-1 ${
                    isEntity ? 'bg-purple-600 hover:bg-purple-700' : 'bg-amber-600 hover:bg-amber-700'
                  }`}
                  title={isEntity ? 'Track this entity' : 'Add this feed to the Collector'}
                >
                  <Check className="w-3 h-3" /> {itemState === 'busy' ? '...' : isEntity ? 'Track' : 'Subscribe'}
                </button>
                <button
                  onClick={() => handleDismiss(s)}
                  disabled={itemState === 'busy'}
                  className="px-2 py-1 text-xs rounded-lg bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 disabled:opacity-50 flex items-center gap-1"
                >
                  <X className="w-3 h-3" /> Dismiss
                </button>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
};

export default CorrespondentSubscriptionsRenderer;
