/**
 * PlanCard — inline visual for multi-step curator plans.
 *
 * Curator Phase 2b (2026-05-13). Renders inside a chat message when the
 * message metadata carries `planId`. Reads live plan state from
 * `usePlanStream()`. Provides a real Stop button while the plan is
 * running, calling `POST /curator/plans/{id}/cancel`.
 *
 * Styling deliberately matches the existing "system status" tone of the
 * chat surface — subtle, expandable, not screaming for attention.
 */
import { KeyboardEvent, ReactElement, useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, Circle, Loader2, Check, X, MinusCircle, Square } from 'lucide-react';
import { usePlanStream, PlanStep, PlanStepStatus, PlanStatus } from '../../hooks/usePlanStream';

interface PlanCardProps {
  planId: string;
}

const STATUS_LABEL: Record<PlanStatus, string> = {
  proposed: 'Preparing…',
  running: 'Running',
  completed: 'Done',
  cancelled: 'Cancelled',
  failed: 'Failed',
};

const STATUS_TEXT_COLOR: Record<PlanStatus, string> = {
  proposed: 'text-gray-500 dark:text-gray-400',
  running: 'text-blue-600 dark:text-blue-400',
  completed: 'text-emerald-600 dark:text-emerald-400',
  cancelled: 'text-amber-600 dark:text-amber-400',
  failed: 'text-rose-600 dark:text-rose-400',
};

const STEP_ICON_BY_STATUS: Record<PlanStepStatus, ReactElement> = {
  pending: <Circle className="h-3.5 w-3.5 text-gray-400 dark:text-gray-500" />,
  running: <Loader2 className="h-3.5 w-3.5 text-blue-600 dark:text-blue-400 animate-spin" />,
  done: <Check className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />,
  failed: <X className="h-3.5 w-3.5 text-rose-600 dark:text-rose-400" />,
  skipped: <MinusCircle className="h-3.5 w-3.5 text-gray-400 dark:text-gray-500" />,
};

function StepRow({ step }: { step: PlanStep }) {
  return (
    <div className="flex items-start gap-2 py-1">
      <span className="flex-shrink-0 mt-0.5">{STEP_ICON_BY_STATUS[step.status]}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2">
          <span className="text-xs font-medium text-gray-800 dark:text-gray-200">{step.name}</span>
          {step.description && (
            <span className="text-[11px] text-gray-500 dark:text-gray-400 truncate">{step.description}</span>
          )}
        </div>
        {step.output_summary && (
          <div className="text-[11px] text-gray-600 dark:text-gray-400 italic mt-0.5">
            {step.output_summary}
          </div>
        )}
      </div>
    </div>
  );
}

export function PlanCard({ planId }: PlanCardProps) {
  const { plans, cancelPlan, fetchPlan } = usePlanStream();
  const [expanded, setExpanded] = useState(true);
  const [stopping, setStopping] = useState(false);

  const plan = plans.get(planId);

  // Race recovery: if the SSE stream hasn't delivered any event for this
  // plan_id yet (collection finished faster than the EventSource opened),
  // one-shot fetch the current state from REST. Idempotent + deduped
  // inside the hook.
  useEffect(() => {
    if (!plan) {
      void fetchPlan(planId);
    }
  }, [plan, planId, fetchPlan]);

  const toggleExpanded = () => setExpanded((v) => !v);
  const handleHeaderKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      toggleExpanded();
    }
  };

  if (!plan) {
    // Plan not yet seen on the SSE stream — render a placeholder so the
    // user knows something's coming. This is transient (events arrive
    // within ~200ms of plan_created).
    return (
      <div className="my-2 px-3 py-2 rounded-md border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/40 text-xs text-gray-600 dark:text-gray-400">
        <Loader2 className="inline h-3 w-3 mr-1.5 animate-spin" />
        Curator is preparing a plan…
      </div>
    );
  }

  const doneSteps = plan.steps.filter((s) => s.status === 'done').length;
  const totalSteps = plan.steps.length;
  const isRunning = plan.status === 'running';
  const showStop = isRunning && !stopping;

  const handleStop = async () => {
    if (stopping) return;
    setStopping(true);
    const ok = await cancelPlan(planId);
    if (!ok) {
      // Couldn't cancel — re-enable button. The SSE stream will still
      // bring the real state if it changes.
      setStopping(false);
    }
    // If ok, leave stopping=true until the plan_cancelled event arrives
    // (which flips plan.status to cancelled and re-renders).
  };

  return (
    <div className="my-2 rounded-md border border-gray-300 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/40 overflow-hidden">
      {/* Header — clickable to expand/collapse. Implemented as a div with
          role="button" so we can nest a real <button> (Stop) inside
          without producing invalid button-in-button HTML. */}
      <div
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onClick={toggleExpanded}
        onKeyDown={handleHeaderKey}
        className="w-full flex items-center justify-between px-3 py-2 hover:bg-gray-100 dark:hover:bg-gray-700/40 transition-colors text-left cursor-pointer select-none"
      >
        <div className="flex items-center gap-2 min-w-0">
          {expanded ? (
            <ChevronDown className="h-3.5 w-3.5 text-gray-500 dark:text-gray-400" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5 text-gray-500 dark:text-gray-400" />
          )}
          <span className="text-xs font-medium text-gray-800 dark:text-gray-200 truncate">
            {plan.summary || plan.intent || 'Plan'}
          </span>
          <span className={`text-[11px] ${STATUS_TEXT_COLOR[plan.status]}`}>
            {STATUS_LABEL[plan.status]}
          </span>
          <span className="text-[11px] text-gray-500 dark:text-gray-400">
            ({doneSteps}/{totalSteps})
          </span>
        </div>
        {showStop && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              void handleStop();
            }}
            className="flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium text-rose-600 dark:text-rose-300 hover:text-rose-700 dark:hover:text-rose-200 hover:bg-rose-100 dark:hover:bg-rose-900/30 transition-colors cursor-pointer"
            aria-label="Stop plan"
          >
            <Square className="h-3 w-3" />
            Stop
          </button>
        )}
        {stopping && (
          <span className="text-[11px] text-amber-600 dark:text-amber-400 italic">
            Stopping…
          </span>
        )}
      </div>

      {/* Expanded body — step list */}
      {expanded && (
        <div className="px-3 pb-2 pt-1 border-t border-gray-200 dark:border-gray-700/50">
          {plan.steps.length === 0 ? (
            <div className="text-[11px] text-gray-500 dark:text-gray-400 italic py-1">
              Steps will appear as the curator runs…
            </div>
          ) : (
            <div className="space-y-0.5">
              {plan.steps.map((step) => (
                <StepRow key={step.seq} step={step} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
