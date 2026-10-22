import { useCallback, useEffect, useMemo, useState } from 'react';
import { Laptop, MoreHorizontal, Smartphone } from 'lucide-react';
import { api } from '../../services/api';
import { SyncProgress } from './SyncProgress';
import { conflictLabel, conflictValue } from './conflictLabel';
import { EmbeddingSwitchPanel } from '../llm/EmbeddingSwitchPanel';

/**
 * Settings › Sync (LB-12), redesigned 2026-10-21 to four calm zones:
 *
 *   1. header    one switch + one status line
 *   2. devices   everything paired (Macs and phones) as one list; address/port on hover only;
 *                pairing offers nearby devices with a single button
 *   3. what syncs  notebooks, each with a switch; off = kept on this Mac only (no other device gets it)
 *   4. footer    what has synced, when, when next, and anything that needs attention
 *
 * Everything that is rarely needed (which Mac collects, conflicts, clean-up, backup folder) sits under
 * "Advanced". The mechanics are unchanged from before: pairing is two devices on the same network with a
 * 6-digit code both screens must show; the Mac paired WITH is the seed; first contact is always a preview
 * and Start sync converges it after a backup; every sync runs in the background with progress (SyncProgress).
 */

type Device = {
    device_id: string; name?: string; host?: string; port?: number; role?: string; mode?: string;
    last_seen?: number; last_error?: string; fingerprint: string;
    preview?: { genesis?: Record<string, number>; incoming?: { inserted: number; updated: number; deleted: number; conflicts: number }; created_at?: number } | null;
    last_sync?: { at: number; line?: string | null } | null;
    model_mismatch?: Record<string, { here: string; there: string }>;
    preview_state?: string | null;
};
type Phone = { device_id: string; name?: string | null; paired_at?: number | null; last_seen?: number | null };
type FoundMac = { name: string; host: string; port: number; device_id?: string | null };
type Pairing = { id: string; direction: 'incoming' | 'outgoing'; device_id: string; name?: string; sas: string; host?: string };
type Status = {
    enabled: boolean;
    this_mac: { device_id: string; name: string; addresses: string[]; port: number };
    listening: Record<string, boolean>;
    pairing_open_until: number;
    pairing: Pairing[];
    devices: Device[];
    phones?: Phone[];
    open_conflicts: number | null;
    stats?: { notebooks: number | null; sources: number | null };
    interval_seconds?: number;
    backup_destination: string | null;
    proposed_backup: string | null;
    collector?: { chosen: { device_id: string; name?: string } | null; here: boolean; reason: string };
    retention?: {
        last: { at: number; tombstones: Record<string, number>; logs: Record<string, number>; conflicts: number } | null;
        held_by: { device_id: string; name?: string; days: number | null }[];
    };
    // This device searches with a different embedder than the rest of the library.
    embedder_advice?: {
        library_model: string; this_device: string; devices: string[];
        downloaded: boolean; in_progress: boolean; message: string;
    } | null;
};
type Conflict = { id: string; tbl: string; pk?: string; field: string; kind: string; kept_value: any; other_value: any; created_at: string };
type SyncNotebook = { id: string; title: string; syncs: boolean; phones_only_off?: boolean };

function ago(t?: number | null): string {
    if (!t) return 'never';
    const s = Math.round(Date.now() / 1000 - t);
    if (s < 60) return `${Math.max(s, 0)}s ago`;
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    return new Date(t * 1000).toLocaleString();
}

function inFuture(seconds: number): string {
    if (seconds <= 5) return 'any moment';
    if (seconds < 90) return `in ${Math.round(seconds)}s`;
    return `in ${Math.round(seconds / 60)} min`;
}

const btn = 'rounded-lg px-3 py-1.5 text-sm disabled:opacity-40';
const primary = `${btn} bg-blue-600 text-white hover:bg-blue-500`;
const secondary = `${btn} border border-gray-600 text-gray-200 hover:bg-gray-800`;
const card = 'space-y-3 rounded-lg border border-gray-700 bg-gray-900/40 p-4 text-sm';
const zoneTitle = 'text-xs font-semibold uppercase tracking-wide text-gray-400';

function Switch({ on, disabled, onChange, label }: { on: boolean; disabled?: boolean; onChange: (v: boolean) => void; label: string }) {
    return (
        <button
            role="switch" aria-checked={on} aria-label={label} disabled={disabled} onClick={() => onChange(!on)}
            className={`relative h-6 w-11 shrink-0 rounded-full transition-colors disabled:opacity-40 ${on ? 'bg-emerald-500' : 'bg-gray-600'}`}>
            <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white transition-all ${on ? 'left-[22px]' : 'left-0.5'}`} />
        </button>
    );
}

export function SyncSection() {
    const [st, setSt] = useState<Status | null>(null);
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [host, setHost] = useState('');
    const [folder, setFolder] = useState('');
    const [conflicts, setConflicts] = useState<Conflict[] | null>(null);
    const [found, setFound] = useState<FoundMac[] | null>(null);
    const [syncing, setSyncing] = useState(false);
    const [notebooks, setNotebooks] = useState<SyncNotebook[] | null>(null);
    const [menuFor, setMenuFor] = useState<string | null>(null);
    const [detailsFor, setDetailsFor] = useState<string | null>(null);
    const [advancedOpen, setAdvancedOpen] = useState(false);
    const [now, setNow] = useState(() => Date.now());

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
    // "last synced / next sync" read from the clock; keep them honest without hammering the backend.
    useEffect(() => {
        const t = window.setInterval(() => { setNow(Date.now()); void refresh(); }, 30000);
        return () => window.clearInterval(t);
    }, [refresh]);

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

    const loadNotebooks = useCallback(async () => {
        try {
            const { data } = await api.get<{ notebooks: SyncNotebook[] }>('/sync/notebooks');
            setNotebooks(data.notebooks);
        } catch { setNotebooks(null); }
    }, []);
    useEffect(() => { if (st?.enabled) void loadNotebooks(); }, [st?.enabled, loadNotebooks]);

    const run = async (label: string, fn: () => Promise<any>) => {
        setBusy(label); setError(null);
        try { await fn(); await refresh(); }
        catch (e: any) { setError(e?.response?.data?.detail ?? String(e)); }
        finally { setBusy(null); }
    };

    // Off = this notebook stays on this device: it syncs with no other device (Mac or phone).
    const toggleNotebook = (id: string, syncs: boolean) => run(`nb-${id}`, async () => {
        const next = (notebooks ?? []).map((n) => (n.id === id ? { ...n, syncs } : n));
        const { data } = await api.post<{ notebooks: SyncNotebook[] }>(
            '/sync/notebooks', { keep_local: next.filter((n) => !n.syncs).map((n) => n.id) });
        setNotebooks(data.notebooks);
    });

    // A notebook from the older setting that syncs between Macs but is not shared with phones: lift that, nothing else.
    const shareWithPhones = (id: string) => run(`nb-${id}`, async () => {
        const { data } = await api.post<{ notebooks: SyncNotebook[] }>(
            '/sync/notebooks', { keep_local: (notebooks ?? []).filter((n) => !n.syncs).map((n) => n.id), share_with_phones: [id] });
        setNotebooks(data.notebooks);
    });

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

    // ── derived view ────────────────────────────────────────────────────────────
    const paired = useMemo(() => new Set((st?.devices ?? []).map((d) => d.device_id)), [st?.devices]);
    const nearby = (found ?? []).filter((m) => !m.device_id || !paired.has(m.device_id));

    if (!st) return <div className="text-sm text-gray-400">{error ?? 'Loading…'}</div>;

    const lastSyncAt = Math.max(0, ...st.devices.map((d) => d.last_sync?.at ?? 0));
    const liveMacs = st.devices.filter((d) => d.mode === 'live');
    const issues: { key: string; text: string; action?: { label: string; run: () => void } }[] = [];
    for (const d of st.devices) {
        if (d.last_error) issues.push({ key: `err-${d.device_id}`, text: `${d.name ?? 'A Mac'}: ${d.last_error}` });
        for (const [role, m] of Object.entries(d.model_mismatch ?? {})) {
            issues.push({ key: `mm-${d.device_id}-${role}`, text: `${d.name ?? 'A Mac'} uses a different ${role} model (${m.there.split('/').pop()} vs ${m.here.split('/').pop()})` });
        }
    }
    if (st.open_conflicts) {
        issues.push({
            key: 'conflicts', text: `${st.open_conflicts} edit${st.open_conflicts === 1 ? '' : 's'} to review`,
            action: { label: 'Review', run: () => { setAdvancedOpen(true); void loadConflicts(); } },
        });
    }
    for (const h of st.retention?.held_by ?? []) {
        issues.push({ key: `held-${h.device_id}`, text: `${h.name ?? 'A paired Mac'} hasn't synced ${h.days != null ? `in ${h.days} days` : 'yet'}` });
    }

    let statusLine: { text: string; tone: string };
    if (!st.enabled) statusLine = { text: 'Sync is off', tone: 'text-gray-400' };
    else if (syncing) statusLine = { text: 'Syncing…', tone: 'text-blue-300' };
    else if (issues.length > 0) statusLine = { text: `${issues.length} thing${issues.length === 1 ? '' : 's'} need${issues.length === 1 ? 's' : ''} attention`, tone: 'text-amber-300' };
    else if (st.devices.length === 0 && (st.phones?.length ?? 0) === 0) statusLine = { text: 'On — nothing paired yet', tone: 'text-gray-300' };
    else statusLine = { text: lastSyncAt ? `Up to date · last synced ${ago(lastSyncAt)}` : 'On — waiting for the first sync', tone: 'text-emerald-300' };

    const nextSync = (() => {
        if (!st.enabled || liveMacs.length === 0) return '—';
        return inFuture(lastSyncAt + (st.interval_seconds ?? 60) - now / 1000);
    })();

    return (
        <div className="space-y-6">
            {/* ── 1. header ── */}
            <header className="flex items-start justify-between gap-4">
                <div>
                    <h2 className="text-lg font-semibold text-gray-100">Sync</h2>
                    <p className={`mt-1 text-sm ${statusLine.tone}`}>{statusLine.text}</p>
                </div>
                <Switch
                    on={st.enabled} disabled={!!busy || (!st.enabled && !st.backup_destination && !folder)} label="Sync"
                    onChange={(v) => {
                        if (v) void turnOn();
                        else if (window.confirm('Turn sync off? Your devices keep everything they have; they just stop updating each other.')) {
                            void run('disable', () => api.post('/sync/disable'));
                        }
                    }} />
            </header>
            {error && <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-200">{error}</div>}

            {!st.enabled && (
                <section className={card}>
                    <p className="text-gray-300">
                        Keeps your notebooks, sources, notes and memory the same across your devices — directly between them
                        on your network, encrypted, nothing in the cloud. You choose which notebooks stay on this Mac only.
                    </p>
                    {!st.backup_destination && (
                        <div className="space-y-2">
                            <p className="text-gray-300">A backup of this Mac is taken before its first sync, to:</p>
                            <input value={folder} onChange={(e) => setFolder(e.target.value)}
                                className="w-full rounded border border-gray-600 bg-gray-800 px-2 py-1 font-mono text-xs text-gray-100" />
                        </div>
                    )}
                    <p className="text-xs text-gray-500">Use the switch above to turn sync on.</p>
                </section>
            )}

            {st.enabled && (
                <>
                    {st.embedder_advice && <EmbedderAdvice advice={st.embedder_advice} />}
                    <SyncProgress onRunningChange={onRunningChange} />

                    {/* ── 2. devices ── */}
                    <section className={card}>
                        <h3 className={zoneTitle}>Devices</h3>

                        {st.pairing.map((p) => (
                            <div key={p.id} className="flex items-center justify-between gap-3 rounded border border-amber-500/40 bg-amber-500/5 px-3 py-2">
                                <div>
                                    <div className="text-gray-100">Pairing with {p.name ?? p.device_id}</div>
                                    <div className="text-xs text-gray-400">Confirm only if the other screen shows the same code:</div>
                                    <div className="mt-1 font-mono text-2xl tracking-widest text-amber-200">{p.sas.slice(0, 3)} {p.sas.slice(3)}</div>
                                </div>
                                <div className="flex gap-2">
                                    <button className={primary} disabled={!!busy} onClick={() => run('confirm', () => api.post(`/sync/pairing/${p.id}/confirm`))}>Codes match</button>
                                    <button className={secondary} disabled={!!busy} onClick={() => run('reject', () => api.post(`/sync/pairing/${p.id}/reject`))}>Cancel</button>
                                </div>
                            </div>
                        ))}

                        {st.devices.length === 0 && (st.phones?.length ?? 0) === 0 && <p className="text-gray-500">No devices paired yet.</p>}

                        <ul className="space-y-2">
                            {st.devices.map((d) => {
                                const live = d.mode === 'live';
                                const bad = !!d.last_error || Object.keys(d.model_mismatch ?? {}).length > 0;
                                const recent = (d.last_seen ?? 0) * 1000 > now - 5 * 60 * 1000;
                                const dot = bad ? 'bg-amber-400' : live && recent ? 'bg-emerald-400' : 'bg-gray-500';
                                const sub = d.preview_state ? `${d.preview_state}…`
                                    : !live ? (d.preview ? 'Ready to start' : 'Paired — not syncing yet')
                                    : `Synced ${ago(d.last_sync?.at)}${d.last_sync?.line ? ` · ${d.last_sync.line}` : ''}`;
                                return (
                                    <li key={d.device_id} className="rounded border border-gray-700/60 bg-gray-800/40 px-3 py-2">
                                        <div className="flex items-center justify-between gap-3">
                                            <div className="flex min-w-0 items-center gap-3">
                                                <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${dot}`} />
                                                <Laptop size={16} className="shrink-0 text-gray-400" />
                                                <div className="min-w-0">
                                                    <div className="truncate text-gray-100" title={d.host ? `${d.host}${d.port ? `:${d.port}` : ''}` : 'address unknown'}>
                                                        {d.name ?? d.device_id}{d.role === 'seed' ? <span className="ml-2 text-xs text-gray-500">seed</span> : null}
                                                    </div>
                                                    <div className="text-xs text-gray-500">{sub}</div>
                                                </div>
                                            </div>
                                            <div className="relative flex shrink-0 items-center gap-2">
                                                {!live && d.preview && (
                                                    <button className={primary} disabled={!!busy || syncing}
                                                        onClick={() => run(`apply-${d.device_id}`, () => api.post(`/sync/devices/${d.device_id}/apply`))}>
                                                        Start sync
                                                    </button>
                                                )}
                                                {!live && !d.preview && !d.preview_state && (
                                                    <button className={secondary} disabled={!!busy || syncing || !d.host}
                                                        onClick={() => run(`preview-${d.device_id}`, () => api.post(`/sync/devices/${d.device_id}/preview`))}>
                                                        {busy === `preview-${d.device_id}` ? 'Checking…' : 'Check first'}
                                                    </button>
                                                )}
                                                {live && (
                                                    <button className={secondary} disabled={!!busy || syncing || !d.host}
                                                        onClick={() => run(`sync-${d.device_id}`, () => api.post(`/sync/devices/${d.device_id}/sync`))}>
                                                        Sync now
                                                    </button>
                                                )}
                                                <button aria-label="More" className="rounded p-1 text-gray-400 hover:bg-gray-700"
                                                    onClick={() => setMenuFor(menuFor === d.device_id ? null : d.device_id)}>
                                                    <MoreHorizontal size={16} />
                                                </button>
                                                {menuFor === d.device_id && (
                                                    <div className="absolute right-0 top-9 z-10 w-44 rounded border border-gray-600 bg-gray-900 py-1 text-sm shadow-lg">
                                                        {!live && d.preview && (
                                                            <button className="block w-full px-3 py-1.5 text-left text-gray-200 hover:bg-gray-800"
                                                                onClick={() => { setDetailsFor(detailsFor === d.device_id ? null : d.device_id); setMenuFor(null); }}>
                                                                What will change
                                                            </button>
                                                        )}
                                                        <button className="block w-full px-3 py-1.5 text-left text-red-300 hover:bg-gray-800"
                                                            onClick={() => {
                                                                setMenuFor(null);
                                                                if (window.confirm(`Remove ${d.name ?? 'this Mac'}? It stops syncing at once. It cannot be made to forget what it already has.`)) {
                                                                    void run('revoke', () => api.post(`/sync/devices/${d.device_id}/revoke`));
                                                                }
                                                            }}>
                                                            Remove device
                                                        </button>
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                        {detailsFor === d.device_id && !live && d.preview && (
                                            <div className="mt-2 rounded bg-gray-900/60 px-3 py-2 text-xs text-gray-300">
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
                                                        {d.preview.incoming.deleted} removed, {d.preview.incoming.conflicts} conflicts.
                                                    </div>
                                                )}
                                                <div className="mt-1 text-gray-500">Nothing has changed yet. Start sync backs this Mac up first.</div>
                                            </div>
                                        )}
                                    </li>
                                );
                            })}

                            {(st.phones ?? []).map((p) => {
                                const recent = (p.last_seen ?? 0) * 1000 > now - 24 * 3600 * 1000;
                                return (
                                    <li key={p.device_id} className="flex items-center justify-between gap-3 rounded border border-gray-700/60 bg-gray-800/40 px-3 py-2">
                                        <div className="flex min-w-0 items-center gap-3">
                                            <span className={`h-2.5 w-2.5 shrink-0 rounded-full ${recent ? 'bg-emerald-400' : 'bg-gray-500'}`} />
                                            <Smartphone size={16} className="shrink-0 text-gray-400" />
                                            <div className="min-w-0">
                                                <div className="truncate text-gray-100">{p.name ?? p.device_id}</div>
                                                <div className="text-xs text-gray-500">Phone · last contact {ago(p.last_seen)}</div>
                                            </div>
                                        </div>
                                        <button className={secondary} disabled={!!busy}
                                            onClick={() => {
                                                if (window.confirm(`Remove ${p.name ?? 'this phone'}? It can no longer read your library.`)) {
                                                    void run('revoke-phone', () => api.post(`/sync/phones/${p.device_id}/revoke`));
                                                }
                                            }}>
                                            Remove
                                        </button>
                                    </li>
                                );
                            })}
                        </ul>

                        {/* nearby, one click */}
                        <div className="border-t border-gray-700/60 pt-3">
                            <div className="mb-2 flex items-center justify-between">
                                <h4 className="text-xs font-medium text-gray-400">Available nearby</h4>
                                <button className="text-xs text-blue-300 hover:underline disabled:opacity-40" disabled={!!busy || found === null} onClick={() => void discover()}>
                                    {found === null ? 'Looking…' : 'Look again'}
                                </button>
                            </div>
                            {nearby.length > 0 ? (
                                <ul className="space-y-2">
                                    {nearby.map((m) => (
                                        <li key={`${m.device_id}-${m.host}`} className="flex items-center justify-between gap-3 rounded border border-gray-700/60 px-3 py-2">
                                            <div className="flex items-center gap-3" title={`${m.host}:${m.port}`}>
                                                <Laptop size={16} className="text-gray-400" />
                                                <span className="text-gray-100">{m.name}</span>
                                            </div>
                                            <button className={primary} disabled={!!busy}
                                                onClick={() => run(`pair-${m.host}`, () => api.post('/sync/pairing/connect', { host: m.host, port: m.port }))}>
                                                {busy === `pair-${m.host}` ? 'Asking…' : 'Pair'}
                                            </button>
                                        </li>
                                    ))}
                                </ul>
                            ) : found !== null && (
                                <p className="text-xs text-gray-500">No other LocalBook found on this network. Turn sync on there as well.</p>
                            )}
                            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-gray-400">
                                {windowOpen
                                    ? <span>Ready to be found — choose <span className="text-gray-200">{st.this_mac.name}</span> on the other device.</span>
                                    : <button className="text-blue-300 hover:underline disabled:opacity-40" disabled={!!busy}
                                        onClick={() => run('open', () => api.post('/sync/pairing/open'))}>Let another device pair with this one</button>}
                                <details>
                                    <summary className="cursor-pointer text-blue-300">Enter an address</summary>
                                    <div className="mt-2 flex items-center gap-2">
                                        <input value={host} onChange={(e) => setHost(e.target.value)} placeholder="e.g. 192.168.1.20"
                                            className="w-44 rounded border border-gray-600 bg-gray-800 px-2 py-1 text-sm text-gray-100" />
                                        <button className={secondary} disabled={!!busy || !host.trim()}
                                            onClick={() => run('connect', () => api.post('/sync/pairing/connect', { host }))}>Connect</button>
                                    </div>
                                </details>
                            </div>
                        </div>
                    </section>

                    {/* ── 3. what syncs ── */}
                    {notebooks && notebooks.length > 0 && (
                        <section className={card}>
                            <div>
                                <h3 className={zoneTitle}>What syncs</h3>
                                <p className="mt-1 text-xs text-gray-500">
                                    Turn a notebook off to keep it on this device only — it won't sync with any other device.
                                    New notebooks sync. Devices that already have a copy keep it.
                                </p>
                            </div>
                            <ul className="divide-y divide-gray-800">
                                {notebooks.map((n) => (
                                    <li key={n.id} className="flex items-center justify-between gap-3 py-2">
                                        <span className="min-w-0 truncate text-gray-200">
                                            {n.title}
                                            {n.phones_only_off && (
                                                <span className="ml-2 text-xs text-amber-300">
                                                    not shared with phones (earlier setting){' '}
                                                    <button className="text-blue-300 hover:underline" disabled={!!busy} onClick={() => void shareWithPhones(n.id)}>Share</button>
                                                </span>
                                            )}
                                        </span>
                                        <span className="flex shrink-0 items-center gap-2 text-xs text-gray-400">
                                            <span className={n.syncs ? '' : 'text-amber-300'}>{n.syncs ? 'Sync this notebook' : 'Kept on this device only'}</span>
                                            <Switch on={n.syncs} disabled={!!busy} label={`Sync ${n.title}`} onChange={(v) => void toggleNotebook(n.id, v)} />
                                        </span>
                                    </li>
                                ))}
                            </ul>
                        </section>
                    )}

                    {/* ── 4. footer ── */}
                    <section className={card}>
                        <h3 className={zoneTitle}>Status</h3>
                        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                            <div><dt className="text-xs text-gray-500">Synced</dt>
                                <dd className="text-gray-100">{st.stats?.notebooks ?? '—'} notebooks · {st.stats?.sources ?? '—'} sources</dd></div>
                            <div><dt className="text-xs text-gray-500">Last sync</dt><dd className="text-gray-100">{lastSyncAt ? ago(lastSyncAt) : '—'}</dd></div>
                            <div><dt className="text-xs text-gray-500">Next sync</dt><dd className="text-gray-100">{nextSync}</dd></div>
                            <div><dt className="text-xs text-gray-500">Issues</dt>
                                <dd className={issues.length ? 'text-amber-300' : 'text-emerald-300'}>{issues.length ? issues.length : 'None'}</dd></div>
                        </dl>
                        {issues.length > 0 && (
                            <ul className="space-y-1">
                                {issues.map((i) => (
                                    <li key={i.key} className="flex items-center justify-between gap-3 rounded bg-amber-500/10 px-3 py-1.5 text-xs text-amber-200">
                                        <span>{i.text}</span>
                                        {i.action && <button className="text-blue-300 hover:underline" onClick={i.action.run}>{i.action.label}</button>}
                                    </li>
                                ))}
                            </ul>
                        )}
                    </section>

                    {/* ── advanced ── */}
                    <details className={card} open={advancedOpen} onToggle={(e) => setAdvancedOpen((e.target as HTMLDetailsElement).open)}>
                        <summary className="cursor-pointer text-gray-300">Advanced</summary>
                        <div className="mt-3 space-y-4">
                            <div className="text-xs text-gray-400">
                                This Mac: <span className="text-gray-200">{st.this_mac.name}</span>{' '}
                                <span className="font-mono">({st.this_mac.addresses[0] ?? '?'}:{st.this_mac.port})</span>
                                {st.listening.sync ? '' : ' — listener not running'}
                                {st.backup_destination && <> · backup before first sync: <span className="font-mono">{st.backup_destination}</span></>}
                            </div>

                            {st.devices.length > 0 && (
                                <div className="space-y-2">
                                    <h4 className="font-medium text-gray-100">Collections</h4>
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
                                        One Mac searches and collects on schedule; sync brings what it finds to the others, so the work isn't repeated.
                                        "Collect now" still works on any Mac. {st.collector?.reason ? `This Mac: ${st.collector.reason}.` : ''}
                                    </p>
                                </div>
                            )}

                            <div className="space-y-2">
                                <div className="flex items-center justify-between">
                                    <h4 className="font-medium text-gray-100">Conflicts {st.open_conflicts ? `(${st.open_conflicts})` : ''}</h4>
                                    <button className={secondary} disabled={!!busy} onClick={loadConflicts}>Review</button>
                                </div>
                                <p className="text-xs text-gray-500">
                                    When the same thing was edited on two Macs while apart, sync keeps one and saves the other here — nothing is lost.
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
                            </div>

                            {st.retention?.last && (
                                <p className="text-xs text-gray-500">
                                    Last clean-up {ago(st.retention.last.at)}:{' '}
                                    {Object.values(st.retention.last.tombstones).reduce((a, b) => a + b, 0)} old deletes,{' '}
                                    {Object.values(st.retention.last.logs).reduce((a, b) => a + b, 0)} old log entries
                                    {st.retention.last.conflicts ? `, ${st.retention.last.conflicts} resolved conflicts` : ''} removed.
                                    Deleted items are kept until every paired Mac has seen the delete.
                                </p>
                            )}
                        </div>
                    </details>
                </>
            )}
        </div>
    );
}


function EmbedderAdvice({ advice }: { advice: NonNullable<Status['embedder_advice']> }) {
    const [open, setOpen] = useState(false);
    const [note, setNote] = useState<string | null>(null);
    const download = async () => {
        try {
            await api.post('/settings/mlx/download', { model_id: advice.library_model });
            setNote('Downloading — follow it in LLM Studio, then come back here to switch.');
        } catch (e: any) {
            setNote(e?.response?.data?.detail ?? 'Could not start the download.');
        }
    };
    return (
        <section className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-4 text-sm text-amber-100">
            <div className="font-medium">Search model differs from your other devices</div>
            <p className="mt-1">{advice.message}</p>
            {!advice.in_progress && (
                <div className="mt-3">
                    {advice.downloaded
                        ? <button onClick={() => setOpen(true)} className={primary}>Switch search model…</button>
                        : <button onClick={download} className={primary}>Download {advice.library_model.split('/').pop()}</button>}
                </div>
            )}
            {note && <p className="mt-2 text-xs">{note}</p>}
            {open && <EmbeddingSwitchPanel target={advice.library_model} onClose={() => setOpen(false)} />}
        </section>
    );
}
