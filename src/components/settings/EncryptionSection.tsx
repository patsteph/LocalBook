import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../../services/api';

/**
 * Encryption at rest — the setup flow (LB-11).
 *
 * This is the first place anyone meets a one-way operation on their whole
 * corpus, so the screen is built around three rules:
 *
 *   1. **Say honestly what it protects.** Silent unlock means the volume opens
 *      for anything running as this user while LocalBook is open. It protects
 *      data at rest and in COPIES — not against software on the Mac. (§2 of the
 *      v2.5.0 plan; the wording below is that section's, not a softened one.)
 *   2. **Nothing is offered until every precondition holds.** Recovery phrase,
 *      a backup destination, free space — shown as a checklist, each one linking
 *      to where it is fixed, never a single disabled button with no reason.
 *   3. **Nothing is deleted until the user has seen their notebooks come back.**
 *      The plaintext copy is kept after the switch and removing it is a separate,
 *      confirmed act.
 */

type Check = { ok: boolean; [k: string]: any };

type Report = {
    ok: boolean;
    stage: string;
    bytes_total: number;
    bytes_copied: number;
    files_copied: number;
    backup_path: string | null;
    row_count_drift: Record<string, any>;
    mismatched_files: string[];
    errors: string[];
    seconds: number;
};

type SetupState = {
    encryption_enabled: boolean;
    mounted: boolean;
    preflight: { ready: boolean; checks: Record<string, Check>; data_bytes: number; data_dir: string };
    job: { running: boolean; started_at: string; finished_at?: string; report: Report } | null;
    pending: Record<string, any> | null;
    last_apply: { applied: boolean; error?: string; at?: string; plaintext_kept_at?: string } | null;
    plaintext_copies: { path: string; name: string; bytes: number }[];
};

type Props = { onNavigate?: (section: 'recovery' | 'data-health') => void };

const STAGE_LABELS: Record<string, string> = {
    queued: 'Starting…',
    starting: 'Checking…',
    'backing up': 'Taking a backup first',
    'creating the volume': 'Creating the encrypted volume',
    copying: 'Copying your data in',
    verifying: 'Verifying every database and file',
    detaching: 'Finishing up',
    staged: 'Ready',
};

function mb(bytes?: number): string {
    if (!bytes && bytes !== 0) return '—';
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
    if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(0)} MB`;
    return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

async function relaunchApp(): Promise<boolean> {
    try {
        const { relaunch } = await import('@tauri-apps/plugin-process');
        await relaunch();
        return true;
    } catch (e) {
        console.error('relaunch failed', e);
        return false;
    }
}

export function EncryptionSection({ onNavigate }: Props) {
    const [state, setState] = useState<SetupState | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [understood, setUnderstood] = useState(false);
    const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
    const [note, setNote] = useState<string | null>(null);
    const timer = useRef<number | null>(null);

    const refresh = useCallback(async () => {
        try {
            const { data } = await api.get<SetupState>('/system/volume/setup');
            setState(data);
            setError(null);
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? 'Could not read the encryption status.');
        }
    }, []);

    useEffect(() => { void refresh(); }, [refresh]);

    // Poll only while a job runs — the copy is minutes, not seconds.
    const running = !!state?.job?.running;
    useEffect(() => {
        if (!running) return;
        timer.current = window.setInterval(() => { void refresh(); }, 1000);
        return () => { if (timer.current) window.clearInterval(timer.current); };
    }, [running, refresh]);

    const start = async () => {
        setBusy(true); setError(null);
        try {
            await api.post('/system/volume/migrate', { skip_backup: false });
            await refresh();
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? 'Could not start.');
        } finally {
            setBusy(false);
        }
    };

    const cancelStaged = async () => {
        setBusy(true);
        try {
            await api.delete('/system/volume/migrate/pending');
            await refresh();
        } finally {
            setBusy(false);
        }
    };

    const restart = async () => {
        setBusy(true);
        if (!(await relaunchApp())) {
            setNote('Quit LocalBook (⌘Q) and open it again to switch over.');
            setBusy(false);
        }
    };

    const discard = async (path: string) => {
        setBusy(true); setError(null);
        try {
            const { data } = await api.delete('/system/volume/plaintext-copies', { params: { path } });
            setNote(`Unencrypted copy deleted — ${mb(data.freed_bytes)} freed.`);
            setConfirmDelete(null);
            await refresh();
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? 'Could not delete the copy.');
        } finally {
            setBusy(false);
        }
    };

    if (!state) {
        return (
            <div className="space-y-4">
                <Header />
                {error && <Banner tone="red">{error}</Banner>}
            </div>
        );
    }

    const encrypted = state.encryption_enabled && state.mounted;
    const job = state.job;
    const report = job?.report;
    const jobFailed = !!job && !job.running && !!report && !report.ok;
    const applyFailed = !!state.last_apply && !state.last_apply.applied && !encrypted && !state.pending;
    const pct = report && report.bytes_total > 0
        ? Math.min(100, Math.round((report.bytes_copied / report.bytes_total) * 100))
        : 0;

    return (
        <div className="space-y-6">
            <Header />
            {error && <Banner tone="red">{error}</Banner>}
            {note && <Banner tone="green">{note}</Banner>}

            {/* ── done ── */}
            {encrypted && (
                <Banner tone="green">
                    <strong>Encrypted.</strong> Your LocalBook data lives in an AES-256 encrypted volume
                    that unlocks automatically for your account on this Mac.
                </Banner>
            )}

            {encrypted && state.plaintext_copies.length > 0 && (
                <section className="space-y-3 rounded-lg border border-amber-500/40 bg-amber-500/5 p-4">
                    <h3 className="text-sm font-semibold text-amber-100">One last step: the unencrypted copy</h3>
                    <p className="text-sm text-gray-300">
                        The data from before the switch is still on this Mac, unencrypted, in case anything did not
                        come across. Open a few notebooks and check your sources and chats are all there. Once you are
                        satisfied, delete it — until then, the encryption protects only the new copy.
                    </p>
                    {state.plaintext_copies.map((copy) => (
                        <div key={copy.path} className="flex items-center justify-between gap-3 rounded border border-gray-700 bg-gray-900/60 px-3 py-2">
                            <div className="min-w-0">
                                <div className="truncate font-mono text-xs text-gray-300">{copy.name}</div>
                                <div className="text-xs text-gray-500">{mb(copy.bytes)}</div>
                            </div>
                            {confirmDelete === copy.path ? (
                                <div className="flex shrink-0 gap-2">
                                    <button
                                        onClick={() => void discard(copy.path)}
                                        disabled={busy}
                                        className="rounded bg-red-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-500 disabled:opacity-50"
                                    >
                                        Yes, my notebooks are fine — delete it
                                    </button>
                                    <button
                                        onClick={() => setConfirmDelete(null)}
                                        className="rounded border border-gray-600 px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-800"
                                    >
                                        Not yet
                                    </button>
                                </div>
                            ) : (
                                <button
                                    onClick={() => setConfirmDelete(copy.path)}
                                    disabled={busy}
                                    className="shrink-0 rounded border border-gray-600 px-3 py-1.5 text-xs text-gray-200 hover:bg-gray-800"
                                >
                                    Delete unencrypted copy…
                                </button>
                            )}
                        </div>
                    ))}
                </section>
            )}

            {/* ── the switch did not happen ── */}
            {applyFailed && (
                <Banner tone="amber">
                    <strong>The switch to encryption did not happen, and your data was not changed.</strong>{' '}
                    {state.last_apply?.error}
                </Banner>
            )}

            {/* ── staged, waiting for a restart ── */}
            {state.pending && !encrypted && (
                <section className="space-y-3 rounded-lg border border-blue-500/40 bg-blue-500/5 p-4">
                    <h3 className="text-sm font-semibold text-blue-100">Ready — restart to switch over</h3>
                    <p className="text-sm text-gray-300">
                        An encrypted copy of your data has been made and verified. LocalBook switches to it when it
                        next starts. Anything you do before then is carried across at restart, and your current data
                        is kept, not replaced.
                    </p>
                    <div className="flex gap-2">
                        <button
                            onClick={() => void restart()}
                            disabled={busy}
                            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-50"
                        >
                            Restart LocalBook now
                        </button>
                        <button
                            onClick={() => void cancelStaged()}
                            disabled={busy}
                            className="rounded-lg border border-gray-600 px-4 py-2 text-sm text-gray-300 hover:bg-gray-800"
                        >
                            Don't switch
                        </button>
                    </div>
                </section>
            )}

            {/* ── running ── */}
            {job?.running && report && (
                <section className="space-y-3 rounded-lg border border-gray-700 bg-gray-900/60 p-4">
                    <div className="flex items-baseline justify-between">
                        <h3 className="text-sm font-semibold text-gray-100">{STAGE_LABELS[report.stage] ?? report.stage}</h3>
                        <span className="text-xs text-gray-400">
                            {mb(report.bytes_copied)} of {mb(report.bytes_total)}
                        </span>
                    </div>
                    <div className="h-2 overflow-hidden rounded bg-gray-800">
                        <div className="h-full bg-blue-500 transition-all" style={{ width: `${pct}%` }} />
                    </div>
                    <p className="text-xs text-gray-400">
                        Please keep LocalBook open. Your data is only being read — if the app quits, nothing is lost
                        and you can start again.
                    </p>
                </section>
            )}

            {jobFailed && report && !state.pending && (
                <Banner tone="amber">
                    <strong>Encryption was not set up. Your data has not been touched.</strong>
                    <ul className="mt-1 list-disc pl-5">
                        {report.errors.map((e, i) => <li key={i}>{e}</li>)}
                        {Object.keys(report.row_count_drift ?? {}).length > 0 && (
                            <li>Row counts did not match in {Object.keys(report.row_count_drift).join(', ')}.</li>
                        )}
                        {report.mismatched_files?.length > 0 && (
                            <li>{report.mismatched_files.length} file(s) did not verify.</li>
                        )}
                    </ul>
                </Banner>
            )}

            {/* ── not started ── */}
            {!encrypted && !state.pending && !job?.running && (
                <section className="space-y-4 rounded-lg border border-gray-700 bg-gray-900/40 p-4">
                    <div className="space-y-2 text-sm text-gray-300">
                        <h3 className="font-semibold text-gray-100">What this protects — and what it doesn't</h3>
                        <p>
                            <strong className="text-gray-100">Protects your data at rest and in copies:</strong> a lost
                            or stolen Mac, a Time Machine disk, a backup agent, a copy that ends up somewhere it
                            shouldn't. Without the key, the volume is unreadable.
                        </p>
                        <p>
                            <strong className="text-gray-100">Does not protect against software on this Mac while
                            LocalBook is open.</strong> The volume unlocks automatically for your account, so anything
                            running as you can read it, just as it can today.
                        </p>
                    </div>

                    <div>
                        <h3 className="mb-2 text-sm font-semibold text-gray-100">Before you start</h3>
                        <ul className="space-y-1.5 text-sm">
                            <CheckRow ok={state.preflight.checks.recovery_phrase?.ok}>
                                A recovery phrase — the only way back in if this Mac's Keychain is lost.
                                {!state.preflight.checks.recovery_phrase?.ok && onNavigate && (
                                    <Link onClick={() => onNavigate('recovery')}>Set one up</Link>
                                )}
                            </CheckRow>
                            <CheckRow ok={state.preflight.checks.backup_destination?.ok}>
                                A backup destination — a backup is taken before anything is copied.
                                {!state.preflight.checks.backup_destination?.ok && onNavigate && (
                                    <Link onClick={() => onNavigate('data-health')}>Choose one</Link>
                                )}
                            </CheckRow>
                            <CheckRow ok={state.preflight.checks.free_space?.ok}>
                                Free space: {mb(state.preflight.checks.free_space?.needed_bytes)} needed,{' '}
                                {mb(state.preflight.checks.free_space?.free_bytes)} available.
                            </CheckRow>
                        </ul>
                        <p className="mt-2 text-xs text-gray-500">
                            {mb(state.preflight.data_bytes)} of data will be copied into the encrypted volume.
                        </p>
                    </div>

                    <label className="flex items-start gap-2 text-sm text-gray-300">
                        <input
                            type="checkbox"
                            checked={understood}
                            onChange={(e) => setUnderstood(e.target.checked)}
                            className="mt-1"
                        />
                        <span>
                            I understand what this protects, and that my recovery phrase is the only way back in if
                            this Mac's Keychain is lost.
                        </span>
                    </label>

                    <button
                        onClick={() => void start()}
                        disabled={busy || !understood || !state.preflight.ready}
                        className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-40"
                    >
                        Encrypt my data
                    </button>
                </section>
            )}
        </div>
    );
}

function Header() {
    return (
        <header>
            <h2 className="text-lg font-semibold text-gray-100">Encryption</h2>
            <p className="mt-1 text-sm text-gray-400">
                Keep everything LocalBook stores — notebooks, sources, chats, audio — in an encrypted volume on
                this Mac.
            </p>
        </header>
    );
}

function Banner({ tone, children }: { tone: 'red' | 'amber' | 'green'; children: React.ReactNode }) {
    const style = {
        red: 'border-red-500/40 bg-red-500/10 text-red-200',
        amber: 'border-amber-500/40 bg-amber-500/10 text-amber-100',
        green: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200',
    }[tone];
    return <div className={`rounded-lg border px-4 py-3 text-sm ${style}`}>{children}</div>;
}

function CheckRow({ ok, children }: { ok?: boolean; children: React.ReactNode }) {
    return (
        <li className="flex items-start gap-2">
            <span className="mt-0.5">{ok ? '✅' : '⚠️'}</span>
            <span className={ok ? 'text-gray-300' : 'text-amber-100'}>{children}</span>
        </li>
    );
}

function Link({ onClick, children }: { onClick: () => void; children: React.ReactNode }) {
    return (
        <button onClick={onClick} className="ml-2 underline underline-offset-2 hover:text-white">
            {children}
        </button>
    );
}
