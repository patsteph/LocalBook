import { useCallback, useEffect, useRef, useState } from 'react';
import { emitEvent } from '../../lib/events';
import { api } from '../../services/api';
import { pickFolder } from '../../services/folders';
import {
    Banner, JOB_FAILED, ReportProblems, STAGE_LABELS, mb, relaunchApp, type SetupState,
} from '../encryption/shared';

/**
 * Encryption at rest — managing it (LB-11).
 *
 * Setting it up is the wizard's job (`encryption/EncryptionWizard`, owned by
 * EncryptionPrompt); this screen hands off to it and keeps status, the
 * unencrypted-copy removal, export and turn-off.
 *
 * This is the first place anyone meets a one-way operation on their whole
 * corpus, so the screen is built around three rules:
 *
 *   1. **Say honestly what it protects.** Silent unlock means the volume opens
 *      for anything running as this user while LocalBook is open. It protects
 *      data at rest and in COPIES — not against software on the Mac. (§2 of the
 *      v2.5.0 plan; the wording below is that section's, not a softened one.)
 *   2. **Nothing is deleted until the user has seen their notebooks come back.**
 *      The plaintext copy is kept after the switch and removing it is a separate,
 *      confirmed act.
 */

export function EncryptionSection() {
    const [state, setState] = useState<SetupState | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
    const [note, setNote] = useState<string | null>(null);
    const [confirmOff, setConfirmOff] = useState(false);
    const [confirmImage, setConfirmImage] = useState(false);
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

    const exportCopy = async () => {
        setError(null); setNote(null);
        const folder = await pickFolder();
        if (!folder) return;
        setBusy(true);
        try {
            await api.post('/system/volume/export', { destination: folder });
            await refresh();
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? 'Could not start the export.');
        } finally {
            setBusy(false);
        }
    };

    const turnOff = async () => {
        setBusy(true); setError(null);
        try {
            await api.post('/system/volume/decrypt');
            setConfirmOff(false);
            await refresh();
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? 'Could not start.');
        } finally {
            setBusy(false);
        }
    };

    const cancelDecrypt = async () => {
        setBusy(true);
        try {
            await api.delete('/system/volume/decrypt/pending');
            await refresh();
        } finally {
            setBusy(false);
        }
    };

    const discardImage = async () => {
        setBusy(true); setError(null);
        try {
            const { data } = await api.delete('/system/volume/image');
            setNote(`Encrypted image deleted — ${mb(data.freed_bytes)} freed.`);
            setConfirmImage(false);
            await refresh();
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? 'Could not delete the image.');
        } finally {
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
    const exported = job?.kind === 'export' && !job.running && report?.ok;
    const applyFailed = !!state.last_apply && !state.last_apply.applied && !encrypted && !state.pending;
    const decryptFailed = !!state.last_decrypt && !state.last_decrypt.applied && encrypted && !state.decrypt_pending;
    const decryptedRecently = !!state.last_decrypt?.applied && !state.encryption_enabled;
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

            {exported && report && (
                <Banner tone="green">
                    <strong>Exported.</strong> A decrypted, verified copy is at{' '}
                    <span className="font-mono text-xs">{report.backup_path}</span>. It is an ordinary LocalBook
                    data folder — and it is not encrypted, so keep it somewhere safe.
                </Banner>
            )}

            {jobFailed && report && !state.pending && (
                <Banner tone="amber">
                    <strong>{JOB_FAILED[job?.kind ?? 'encrypt']}</strong>
                    <ReportProblems report={report} />
                </Banner>
            )}

            {/* ── the escape hatch ── */}
            {decryptFailed && (
                <Banner tone="amber">
                    <strong>Encryption was not turned off, and your data was not changed.</strong>{' '}
                    {state.last_decrypt?.error}
                </Banner>
            )}

            {state.decrypt_pending && (
                <section className="space-y-3 rounded-lg border border-blue-500/40 bg-blue-500/5 p-4">
                    <h3 className="text-sm font-semibold text-blue-100">Ready — restart to turn encryption off</h3>
                    <p className="text-sm text-gray-300">
                        A decrypted copy has been made and verified. LocalBook switches to it when it next starts, and
                        carries over anything you do before then. The encrypted volume is kept until you delete it.
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
                            onClick={() => void cancelDecrypt()}
                            disabled={busy}
                            className="rounded-lg border border-gray-600 px-4 py-2 text-sm text-gray-300 hover:bg-gray-800"
                        >
                            Keep encryption on
                        </button>
                    </div>
                </section>
            )}

            {encrypted && !state.decrypt_pending && !job?.running && (
                <section className="space-y-3 rounded-lg border border-gray-700 bg-gray-900/40 p-4">
                    <h3 className="text-sm font-semibold text-gray-100">Getting your data back out</h3>
                    <p className="text-sm text-gray-400">
                        Export a decrypted copy to a folder you choose (encryption stays on), or turn encryption off
                        entirely. Both copies are verified before anything else happens.
                    </p>
                    <div className="flex flex-wrap gap-2">
                        <button
                            onClick={() => void exportCopy()}
                            disabled={busy}
                            className="rounded border border-gray-600 px-3 py-1.5 text-sm text-gray-200 hover:bg-gray-800 disabled:opacity-50"
                        >
                            Export a decrypted copy…
                        </button>
                        {confirmOff ? (
                            <>
                                <button
                                    onClick={() => void turnOff()}
                                    disabled={busy}
                                    className="rounded bg-amber-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-amber-500 disabled:opacity-50"
                                >
                                    Yes, turn encryption off
                                </button>
                                <button
                                    onClick={() => setConfirmOff(false)}
                                    className="rounded border border-gray-600 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-800"
                                >
                                    Cancel
                                </button>
                            </>
                        ) : (
                            <button
                                onClick={() => setConfirmOff(true)}
                                disabled={busy}
                                className="rounded border border-gray-600 px-3 py-1.5 text-sm text-gray-200 hover:bg-gray-800 disabled:opacity-50"
                            >
                                Turn encryption off…
                            </button>
                        )}
                    </div>
                </section>
            )}

            {decryptedRecently && (
                <Banner tone="green">
                    <strong>Encryption is off.</strong> Your data is back in an ordinary folder.
                </Banner>
            )}

            {state.leftover_image && (
                <section className="flex items-center justify-between gap-3 rounded-lg border border-gray-700 bg-gray-900/40 p-4">
                    <div className="min-w-0 text-sm text-gray-300">
                        The encrypted volume from before is still on this Mac ({mb(state.leftover_image.bytes)}).
                        Once your notebooks look right, it can go.
                    </div>
                    {confirmImage ? (
                        <div className="flex shrink-0 gap-2">
                            <button
                                onClick={() => void discardImage()}
                                disabled={busy}
                                className="rounded bg-red-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-red-500 disabled:opacity-50"
                            >
                                Delete it
                            </button>
                            <button
                                onClick={() => setConfirmImage(false)}
                                className="rounded border border-gray-600 px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-800"
                            >
                                Not yet
                            </button>
                        </div>
                    ) : (
                        <button
                            onClick={() => setConfirmImage(true)}
                            disabled={busy}
                            className="shrink-0 rounded border border-gray-600 px-3 py-1.5 text-xs text-gray-200 hover:bg-gray-800"
                        >
                            Delete encrypted volume…
                        </button>
                    )}
                </section>
            )}

            {/* ── not started: the wizard does the setup ── */}
            {!encrypted && !state.pending && !job?.running && (
                <section className="space-y-3 rounded-lg border border-gray-700 bg-gray-900/40 p-4 text-sm text-gray-300">
                    <p>
                        <strong className="text-gray-100">Protects your data at rest and in copies:</strong> a lost
                        or stolen Mac, a Time Machine disk, a backup agent, a copy that ends up somewhere it
                        shouldn't. <strong className="text-gray-100">Not</strong> against software running as you
                        while LocalBook is open — the volume unlocks automatically for your account.
                    </p>
                    <button
                        onClick={() => emitEvent('lb:openEncryptionWizard')}
                        className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500"
                    >
                        Set up encryption
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
