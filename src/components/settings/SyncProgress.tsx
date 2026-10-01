import { useEffect, useRef, useState } from 'react';
import { api } from '../../services/api';

/**
 * What sync is doing right now (LB-12). A sync used to be one long request behind a
 * spinner — no phase, no count, no idea whether stopping was safe (first mini ⇄ MBP
 * run, 2026-10-01). Runs now execute in the background and report here:
 *
 *   initiated  this Mac started it (Start sync / Sync now / the background loop)
 *   incoming   another Mac is pulling from or pushing to this one
 *   index      this Mac is making synced sources searchable
 *
 * Stop is always safe: every page commits on its own and files resume from where they
 * stopped, so the next sync simply continues.
 */

type Phase = { id: string; label: string; state: 'done' | 'current' | 'pending' | 'failed' };
export type SyncRun = {
    run_id: string; kind: 'initiated' | 'incoming' | 'index'; name?: string | null;
    running: boolean; quiet: boolean; phases: Phase[]; phase?: string | null; label: string;
    done: number; total: number; unit: string; detail: string; elapsed: number;
    result?: any; error?: string | null; stopped?: boolean; finished_at?: number | null;
};

const NAMES: Record<string, [string, string]> = {
    notebooks: ['notebook', 'notebooks'], sources: ['source', 'sources'], canvas_notes: ['note', 'notes'],
    highlights: ['highlight', 'highlights'], audio_generations: ['podcast', 'podcasts'],
    video_generations: ['video', 'videos'], content_generations: ['document', 'documents'],
    quiz_generations: ['quiz', 'quizzes'], visual_generations: ['visual', 'visuals'],
    infographic_generations: ['infographic', 'infographics'], documents: ['setting or memory', 'settings and memories'],
    archival_records: ['memory', 'memories'],
};

function counted(by: Record<string, number> | undefined): string {
    const parts: string[] = [];
    let other = 0;
    for (const [t, n] of Object.entries(by ?? {})) {
        const name = NAMES[t];
        if (name) parts.push(`${n} ${n === 1 ? name[0] : name[1]}`);
        else other += n;
    }
    if (other) parts.push(`${other} other change${other === 1 ? '' : 's'}`);
    return parts.join(', ');
}

/** The finished run, in words. */
export function describeResult(r: SyncRun): string {
    if (r.error) return `Stopped with an error during “${r.label || 'sync'}”: ${r.error}`;
    if (r.stopped || r.result?.stopped) return 'Stopped. What arrived is kept — the next sync continues from here.';
    const res = r.result ?? {};
    if (r.kind === 'index') {
        const i = res;
        return i.indexed ? `Made ${i.indexed} synced source${i.indexed === 1 ? '' : 's'} searchable.` : 'Search index up to date.';
    }
    if (r.kind === 'incoming') return `${r.name ?? 'Another Mac'} synced with this Mac (${res.done ?? r.done} changes).`;
    const got = counted(res.received);
    const sent = counted(res.sent);
    const bits = [got ? `Brought in ${got}` : 'Nothing new to bring in', sent ? `sent ${sent}` : 'nothing to send'];
    const files = (res.files?.fetched ?? 0) + (res.files?.sent ?? 0);
    if (files) bits.push(`${files} file${files === 1 ? '' : 's'}`);
    if (res.index?.indexed) bits.push(`${res.index.indexed} source${res.index.indexed === 1 ? '' : 's'} made searchable`);
    if (res.conflicts) bits.push(`${res.conflicts} conflict${res.conflicts === 1 ? '' : 's'} to review`);
    return bits.join(' · ') + '.';
}

function amount(r: SyncRun): string {
    if (!r.total && !r.done) return r.detail || '';
    const mine = r.total ? `${r.done} of ${r.total} ${r.unit}` : `${r.done} ${r.unit}`;
    return r.detail ? `${mine} · ${r.detail}` : mine;
}

function title(r: SyncRun): string {
    if (r.kind === 'index') return 'Making synced sources searchable';
    if (r.kind === 'incoming') return `${r.name ?? 'Another Mac'} is syncing with this Mac`;
    return `Syncing with ${r.name ?? 'the other Mac'}`;
}

const btn = 'rounded-lg px-3 py-1.5 text-sm disabled:opacity-40';
const secondary = `${btn} border border-gray-600 text-gray-200 hover:bg-gray-800`;

export function SyncProgress({ onRunningChange }: { onRunningChange?: (running: boolean) => void }) {
    const [runs, setRuns] = useState<SyncRun[]>([]);
    const [stopping, setStopping] = useState(false);
    const wasRunning = useRef(false);

    useEffect(() => {
        let alive = true;
        let timer: number | undefined;
        const tick = async () => {
            let running = false;
            try {
                const { data } = await api.get<{ running: boolean; runs: SyncRun[] }>('/sync/progress');
                if (!alive) return;
                setRuns(data.runs);
                running = data.running;
            } catch { /* keep the last picture; the status line shows errors */ }
            if (running !== wasRunning.current) {
                wasRunning.current = running;
                onRunningChange?.(running);
                if (!running) setStopping(false);
            }
            if (alive) timer = window.setTimeout(tick, running ? 1000 : 5000);
        };
        void tick();
        return () => { alive = false; if (timer) window.clearTimeout(timer); };
    }, [onRunningChange]);

    // Background-loop runs that moved nothing are noise; anything the user started, or
    // anything that moved data, is shown.
    const shown = runs.filter((r) => r.running ? !r.quiet || r.done > 0 || r.phase === 'index' && r.total > 0
                                                : !r.quiet || !!r.error || !!(r.result && (Object.keys(r.result.received ?? {}).length || r.result.index?.indexed)));
    const active = shown.filter((r) => r.running);
    const last = shown.find((r) => !r.running);
    if (!active.length && !last) return null;

    const stop = async () => {
        setStopping(true);
        try { await api.post('/sync/progress/cancel'); } catch { setStopping(false); }
    };

    return (
        <section className="space-y-3 rounded-lg border border-blue-500/40 bg-blue-500/5 p-4 text-sm">
            {active.map((r) => {
                const pct = r.total ? Math.min(100, Math.round((100 * r.done) / r.total)) : null;
                return (
                    <div key={r.run_id} className="space-y-2">
                        <div className="flex items-center justify-between gap-3">
                            <div className="font-semibold text-gray-100">{title(r)}…</div>
                            <div className="flex items-center gap-3">
                                <span className="text-xs text-gray-400">{Math.round(r.elapsed)} s</span>
                                {r.kind !== 'incoming' && (
                                    <button className={secondary} disabled={stopping} onClick={stop}>
                                        {stopping ? 'Stopping…' : 'Stop'}
                                    </button>
                                )}
                            </div>
                        </div>
                        {r.phases.length > 1 && (
                            <ol className="flex flex-wrap gap-x-3 gap-y-1 text-xs">
                                {r.phases.map((p) => (
                                    <li key={p.id} className={p.state === 'done' ? 'text-emerald-300' : p.state === 'current' ? 'text-blue-200' : p.state === 'failed' ? 'text-red-300' : 'text-gray-500'}>
                                        {p.state === 'done' ? '✓' : p.state === 'current' ? '●' : '○'} {p.label}
                                    </li>
                                ))}
                            </ol>
                        )}
                        <div className="h-2 w-full overflow-hidden rounded bg-gray-800">
                            <div className={`h-full bg-blue-500 transition-all ${pct === null ? 'w-1/3 animate-pulse' : ''}`}
                                 style={pct === null ? undefined : { width: `${pct}%` }} />
                        </div>
                        <div className="flex justify-between text-xs text-gray-400">
                            <span>{r.label}{amount(r) ? ` — ${amount(r)}` : ''}</span>
                            {pct !== null && <span>{pct}%</span>}
                        </div>
                    </div>
                );
            })}
            {!active.length && last && (
                <div className={last.error ? 'text-amber-200' : 'text-gray-200'}>
                    <span className="font-semibold">{last.error ? 'Sync stopped' : 'Last sync'}:</span> {describeResult(last)}
                </div>
            )}
            {!!active.length && (
                <p className="text-xs text-gray-500">
                    You can keep working. Stopping is safe — what has arrived is kept and the next sync continues.
                </p>
            )}
        </section>
    );
}
