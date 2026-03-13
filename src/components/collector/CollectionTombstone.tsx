import React, { useState, useEffect } from 'react';
import { collectorService } from '../../services/collector';

interface CollectionTombstoneProps {
  notebookId: string | null;
  onOpenCollector: () => void;
}

interface StagnationData {
  stagnation: {
    stagnating: boolean;
    severity: string | null;
    days_since_growth: number;
    total_dry_runs: number;
    dominant_rejection_reasons: Record<string, number>;
  };
  auto_expand: boolean;
  pending_count: number;
}

export const CollectionTombstone: React.FC<CollectionTombstoneProps> = ({
  notebookId,
  onOpenCollector,
}) => {
  const [data, setData] = useState<StagnationData | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    setData(null);
    setDismissed(false);
    if (!notebookId) return;

    let cancelled = false;
    collectorService.getStagnationStatus(notebookId).then((result) => {
      if (!cancelled) setData(result);
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [notebookId]);

  if (!data || dismissed) return null;

  const { stagnation, auto_expand, pending_count } = data;
  const hasPending = pending_count > 0;
  const isStagnating = stagnation.stagnating;

  // Nothing to show
  if (!hasPending && !isStagnating) return null;

  // Determine banner variant
  let bgClass = '';
  let borderClass = '';
  let iconClass = '';
  let icon = '';
  let title = '';
  let subtitle = '';

  if (hasPending && isStagnating && auto_expand) {
    // Expansion mode + pending items
    bgClass = 'bg-violet-50 dark:bg-violet-900/20';
    borderClass = 'border-violet-200 dark:border-violet-800';
    iconClass = 'text-violet-600 dark:text-violet-400';
    icon = '\uD83D\uDD2D'; // telescope
    title = `${pending_count} item${pending_count !== 1 ? 's' : ''} found via expanded search`;
    subtitle = `Collection expanded after ${stagnation.days_since_growth} days — review to grow your notebook`;
  } else if (hasPending) {
    // Normal pending items
    bgClass = 'bg-amber-50 dark:bg-amber-900/20';
    borderClass = 'border-amber-200 dark:border-amber-800';
    iconClass = 'text-amber-600 dark:text-amber-400';
    icon = '\uD83D\uDCCB'; // clipboard
    title = `${pending_count} item${pending_count !== 1 ? 's' : ''} awaiting your review`;
    subtitle = 'Open the Collector to approve or reject';
  } else if (isStagnating && stagnation.severity === 'plateau') {
    // Plateau — suggest action
    bgClass = 'bg-gray-50 dark:bg-gray-700/30';
    borderClass = 'border-gray-200 dark:border-gray-600';
    iconClass = 'text-gray-500 dark:text-gray-400';
    icon = '\uD83D\uDCCA'; // bar chart
    title = 'Collection has plateaued';
    subtitle = `No new content in ${stagnation.days_since_growth} days — consider expanding scope or adding sources`;
  } else if (isStagnating && auto_expand) {
    // Stagnating with auto-expand (mild/moderate, no pending yet)
    bgClass = 'bg-blue-50 dark:bg-blue-900/20';
    borderClass = 'border-blue-200 dark:border-blue-800';
    iconClass = 'text-blue-600 dark:text-blue-400';
    icon = '\uD83D\uDD2D'; // telescope
    title = 'Expanding search scope';
    subtitle = `No new content in ${stagnation.days_since_growth} days — exploring adjacent topics`;
  } else {
    return null;
  }

  return (
    <div className={`mx-2 my-1.5 px-2.5 py-2 rounded-lg border ${bgClass} ${borderClass} animate-slide-down`}>
      <div className="flex items-start gap-2">
        <span className={`text-sm flex-shrink-0 mt-0.5 ${iconClass}`}>{icon}</span>
        <div className="flex-1 min-w-0">
          <p className={`text-[11px] font-semibold ${iconClass} leading-tight`}>{title}</p>
          <p className="text-[10px] text-gray-500 dark:text-gray-400 mt-0.5 leading-tight">{subtitle}</p>
        </div>
        <div className="flex items-center gap-1 flex-shrink-0">
          {hasPending && (
            <button
              onClick={onOpenCollector}
              className={`px-2 py-0.5 text-[10px] font-medium rounded ${iconClass} hover:underline`}
            >
              Review
            </button>
          )}
          <button
            onClick={() => setDismissed(true)}
            className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 text-[10px] px-1"
            title="Dismiss"
          >
            ✕
          </button>
        </div>
      </div>
    </div>
  );
};
