import { useCallback, useEffect, useState } from 'react';
import { api } from '../../services/api';

/**
 * How LocalBook's memory budget is arrived at (LB-1).
 *
 * Shown in LLM Studio because that is where "will this model fit?" is asked,
 * and the answer is meaningless without the reason. A 48 GB Mac reporting a
 * 7 GB budget reads as a bug until you can see that 26 GB was deliberately
 * handed to something else — so the parts are shown, not just the total.
 *
 * The reserve is PER-MACHINE and never synced: the Mac mini needs 0, a machine
 * also running a ~26 GB agent brain needs about 26. A synced value would be
 * wrong on at least one machine by construction.
 */

type Budget = {
    working_set_gb: number;
    resident_reserve_gb: number;
    external_reserve_gb: number;
    budget_gb: number;
};

export function GpuBudgetBar() {
    const [b, setB] = useState<Budget | null>(null);
    const [editing, setEditing] = useState(false);
    const [draft, setDraft] = useState('0');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const refresh = useCallback(async () => {
        try {
            const { data } = await api.get<Budget>('/settings/gpu-budget');
            setB(data);
            setDraft(String(data.external_reserve_gb));
        } catch {
            setB(null);
        }
    }, []);

    useEffect(() => { void refresh(); }, [refresh]);

    const save = async () => {
        setBusy(true); setError(null);
        try {
            const { data } = await api.post<Budget>('/settings/gpu-budget/external-reserve', {
                gb: Number(draft),
            });
            setB(data);
            setEditing(false);
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? 'Could not save that.');
        } finally {
            setBusy(false);
        }
    };

    if (!b || b.working_set_gb <= 0) return null;

    const ws = b.working_set_gb;
    const pct = (n: number) => `${Math.max(0, Math.min(100, (n / ws) * 100))}%`;

    return (
        <div className="border-b border-gray-200 bg-gray-50 px-4 py-3 dark:border-gray-700 dark:bg-gray-900/40">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
                <span className="text-xs font-medium text-gray-700 dark:text-gray-300">
                    {b.budget_gb} GB available to models
                    <span className="ml-1 font-normal text-gray-500">
                        of {ws.toFixed(1)} GB this Mac can address
                    </span>
                </span>
                {!editing && (
                    <button
                        onClick={() => setEditing(true)}
                        className="text-xs text-blue-600 underline underline-offset-2 hover:text-blue-500 dark:text-blue-400"
                    >
                        {b.external_reserve_gb > 0
                            ? `Reserved for other apps: ${b.external_reserve_gb} GB`
                            : 'Reserve memory for other apps'}
                    </button>
                )}
            </div>

            {/* Parts, not just the total — the reserve is the whole explanation. */}
            <div className="mt-2 flex h-2 w-full overflow-hidden rounded bg-gray-200 dark:bg-gray-700">
                <div
                    className="bg-blue-500"
                    style={{ width: pct(b.budget_gb) }}
                    title={`${b.budget_gb} GB for models`}
                />
                <div
                    className="bg-amber-500/70"
                    style={{ width: pct(b.external_reserve_gb) }}
                    title={`${b.external_reserve_gb} GB reserved for other apps`}
                />
                <div
                    className="bg-gray-400/60"
                    style={{ width: pct(b.resident_reserve_gb) }}
                    title={`${b.resident_reserve_gb} GB LocalBook keeps resident (embeddings, app)`}
                />
            </div>

            {editing && (
                <div className="mt-3">
                    <p className="text-xs text-gray-500 dark:text-gray-400">
                        Memory to keep free for something else on this Mac — an agent running
                        alongside LocalBook, for instance. LocalBook subtracts it before deciding
                        what it can load, so the two are not competing for the same memory. This
                        setting stays on this Mac and is never synced.
                    </p>
                    <div className="mt-2 flex items-center gap-2">
                        <input
                            type="number"
                            min={0}
                            max={Math.floor(ws)}
                            step={0.5}
                            value={draft}
                            onChange={(e) => setDraft(e.target.value)}
                            className="w-24 rounded border border-gray-300 bg-white px-2 py-1 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-gray-100"
                        />
                        <span className="text-xs text-gray-500">GB</span>
                        <button
                            onClick={save}
                            disabled={busy}
                            className="rounded bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-500 disabled:opacity-50"
                        >
                            Save
                        </button>
                        <button
                            onClick={() => {
                                setEditing(false);
                                setDraft(String(b.external_reserve_gb));
                                setError(null);
                            }}
                            className="rounded border border-gray-300 px-3 py-1 text-xs text-gray-600 dark:border-gray-600 dark:text-gray-300"
                        >
                            Cancel
                        </button>
                    </div>
                    {error && <p className="mt-2 text-xs text-red-500">{error}</p>}
                </div>
            )}
        </div>
    );
}
