import { useCallback, useEffect, useState } from 'react';
import { api } from '../../services/api';

/**
 * Settings › Sync (LB-12): this Mac's peers.
 *
 * Pairing is two Macs on the same network: one opens a 5-minute window, the
 * other connects to its address, and BOTH screens show a 6-digit code — the user
 * confirms only if the codes match (that is what defeats a man in the middle).
 * The Mac that opened the window is the seed: on first contact the other Mac's
 * matching notebooks and sources become the seed's records.
 *
 * First contact is always a preview (user decision 2026-09-30); Apply converges
 * it, after a backup on this Mac.
 */

type Device = {
    device_id: string; name?: string; host?: string; port?: number; role?: string; mode?: string;
    last_seen?: number; last_error?: string; fingerprint: string;
    preview?: { genesis?: Record<string, number>; incoming?: { inserted: number; updated: number; deleted: number; conflicts: number }; created_at?: number } | null;
    last_sync?: { at: number } | null;
};
type Pairing = { id: string; direction: 'incoming' | 'outgoing'; device_id: string; name?: string; sas: string; host?: string };
type Status = {
    enabled: boolean;
    this_mac: { device_id: string; name: string; addresses: string[]; port: number };
    listening: Record<string, boolean>;
    pairing_open_until: number;
    pairing: Pairing[];
    devices: Device[];
    open_conflicts: number | null;
    backup_destination: string | null;
    proposed_backup: string | null;
};
type Conflict = { id: string; tbl: string; field: string; kind: string; kept_value: any; other_value: any; created_at: string };

function ago(t?: number | null): string {
    if (!t) return 'never';
    const s = Math.round(Date.now() / 1000 - t);
    if (s < 60) return `${s}s ago`;
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    return new Date(t * 1000).toLocaleString();
}

function text(v: any): string {
    if (v === null || v === undefined) return '(empty)';
    return typeof v === 'string' ? v : JSON.stringify(v);
}

const btn = 'rounded-lg px-3 py-1.5 text-sm disabled:opacity-40';
const primary = `${btn} bg-blue-600 text-white hover:bg-blue-500`;
const secondary = `${btn} border border-gray-600 text-gray-200 hover:bg-gray-800`;

export function SyncSection() {
    const [st, setSt] = useState<Status | null>(null);
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [host, setHost] = useState('');
    const [folder, setFolder] = useState('');
    const [conflicts, setConflicts] = useState<Conflict[] | null>(null);

    const refresh = useCallback(async () => {
        try {
            const { data } = await api.get<Status>('/sync/status');
            setSt(data);
            setFolder((f) => f || data.backup_destination || data.proposed_backup || '');
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? 'Could not read the sync status.');
        }
    }, []);

    useEffect(() => { void refresh(); }, [refresh]);
    const windowOpen = !!st && st.pairing_open_until * 1000 > Date.now();
    useEffect(() => {
        if (!windowOpen && !(st?.pairing.length)) return;
        const t = window.setInterval(() => { void refresh(); }, 2000);
        return () => window.clearInterval(t);
    }, [windowOpen, st?.pairing.length, refresh]);

    const run = async (label: string, fn: () => Promise<any>) => {
        setBusy(label); setError(null);
        try { await fn(); await refresh(); }
        catch (e: any) { setError(e?.response?.data?.detail ?? String(e)); }
        finally { setBusy(null); }
    };

    const turnOn = () => run('enable', async () => {
        if (!st?.backup_destination) {
            await api.post('/settings/backup-destination', { path: folder, create: true });
        }
        await api.post('/sync/enable');
    });

    const loadConflicts = () => run('conflicts', async () => {
        const { data } = await api.get<{ conflicts: Conflict[] }>('/sync/conflicts');
        setConflicts(data.conflicts);
    });

    if (!st) return <div className="text-sm text-gray-400">{error ?? 'Loading…'}</div>;

    return (
        <div className="space-y-6">
            <header>
                <h2 className="text-lg font-semibold text-gray-100">Sync</h2>
                <p className="mt-1 text-sm text-gray-400">
                    Keep your notebooks, sources, notes and memory the same on your Macs — directly between them on
                    your network, encrypted and authenticated, nothing in the cloud.
                </p>
            </header>
            {error && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-200">{error}</div>}

            {/* ── on / off ── */}
            <section className="space-y-3 rounded-lg border border-gray-700 bg-gray-900/40 p-4 text-sm">
                {st.enabled ? (
                    <div className="flex items-center justify-between gap-3">
                        <div className="text-gray-300">
                            <strong className="text-emerald-300">Sync is on.</strong> This Mac is{' '}
                            <span className="text-gray-100">{st.this_mac.name}</span>, reachable at{' '}
                            <span className="font-mono text-xs">{(st.this_mac.addresses[0] ?? '?')}:{st.this_mac.port}</span>
                            {st.listening.sync ? '' : ' (listener not running)'}.
                        </div>
                        <button className={secondary} disabled={!!busy} onClick={() => run('disable', () => api.post('/sync/disable'))}>
                            Turn off
                        </button>
                    </div>
                ) : (
                    <div className="space-y-2">
                        <p className="text-gray-300">A backup of this Mac is taken before its first sync, to:</p>
                        <input value={folder} onChange={(e) => setFolder(e.target.value)} disabled={!!st.backup_destination}
                            className="w-full rounded border border-gray-600 bg-gray-800 px-2 py-1 font-mono text-xs text-gray-100" />
                        <button className={primary} disabled={!!busy || !folder} onClick={turnOn}>Turn on sync</button>
                    </div>
                )}
            </section>

            {st.enabled && (
                <>
                    {/* ── pairing ── */}
                    <section className="space-y-3 rounded-lg border border-gray-700 bg-gray-900/40 p-4 text-sm">
                        <h3 className="font-semibold text-gray-100">Pair a Mac</h3>
                        <div className="flex flex-wrap items-center gap-2">
                            <button className={secondary} disabled={!!busy || windowOpen}
                                onClick={() => run('open', () => api.post('/sync/pairing/open'))}>
                                {windowOpen ? 'Waiting for another Mac…' : 'Let another Mac pair with this one'}
                            </button>
                            {windowOpen && (
                                <span className="text-xs text-gray-400">
                                    On the other Mac, connect to <span className="font-mono">{st.this_mac.addresses[0]}</span>
                                </span>
                            )}
                        </div>
                        <div className="flex items-center gap-2">
                            <input value={host} onChange={(e) => setHost(e.target.value)} placeholder="the other Mac's address, e.g. 192.168.1.20"
                                className="flex-1 rounded border border-gray-600 bg-gray-800 px-2 py-1 text-sm text-gray-100" />
                            <button className={secondary} disabled={!!busy || !host.trim()}
                                onClick={() => run('connect', () => api.post('/sync/pairing/connect', { host }))}>
                                Connect
                            </button>
                        </div>
                        {st.pairing.map((p) => (
                            <div key={p.id} className="flex items-center justify-between gap-3 rounded border border-amber-500/40 bg-amber-500/5 px-3 py-2">
                                <div>
                                    <div className="text-gray-100">{p.name ?? p.device_id} {p.host ? <span className="text-xs text-gray-500">({p.host})</span> : null}</div>
                                    <div className="text-xs text-gray-400">Confirm only if the other Mac shows the same code:</div>
                                    <div className="mt-1 font-mono text-2xl tracking-widest text-amber-200">{p.sas.slice(0, 3)} {p.sas.slice(3)}</div>
                                </div>
                                <div className="flex gap-2">
                                    <button className={primary} disabled={!!busy} onClick={() => run('confirm', () => api.post(`/sync/pairing/${p.id}/confirm`))}>Codes match</button>
                                    <button className={secondary} disabled={!!busy} onClick={() => run('reject', () => api.post(`/sync/pairing/${p.id}/reject`))}>Cancel</button>
                                </div>
                            </div>
                        ))}
                    </section>

                    {/* ── devices ── */}
                    <section className="space-y-3 rounded-lg border border-gray-700 bg-gray-900/40 p-4 text-sm">
                        <h3 className="font-semibold text-gray-100">Paired Macs</h3>
                        {st.devices.length === 0 && <p className="text-gray-500">None yet.</p>}
                        {st.devices.map((d) => (
                            <div key={d.device_id} className="space-y-2 rounded border border-gray-700/60 bg-gray-800/40 px-3 py-2">
                                <div className="flex flex-wrap items-center justify-between gap-2">
                                    <div>
                                        <div className="text-gray-100">{d.name ?? d.device_id}
                                            <span className="ml-2 text-xs text-gray-500">{d.mode === 'live' ? 'syncing' : 'not yet applied'}{d.role === 'seed' ? ' · seed' : ''}</span>
                                        </div>
                                        <div className="text-xs text-gray-500">
                                            {d.host ?? 'address unknown'} · last contact {ago(d.last_seen)} · last sync {ago(d.last_sync?.at)}
                                        </div>
                                        {d.last_error && <div className="text-xs text-amber-300">{d.last_error}</div>}
                                    </div>
                                    <div className="flex gap-2">
                                        {d.mode !== 'live' && (
                                            <button className={secondary} disabled={!!busy || !d.host}
                                                onClick={() => run(`preview-${d.device_id}`, () => api.post(`/sync/devices/${d.device_id}/preview`))}>
                                                {busy === `preview-${d.device_id}` ? 'Previewing…' : 'Preview'}
                                            </button>
                                        )}
                                        {d.mode !== 'live' && d.preview && (
                                            <button className={primary} disabled={!!busy}
                                                onClick={() => run(`apply-${d.device_id}`, () => api.post(`/sync/devices/${d.device_id}/apply`))}>
                                                {busy === `apply-${d.device_id}` ? 'Backing up and syncing…' : 'Apply'}
                                            </button>
                                        )}
                                        {d.mode === 'live' && (
                                            <button className={secondary} disabled={!!busy || !d.host}
                                                onClick={() => run(`sync-${d.device_id}`, () => api.post(`/sync/devices/${d.device_id}/sync`))}>
                                                {busy === `sync-${d.device_id}` ? 'Syncing…' : 'Sync now'}
                                            </button>
                                        )}
                                        <button className={secondary} disabled={!!busy}
                                            onClick={() => run('revoke', () => api.post(`/sync/devices/${d.device_id}/revoke`))}>Revoke</button>
                                    </div>
                                </div>
                                {d.mode !== 'live' && d.preview && (
                                    <div className="rounded bg-gray-900/60 px-3 py-2 text-xs text-gray-300">
                                        {d.preview.genesis && (
                                            <div>
                                                Notebooks: {d.preview.genesis.notebooks_here} here, {d.preview.genesis.notebooks_there} there,{' '}
                                                {d.preview.genesis.notebooks_matched} the same → {d.preview.genesis.notebooks_after} after.
                                                Sources matched by identical text: {d.preview.genesis.sources_matched}.
                                            </div>
                                        )}
                                        {d.preview.incoming && (
                                            <div>
                                                Arriving here: {d.preview.incoming.inserted} new, {d.preview.incoming.updated} updated,{' '}
                                                {d.preview.incoming.deleted} removed, {d.preview.incoming.conflicts} conflicts
                                                {d.preview.genesis ? ' (before matching)' : ''}.
                                            </div>
                                        )}
                                        <div className="mt-1 text-gray-500">Nothing has changed yet. Apply backs this Mac up first.</div>
                                    </div>
                                )}
                            </div>
                        ))}
                        <p className="text-xs text-gray-500">Revoking stops a Mac syncing at once. It cannot erase what that Mac already has.</p>
                    </section>

                    {/* ── conflicts ── */}
                    <section className="space-y-2 rounded-lg border border-gray-700 bg-gray-900/40 p-4 text-sm">
                        <div className="flex items-center justify-between">
                            <h3 className="font-semibold text-gray-100">Conflicts {st.open_conflicts ? `(${st.open_conflicts})` : ''}</h3>
                            <button className={secondary} disabled={!!busy} onClick={loadConflicts}>Review</button>
                        </div>
                        <p className="text-xs text-gray-500">
                            When the same thing was edited on two Macs while apart, sync keeps one and saves the other here —
                            nothing is lost.
                        </p>
                        {conflicts?.map((c) => (
                            <div key={c.id} className="space-y-1 rounded border border-gray-700/60 px-3 py-2">
                                <div className="text-xs text-gray-400">{c.tbl} · {c.field} · {c.kind}</div>
                                <div className="grid grid-cols-2 gap-2 text-xs">
                                    <div><div className="text-gray-500">Kept</div><div className="max-h-24 overflow-auto whitespace-pre-wrap text-gray-200">{text(c.kept_value)}</div></div>
                                    <div><div className="text-gray-500">Other</div><div className="max-h-24 overflow-auto whitespace-pre-wrap text-gray-200">{text(c.other_value)}</div></div>
                                </div>
                                <div className="flex gap-2">
                                    <button className={secondary} disabled={!!busy}
                                        onClick={() => run('resolve', async () => { await api.post(`/sync/conflicts/${c.id}/resolve`, { keep: 'kept' }); setConflicts((x) => x?.filter((y) => y.id !== c.id) ?? null); })}>
                                        Keep this
                                    </button>
                                    {c.kind === 'concurrent-edit' && (
                                        <button className={secondary} disabled={!!busy}
                                            onClick={() => run('resolve', async () => { await api.post(`/sync/conflicts/${c.id}/resolve`, { keep: 'other' }); setConflicts((x) => x?.filter((y) => y.id !== c.id) ?? null); })}>
                                            Use the other
                                        </button>
                                    )}
                                </div>
                            </div>
                        ))}
                    </section>
                </>
            )}
        </div>
    );
}
