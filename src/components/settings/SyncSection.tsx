import { useCallback, useEffect, useState } from 'react';
import { api } from '../../services/api';
import { SyncProgress } from './SyncProgress';
import { conflictLabel, conflictValue } from './conflictLabel';

/**
 * Settings › Sync (LB-12): this Mac's peers.
 *
 * Pairing is two Macs on the same network. A Mac with sync on and no partner is
 * always ready to be paired; the other Mac lists it (Bonjour) and pairs with one
 * click — or types its address where multicast is blocked. BOTH screens then show
 * a 6-digit code and the user confirms only if they match (that is what defeats a
 * man in the middle). The Mac that was paired WITH is the seed: on first contact
 * the other Mac's matching notebooks and sources become the seed's records.
 *
 * First contact is always a preview (user decision 2026-09-30) — it now runs by
 * itself once both Macs confirm — and Start sync converges it, after a backup.
 * Every sync runs in the background with progress (SyncProgress), 2026-10-01.
 */

type Device = {
    device_id: string; name?: string; host?: string; port?: number; role?: string; mode?: string;
    last_seen?: number; last_error?: string; fingerprint: string;
    preview?: { genesis?: Record<string, number>; incoming?: { inserted: number; updated: number; deleted: number; conflicts: number }; created_at?: number } | null;
    last_sync?: { at: number } | null;
    model_mismatch?: Record<string, { here: string; there: string }>;
    preview_state?: string | null;
};
type FoundMac = { name: string; host: string; port: number; device_id?: string | null };
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
    collector?: { chosen: { device_id: string; name?: string } | null; here: boolean; reason: string };
    retention?: {
        last: { at: number; tombstones: Record<string, number>; logs: Record<string, number>; conflicts: number } | null;
        held_by: { device_id: string; name?: string; days: number | null }[];
    };
};
type Conflict = { id: string; tbl: string; pk?: string; field: string; kind: string; kept_value: any; other_value: any; created_at: string };

function ago(t?: number | null): string {
    if (!t) return 'never';
    const s = Math.round(Date.now() / 1000 - t);
    if (s < 60) return `${s}s ago`;
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    return new Date(t * 1000).toLocaleString();
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
    const [found, setFound] = useState<FoundMac[] | null>(null);
    const [syncing, setSyncing] = useState(false);

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
    const waitingPreview = !!st?.devices.some((d) => d.preview_state);
    useEffect(() => {
        if (!windowOpen && !(st?.pairing.length) && !waitingPreview) return;
        const t = window.setInterval(() => { void refresh(); }, 2000);
        return () => window.clearInterval(t);
    }, [windowOpen, st?.pairing.length, waitingPreview, refresh]);

    // A run finishing changes last-sync times, previews and conflicts.
    const onRunningChange = useCallback((running: boolean) => {
        setSyncing(running);
        if (!running) void refresh();
    }, [refresh]);

    const discover = useCallback(async () => {
        setFound(null);
        try {
            const { data } = await api.get<{ macs: FoundMac[] }>('/sync/discover');
            setFound(data.macs);
        } catch { setFound([]); }
    }, []);
    useEffect(() => { if (st?.enabled) void discover(); }, [st?.enabled, discover]);

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
                        {st.backup_destination ? (
                            <p className="text-gray-300">
                                A backup of this Mac is taken before its first sync, to{' '}
                                <span className="font-mono text-xs">{st.backup_destination}</span>.
                            </p>
                        ) : (
                            <>
                                <p className="text-gray-300">A backup of this Mac is taken before its first sync, to:</p>
                                <input value={folder} onChange={(e) => setFolder(e.target.value)}
                                    className="w-full rounded border border-gray-600 bg-gray-800 px-2 py-1 font-mono text-xs text-gray-100" />
                            </>
                        )}
                        <button className={primary} disabled={!!busy || !folder} onClick={turnOn}>Turn on sync</button>
                    </div>
                )}
            </section>

            {st.enabled && (
                <>
                    <SyncProgress onRunningChange={onRunningChange} />

                    {/* ── pairing ── */}
                    <section className="space-y-3 rounded-lg border border-gray-700 bg-gray-900/40 p-4 text-sm">
                        <div className="flex items-center justify-between gap-2">
                            <h3 className="font-semibold text-gray-100">Pair a Mac</h3>
                            <button className={secondary} disabled={!!busy || found === null} onClick={() => void discover()}>
                                {found === null ? 'Looking…' : 'Look again'}
                            </button>
                        </div>
                        {found && found.length > 0 && (
                            <ul className="space-y-2">
                                {found.map((m) => (
                                    <li key={`${m.device_id}-${m.host}`} className="flex items-center justify-between gap-3 rounded border border-gray-700/60 px-3 py-2">
                                        <div>
                                            <div className="text-gray-100">{m.name}</div>
                                            <div className="text-xs text-gray-500">{m.host}</div>
                                        </div>
                                        <button className={primary} disabled={!!busy}
                                            onClick={() => run(`pair-${m.host}`, () => api.post('/sync/pairing/connect', { host: m.host, port: m.port }))}>
                                            {busy === `pair-${m.host}` ? 'Asking…' : 'Pair'}
                                        </button>
                                    </li>
                                ))}
                            </ul>
                        )}
                        {found && found.length === 0 && (
                            <p className="text-xs text-gray-500">
                                No other LocalBook found on this network. Turn sync on there, or enter its address below
                                (some work networks block finding Macs automatically).
                            </p>
                        )}
                        <div className="flex flex-wrap items-center gap-2 text-xs text-gray-400">
                            {windowOpen ? (
                                <span>This Mac is ready to be paired — choose <span className="text-gray-200">{st.this_mac.name}</span> on the other Mac.</span>
                            ) : (
                                <button className={secondary} disabled={!!busy}
                                    onClick={() => run('open', () => api.post('/sync/pairing/open'))}>
                                    Let another Mac pair with this one
                                </button>
                            )}
                        </div>
                        <details className="text-xs text-gray-400">
                            <summary className="cursor-pointer">Enter an address instead</summary>
                            <div className="mt-2 flex items-center gap-2">
                                <input value={host} onChange={(e) => setHost(e.target.value)} placeholder="the other Mac's address, e.g. 192.168.1.20"
                                    className="flex-1 rounded border border-gray-600 bg-gray-800 px-2 py-1 text-sm text-gray-100" />
                                <button className={secondary} disabled={!!busy || !host.trim()}
                                    onClick={() => run('connect', () => api.post('/sync/pairing/connect', { host }))}>
                                    Connect
                                </button>
                            </div>
                            <div className="mt-1">This Mac: <span className="font-mono">{st.this_mac.addresses[0] ?? '?'}</span></div>
                        </details>
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
                                        {d.preview_state && <div className="text-xs text-blue-200">{d.preview_state}…</div>}
                                        {d.last_error && <div className="text-xs text-amber-300">{d.last_error}</div>}
                                        {Object.entries(d.model_mismatch ?? {}).map(([role, m]) => (
                                            <div key={role} className="text-xs text-amber-300">
                                                Different {role} model — this Mac: {m.here.split('/').pop()}, {d.name ?? 'that Mac'}: {m.there.split('/').pop()}
                                            </div>
                                        ))}
                                    </div>
                                    <div className="flex gap-2">
                                        {d.mode !== 'live' && d.preview && (
                                            <button className={primary} disabled={!!busy || syncing}
                                                onClick={() => run(`apply-${d.device_id}`, () => api.post(`/sync/devices/${d.device_id}/apply`))}>
                                                Start sync
                                            </button>
                                        )}
                                        {d.mode !== 'live' && !d.preview_state && (
                                            <button className={secondary} disabled={!!busy || syncing || !d.host}
                                                onClick={() => run(`preview-${d.device_id}`, () => api.post(`/sync/devices/${d.device_id}/preview`))}>
                                                {busy === `preview-${d.device_id}` ? 'Previewing…' : d.preview ? 'Preview again' : 'Preview'}
                                            </button>
                                        )}
                                        {d.mode === 'live' && (
                                            <button className={secondary} disabled={!!busy || syncing || !d.host}
                                                onClick={() => run(`sync-${d.device_id}`, () => api.post(`/sync/devices/${d.device_id}/sync`))}>
                                                Sync now
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
                                        <div className="mt-1 text-gray-500">Nothing has changed yet. Start sync backs this Mac up first, then shows its progress above.</div>
                                    </div>
                                )}
                            </div>
                        ))}
                        {st.retention?.held_by.map((h) => (
                            <p key={h.device_id} className="text-xs text-amber-300">
                                {h.name ?? 'A paired Mac'} hasn't synced {h.days != null ? `in ${h.days} days` : 'yet'} — deleted
                                items are kept until it returns (so it learns of them), or until you revoke it.
                            </p>
                        ))}
                        {st.retention?.last && (
                            <p className="text-xs text-gray-500">
                                Last cleanup {ago(st.retention.last.at)}:{' '}
                                {Object.values(st.retention.last.tombstones).reduce((a, b) => a + b, 0)} old deletes,{' '}
                                {Object.values(st.retention.last.logs).reduce((a, b) => a + b, 0)} old log entries
                                {st.retention.last.conflicts ? `, ${st.retention.last.conflicts} resolved conflicts` : ''} removed.
                            </p>
                        )}
                        <p className="text-xs text-gray-500">Revoking stops a Mac syncing at once. It cannot erase what that Mac already has.</p>
                    </section>

                    {/* ── which Mac collects ── */}
                    {st.devices.length > 0 && (
                        <section className="space-y-2 rounded-lg border border-gray-700 bg-gray-900/40 p-4 text-sm">
                            <h3 className="font-semibold text-gray-100">Collections</h3>
                            <div className="flex flex-wrap items-center gap-2">
                                <span className="text-gray-300">Scheduled collections run on</span>
                                <select
                                    className="rounded border border-gray-600 bg-gray-800 px-2 py-1 text-sm text-gray-100"
                                    disabled={!!busy}
                                    value={st.collector?.chosen?.device_id ?? ''}
                                    onChange={(e) => run('collector', () => api.post('/sync/collector', { device_id: e.target.value }))}>
                                    {!st.collector?.chosen && <option value="">every Mac (not chosen)</option>}
                                    <option value={st.this_mac.device_id}>{st.this_mac.name} (this Mac)</option>
                                    {st.devices.map((d) => <option key={d.device_id} value={d.device_id}>{d.name ?? d.device_id}</option>)}
                                </select>
                            </div>
                            <p className="text-xs text-gray-500">
                                One Mac searches and collects on schedule; sync brings what it finds to the others, so the
                                work isn't repeated and the same article isn't added twice. "Collect now" still works on
                                any Mac. {st.collector?.reason ? `This Mac: ${st.collector.reason}.` : ''}
                            </p>
                        </section>
                    )}

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
                                <div className="text-xs text-gray-400">{conflictLabel(c)}{c.kind && c.kind !== 'concurrent-edit' ? ` · ${c.kind.replace(/-/g, ' ')}` : ''}</div>
                                <div className="grid grid-cols-2 gap-2 text-xs">
                                    <div><div className="text-gray-500">Kept</div><div className="max-h-24 overflow-auto whitespace-pre-wrap text-gray-200">{conflictValue(c, c.kept_value)}</div></div>
                                    <div><div className="text-gray-500">Other</div><div className="max-h-24 overflow-auto whitespace-pre-wrap text-gray-200">{conflictValue(c, c.other_value)}</div></div>
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
