import { useCallback, useEffect, useState } from 'react';
import { api } from '../../services/api';

/**
 * Data Health (LB-10 item 8).
 *
 * Everything else in LB-10 produces a fact — an archive exists, a drill passed,
 * a migration ran. This is the one screen where those become an answer, because
 * a backup nobody looks at is only marginally better than no backup.
 *
 * The design rule throughout: **never round "unproven" up to "fine".** A panel
 * that shows green because nothing has obviously broken converts a real risk
 * into a reassurance the user acts on. "No backup destination set" is a
 * problem, not a neutral empty state.
 */

type Overall = { ok: boolean; state: string; problems: string[]; warnings: string[] };

type Health = {
    at: string;
    overall: Overall;
    backup: any;
    drills: any;
    schema: any;
    memory: any;
    keys: any;
    codec: any;
    dead_weight: { path: string; name: string; bytes: number; why: string }[] | any;
    pending_restore: any;
};

const STATE_STYLE: Record<string, string> = {
    healthy: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200',
    warning: 'border-amber-500/40 bg-amber-500/10 text-amber-100',
    problem: 'border-red-500/40 bg-red-500/10 text-red-200',
};

function mb(bytes?: number): string {
    if (!bytes && bytes !== 0) return '—';
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
    if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
    return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

function when(value?: string | null): string {
    if (!value) return 'never';
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString();
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <div className="flex items-baseline justify-between gap-4 border-t border-gray-800 py-1.5 text-sm first:border-t-0">
            <span className="text-gray-400">{label}</span>
            <span className="text-right text-gray-200">{children}</span>
        </div>
    );
}

export function DataHealthSection() {
    const [health, setHealth] = useState<Health | null>(null);
    const [busy, setBusy] = useState<string | null>(null);
    const [note, setNote] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [destination, setDestination] = useState('');

    const refresh = useCallback(async () => {
        try {
            const { data } = await api.get<Health>('/data-health');
            setHealth(data);
            setDestination(data.backup?.destination ?? '');
            setError(null);
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? 'Could not read data health.');
        }
    }, []);

    useEffect(() => { void refresh(); }, [refresh]);

    const act = async (label: string, fn: () => Promise<any>) => {
        setBusy(label); setNote(null); setError(null);
        try {
            const result = await fn();
            setNote(result);
            await refresh();
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? `${label} failed.`);
        } finally {
            setBusy(null);
        }
    };

    // Saved, not just passed along. The original only sent `destination` with
    // each backup request and never persisted it, so the panel, the nightly job
    // and the drill all still read an empty setting — the screen said "nothing
    // is configured" while a 551 MB archive sat in the folder.
    const saveDestination = () =>
        act('Saving', async () => {
            const { data } = await api.post('/settings/backup-destination', {
                path: destination,
            });
            return data.destination
                ? `Backups will go to ${data.destination}.`
                : 'Backup destination cleared.';
        });

    const backupNow = () =>
        act('Backing up', async () => {
            await api.post('/settings/backup-destination', { path: destination });
            const { data } = await api.post('/backup', {
                destination,
                include_blobs: true,
            });
            return `Wrote ${mb(data.bytes)} in ${data.seconds}s.`;
        });

    const drillNow = () =>
        act('Drilling', async () => {
            const { data } = await api.post('/restore/drill', null, {
                params: { destination },
            });
            return data.ok
                ? 'Drill passed — that backup would restore.'
                : `Drill FAILED: ${(data.problems || []).join('; ')}`;
        });

    const checkNow = () =>
        act('Checking', async () => {
            const { data } = await api.post('/data-health/integrity-check');
            return data.ok
                ? 'All databases check out.'
                : `Problems: ${Object.keys(data.problems).join(', ')}`;
        });

    const clearJunk = () =>
        act('Clearing', async () => {
            const { data } = await api.delete('/data-health/dead-weight');
            return `Removed ${data.removed.length} leftover item(s).`;
        });

    if (!health) {
        return (
            <div className="space-y-4">
                <h2 className="text-lg font-semibold text-gray-100">Data Health</h2>
                {error ? (
                    <p className="text-sm text-red-300">{error}</p>
                ) : (
                    <p className="text-sm text-gray-500">Checking…</p>
                )}
            </div>
        );
    }

    const o = health.overall;
    const junk = Array.isArray(health.dead_weight) ? health.dead_weight : [];
    const junkBytes = junk.reduce((n, j) => n + (j.bytes || 0), 0);

    return (
        <div className="space-y-6">
            <header>
                <h2 className="text-lg font-semibold text-gray-100">Data Health</h2>
                <p className="mt-1 text-sm text-gray-400">
                    Whether your notebooks could actually be recovered, and what would stop that.
                </p>
            </header>

            {error && (
                <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-200">
                    {error}
                </div>
            )}
            {note && (
                <div className="rounded-lg border border-gray-700 bg-gray-800/60 px-4 py-3 text-sm text-gray-200">
                    {note}
                </div>
            )}

            {/* The headline. Never rounds "unproven" up to "fine". */}
            <div className={`rounded-lg border px-4 py-3 text-sm ${STATE_STYLE[o.state] ?? STATE_STYLE.warning}`}>
                <strong>
                    {o.state === 'healthy' && 'Your data is backed up and the backups are proven.'}
                    {o.state === 'warning' && 'Backed up, with something worth fixing.'}
                    {o.state === 'problem' && 'Your data is at risk.'}
                </strong>
                {(o.problems.length > 0 || o.warnings.length > 0) && (
                    <ul className="mt-2 list-disc space-y-1 pl-5">
                        {o.problems.map((p) => <li key={p}>{p}</li>)}
                        {o.warnings.map((w) => <li key={w} className="opacity-80">{w}</li>)}
                    </ul>
                )}
            </div>

            {/* ── backups ── */}
            <section className="rounded-lg border border-gray-700 bg-gray-900/40 p-4">
                <h3 className="text-sm font-semibold text-gray-200">Backups</h3>
                <p className="mt-1 text-xs text-gray-500">
                    A folder outside LocalBook's own data — iCloud Drive, an external disk, a NAS.
                    Time Machine is a bonus, never the backup.
                </p>
                <div className="mt-3 flex flex-wrap items-center gap-2">
                    <input
                        type="text"
                        value={destination}
                        onChange={(e) => setDestination(e.target.value)}
                        placeholder="/Users/you/Library/Mobile Documents/…/LocalBook Backups"
                        spellCheck={false}
                        className="min-w-0 flex-1 rounded border border-gray-600 bg-gray-800 px-2 py-1.5 font-mono text-xs text-gray-100"
                    />
                    <button
                        onClick={saveDestination}
                        disabled={!!busy}
                        className="rounded border border-gray-600 px-3 py-1.5 text-xs text-gray-200 hover:bg-gray-800 disabled:opacity-40"
                    >
                        {busy === 'Saving' ? 'Saving…' : 'Save'}
                    </button>
                    <button
                        onClick={backupNow}
                        disabled={!!busy || !destination.trim()}
                        className="rounded bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-500 disabled:opacity-40"
                    >
                        {busy === 'Backing up' ? 'Backing up…' : 'Back up now'}
                    </button>
                </div>

                <div className="mt-4">
                    <Row label="Last backup">{when(health.backup?.created_at)}</Row>
                    <Row label="Archives kept">
                        {health.backup?.count ?? 0} · {mb(health.backup?.total_bytes)}
                    </Row>
                    <Row label="Openable with your recovery phrase">
                        {health.backup?.recoverable_with_phrase === true ? 'yes'
                            : health.backup?.recoverable_with_phrase === false
                                ? <span className="text-amber-300">no — this Mac only</span>
                                : '—'}
                    </Row>
                </div>
            </section>

            {/* ── drills ── */}
            <section className="rounded-lg border border-gray-700 bg-gray-900/40 p-4">
                <div className="flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-gray-200">Restore drills</h3>
                    <button
                        onClick={drillNow}
                        disabled={!!busy || !destination.trim()}
                        className="rounded border border-gray-600 px-2.5 py-1 text-xs text-gray-300 hover:bg-gray-800 disabled:opacity-40"
                    >
                        {busy === 'Drilling' ? 'Drilling…' : 'Drill now'}
                    </button>
                </div>
                <p className="mt-1 text-xs text-gray-500">
                    A drill opens the newest backup, checks every file against its recorded hash and
                    every database against its row counts — without touching your live data. A
                    backup nobody has restored is a hypothesis.
                </p>
                <div className="mt-3">
                    <Row label="Last drill">
                        {health.drills?.last
                            ? <>{when(health.drills.last.at)} — {health.drills.last.ok
                                ? <span className="text-emerald-300">passed</span>
                                : <span className="text-red-300">failed</span>}</>
                            : 'never'}
                    </Row>
                    <Row label="Drills run">{health.drills?.runs ?? 0}</Row>
                </div>
                {health.drills?.last && !health.drills.last.ok && (
                    <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-red-300">
                        {(health.drills.last.problems || []).map((p: string) => <li key={p}>{p}</li>)}
                    </ul>
                )}
            </section>

            {/* ── the rest ── */}
            <section className="rounded-lg border border-gray-700 bg-gray-900/40 p-4">
                <div className="flex items-center justify-between">
                    <h3 className="text-sm font-semibold text-gray-200">This Mac</h3>
                    <button
                        onClick={checkNow}
                        disabled={!!busy}
                        className="rounded border border-gray-600 px-2.5 py-1 text-xs text-gray-300 hover:bg-gray-800 disabled:opacity-40"
                    >
                        {busy === 'Checking' ? 'Checking…' : 'Check databases'}
                    </button>
                </div>
                <div className="mt-3">
                    <Row label="Data format">
                        {health.schema?.version ?? '—'}
                        {health.schema?.pending ? (
                            <span className="ml-2 text-red-300">
                                {health.schema.pending} migration(s) pending
                            </span>
                        ) : null}
                    </Row>
                    <Row label="Keys recoverable from your phrase">
                        {health.keys?.fully_protected === true
                            ? 'yes'
                            : <span className="text-amber-300">
                                not all — {(health.keys?.unprotected_purposes || []).join(', ') || 'none set up'}
                            </span>}
                    </Row>
                    <Row label="Memory available to models">
                        {health.memory?.budget_gb ?? '—'} GB
                        {health.memory?.external_reserve_gb
                            ? <span className="text-gray-500"> ({health.memory.external_reserve_gb} GB reserved)</span>
                            : null}
                    </Row>
                    <Row label="Audio codec">
                        {health.codec?.ok
                            ? 'found'
                            : <span className="text-amber-300">missing — audio will not work</span>}
                    </Row>
                </div>
            </section>

            {junk.length > 0 && (
                <section className="rounded-lg border border-gray-700 bg-gray-900/40 p-4">
                    <div className="flex items-center justify-between">
                        <h3 className="text-sm font-semibold text-gray-200">
                            Leftovers · {mb(junkBytes)}
                        </h3>
                        <button
                            onClick={clearJunk}
                            disabled={!!busy}
                            className="rounded border border-gray-600 px-2.5 py-1 text-xs text-gray-300 hover:bg-gray-800 disabled:opacity-40"
                        >
                            {busy === 'Clearing' ? 'Clearing…' : 'Remove them'}
                        </button>
                    </div>
                    <ul className="mt-2 space-y-1 text-xs text-gray-400">
                        {junk.map((j) => (
                            <li key={j.path}>
                                <span className="font-mono text-gray-300">{j.name}</span>
                                {' — '}{j.why} · {mb(j.bytes)}
                            </li>
                        ))}
                    </ul>
                </section>
            )}

            <p className="text-xs text-gray-600">Checked {when(health.at)}</p>
        </div>
    );
}
