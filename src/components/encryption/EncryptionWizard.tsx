import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../../services/api';
import { pickFolder } from '../../services/folders';
import { Modal } from '../shared/Modal';
import { RecoveryPhraseStep } from './RecoveryPhraseStep';
import {
    Banner, JOB_FAILED, ReportProblems, STAGE_LABELS, mb, relaunchApp, type SetupState,
} from './shared';
import {
    RESTART_COUNTDOWN_SECONDS, currentVerification, initialBackupFolder, shouldAutoRestart,
    shouldPoll, wizardStep,
} from './wizardSteps';

/**
 * Encryption at rest, in one window (LB-11 — the simplified flow).
 *
 * Design of record: READFIRST/planning/lb11-simplified-flow.md. ~6 actions for a
 * new user, 3 for a Mac that already has a phrase and a backup folder, and no
 * safety step removed: the phrase is still typed back, the backup is still taken
 * first, and the plaintext copy is still kept until the user removes it — now on
 * the strength of an automatic check of every table and file, shown to them.
 *
 * Every step is derived from the backend (`wizardStep`), so closing, reopening,
 * or restarting mid-way always lands on the right screen.
 */
export function EncryptionWizard({ isOpen, onClose }: { isOpen: boolean; onClose: () => void }) {
    const [state, setState] = useState<SetupState | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [folder, setFolder] = useState('');
    const [countdown, setCountdown] = useState(RESTART_COUNTDOWN_SECONDS);
    const [cancelled, setCancelled] = useState(false);
    const [confirmRemove, setConfirmRemove] = useState(false);
    const [note, setNote] = useState<string | null>(null);
    const sawRunning = useRef(false);

    const refresh = useCallback(async () => {
        try {
            const { data } = await api.get<SetupState>('/system/volume/setup');
            setState(data);
            setError(null);
            setFolder((f) => f || initialBackupFolder(data));
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? 'Could not read the encryption status.');
        }
    }, []);

    useEffect(() => {
        if (!isOpen) return;
        setCancelled(false); setConfirmRemove(false); setNote(null);
        setCountdown(RESTART_COUNTDOWN_SECONDS);
        sawRunning.current = false;
        void refresh();
    }, [isOpen, refresh]);

    const step = wizardStep(state);
    if (step === 'running') sawRunning.current = true;

    useEffect(() => {
        if (!isOpen || !shouldPoll(step)) return;
        const t = window.setInterval(() => { void refresh(); }, 1000);
        return () => window.clearInterval(t);
    }, [isOpen, step, refresh]);

    const restart = useCallback(async () => {
        setBusy(true);
        if (!(await relaunchApp())) {
            setNote('Quit LocalBook (⌘Q) and open it again to switch over.');
            setBusy(false);
        }
    }, []);

    const autoRestart = isOpen && shouldAutoRestart(step, sawRunning.current, cancelled);
    useEffect(() => {
        if (!autoRestart) return;
        if (countdown <= 0) { void restart(); return; }
        const t = window.setTimeout(() => setCountdown((n) => n - 1), 1000);
        return () => window.clearTimeout(t);
    }, [autoRestart, countdown, restart]);

    const encryptNow = async () => {
        if (!state) return;
        setBusy(true); setError(null);
        try {
            const saved = state.preflight.checks.backup_destination?.path;
            if (folder !== saved) {
                // Only ever the folder shown on this screen — the default is saved
                // here, when the user presses the button, never silently.
                await api.post('/settings/backup-destination', { path: folder, create: true });
            }
            await api.post('/system/volume/migrate', { skip_backup: false });
            await refresh();
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? 'Could not start.');
            await refresh();
        } finally {
            setBusy(false);
        }
    };

    const dontSwitch = async () => {
        setBusy(true);
        try {
            await api.delete('/system/volume/migrate/pending');
            await refresh();
        } finally {
            setBusy(false);
        }
    };

    const removeCopy = async () => {
        if (!state) return;
        const kept = state.last_apply?.plaintext_kept_at;
        const copies = step === 'unverified' ? state.plaintext_copies : state.plaintext_copies.filter((c) => c.path === kept);
        setBusy(true); setError(null);
        try {
            let freed = 0;
            for (const c of copies) {
                const { data } = await api.delete('/system/volume/plaintext-copies', { params: { path: c.path } });
                freed += data.freed_bytes ?? 0;
            }
            setNote(`Unencrypted copy removed — ${mb(freed)} freed.`);
            setConfirmRemove(false);
            await refresh();
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? 'Could not remove the copy.');
        } finally {
            setBusy(false);
        }
    };

    const changeFolder = async () => {
        const picked = await pickFolder();
        if (picked) setFolder(picked);
    };

    const report = state?.job?.report;
    const pct = report && report.bytes_total > 0
        ? Math.min(100, Math.round((report.bytes_copied / report.bytes_total) * 100))
        : 0;
    const applyFailed = !!state?.last_apply && !state.last_apply.applied;
    const v = state ? currentVerification(state) : undefined;
    const primary = 'rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-40';
    const secondary = 'rounded-lg border border-gray-600 px-4 py-2 text-sm text-gray-300 hover:bg-gray-800 disabled:opacity-40';

    return (
        <Modal isOpen={isOpen} onClose={onClose} title="Encrypt your LocalBook data" size="lg">
            <div className="space-y-5 p-5 text-gray-200">
                <StepDots step={step} />
                {error && <Banner tone="red">{error}</Banner>}
                {note && <Banner tone="green">{note}</Banner>}

                {step === 'loading' && <p className="text-sm text-gray-400">Checking this Mac…</p>}

                {/* ① recovery phrase */}
                {step === 'phrase' && (
                    <>
                        <p className="text-sm text-gray-300">
                            First, a recovery phrase — the only way back into your encrypted data if this Mac's
                            Keychain is ever lost.
                        </p>
                        <RecoveryPhraseStep confirmLabel="Continue" onConfirmed={() => void refresh()} />
                    </>
                )}

                {/* ② encrypt */}
                {(step === 'encrypt' || step === 'failed') && state && (
                    <>
                        {step === 'failed' && report && (
                            <Banner tone="amber">
                                <strong>{JOB_FAILED.encrypt}</strong>
                                <ReportProblems report={report} />
                            </Banner>
                        )}
                        {applyFailed && step === 'encrypt' && (
                            <Banner tone="amber">
                                <strong>The last switch to encryption did not happen, and your data was not
                                changed.</strong>{' '}{state.last_apply?.error}
                            </Banner>
                        )}
                        <div className="space-y-2 text-sm text-gray-300">
                            <p>
                                Your notebooks, sources, chats and audio move into an AES-256 encrypted volume that
                                unlocks automatically for your account on this Mac. That protects them <strong
                                className="text-gray-100">at rest and in copies</strong> — a lost Mac, a backup
                                disk, a copy that ends up somewhere it shouldn't.
                            </p>
                            <p className="text-gray-400">
                                It does not protect against software running as you while LocalBook is open — that
                                can read your data, just as it can today.
                            </p>
                        </div>

                        <div className="space-y-2 rounded-lg border border-gray-700 bg-gray-900/40 p-4 text-sm">
                            <div className="flex items-center justify-between gap-3">
                                <div className="min-w-0">
                                    <div className="text-gray-400">A backup is taken first, to</div>
                                    <div className="truncate font-mono text-xs text-gray-200" title={folder}>
                                        {folder || 'no folder chosen'}
                                    </div>
                                </div>
                                <button onClick={() => void changeFolder()} disabled={busy} className="shrink-0 rounded border border-gray-600 px-3 py-1.5 text-xs text-gray-200 hover:bg-gray-800">
                                    Change…
                                </button>
                            </div>
                            <div className={state.preflight.checks.free_space?.ok ? 'text-gray-400' : 'text-amber-200'}>
                                {state.preflight.checks.free_space?.ok ? '✓ ' : '⚠️ '}
                                {mb(state.preflight.data_bytes)} of data ·{' '}
                                {mb(state.preflight.checks.free_space?.needed_bytes)} of space needed,{' '}
                                {mb(state.preflight.checks.free_space?.free_bytes)} free
                            </div>
                        </div>

                        <button
                            onClick={() => void encryptNow()}
                            disabled={busy || !folder || !state.preflight.checks.free_space?.ok}
                            className={primary}
                        >
                            Encrypt now
                        </button>
                    </>
                )}

                {step === 'running' && report && (
                    <section className="space-y-3">
                        <div className="flex items-baseline justify-between">
                            <h3 className="text-sm font-semibold text-gray-100">{STAGE_LABELS[report.stage] ?? report.stage}</h3>
                            <span className="text-xs text-gray-400">{mb(report.bytes_copied)} of {mb(report.bytes_total)}</span>
                        </div>
                        <div className="h-2 overflow-hidden rounded bg-gray-800">
                            <div className="h-full bg-blue-500 transition-all" style={{ width: `${pct}%` }} />
                        </div>
                        <p className="text-xs text-gray-400">
                            Keep LocalBook open. Your data is only being read — if the app quits, nothing is lost.
                        </p>
                    </section>
                )}

                {step === 'restart' && (
                    <section className="space-y-3">
                        <p className="text-sm text-gray-300">
                            An encrypted copy has been made and verified. LocalBook switches to it when it restarts;
                            anything you do before then is carried across, and your current data is kept.
                        </p>
                        {autoRestart ? (
                            <div className="flex items-center gap-3">
                                <span className="text-sm text-gray-100">Restarting in {countdown} s…</span>
                                <button onClick={() => setCancelled(true)} className={secondary}>Cancel</button>
                            </div>
                        ) : (
                            <div className="flex gap-2">
                                <button onClick={() => void restart()} disabled={busy} className={primary}>
                                    Restart LocalBook now
                                </button>
                                <button onClick={() => void dontSwitch()} disabled={busy} className={secondary}>
                                    Don't switch
                                </button>
                            </div>
                        )}
                    </section>
                )}

                {/* ③ after the restart */}
                {step === 'verifying' && (
                    <p className="text-sm text-gray-300">
                        🔒 Encrypted. Checking every database and file against the copy from before…
                    </p>
                )}

                {step === 'verified' && v && (
                    <section className="space-y-3">
                        <Banner tone="green">
                            <strong>Encrypted ✓ — verified:</strong> {v.files ?? 0} files and{' '}
                            {Object.keys(v.databases ?? {}).length} databases match the copy from before.
                        </Banner>
                        {((v.changed_since_swap ?? 0) + (v.removed_since_swap ?? 0)) > 0 && (
                            <details className="text-xs text-gray-400">
                                <summary className="cursor-pointer">
                                    {(v.changed_since_swap ?? 0) + (v.removed_since_swap ?? 0)} file(s) LocalBook
                                    itself changed since the switch were not compared
                                </summary>
                                <ul className="mt-1 list-disc pl-5 font-mono">
                                    {(v.changed_files ?? []).map((f) => <li key={`c${f}`}>{f} (changed)</li>)}
                                    {(v.removed_files ?? []).map((f) => <li key={`r${f}`}>{f} (removed)</li>)}
                                </ul>
                            </details>
                        )}
                        <RemoveCopy
                            text="The unencrypted copy from before is still on this Mac. Until it goes, the encryption only protects the new copy."
                            confirmText="Remove the unencrypted copy? Your encrypted data is verified."
                            confirm={confirmRemove} busy={busy}
                            onAsk={() => setConfirmRemove(true)} onCancel={() => setConfirmRemove(false)}
                            onRemove={() => void removeCopy()}
                        />
                    </section>
                )}

                {step === 'verify_failed' && v && (
                    <Banner tone="amber">
                        <strong>Encrypted, but the check found differences — the unencrypted copy is kept.</strong>
                        <ul className="mt-1 list-disc pl-5">
                            {(v.db_errors ?? []).map((e, i) => <li key={i}>{e}</li>)}
                            {Object.keys(v.row_count_drift ?? {}).length > 0 && (
                                <li>Row counts differ in {Object.keys(v.row_count_drift ?? {}).join(', ')}.</li>
                            )}
                            {(v.mismatched_files ?? []).length > 0 && (
                                <li>{v.mismatched_files!.length} file(s) differ, e.g. {v.mismatched_files!.slice(0, 3).join(', ')}.</li>
                            )}
                        </ul>
                        <p className="mt-2">Nothing is lost. Settings › Encryption can export a decrypted copy or turn encryption off.</p>
                    </Banner>
                )}

                {step === 'unverified' && state && (
                    <RemoveCopy
                        text={`An unencrypted copy from an earlier switch (${mb(state.plaintext_copies.reduce((n, c) => n + c.bytes, 0))}) was made before the automatic check existed. Open a few notebooks first, then remove it.`}
                        confirmText="Remove it? Only if your notebooks look right."
                        confirm={confirmRemove} busy={busy}
                        onAsk={() => setConfirmRemove(true)} onCancel={() => setConfirmRemove(false)}
                        onRemove={() => void removeCopy()}
                    />
                )}

                {step === 'done' && (
                    <section className="space-y-3">
                        <Banner tone="green">
                            <strong>All done.</strong> Your LocalBook data is encrypted on this Mac. Settings ›
                            Encryption has export and turn-off if you ever need them.
                        </Banner>
                        <button onClick={onClose} className={primary}>Close</button>
                    </section>
                )}
            </div>
        </Modal>
    );
}

function RemoveCopy(props: {
    text: string; confirmText: string; confirm: boolean; busy: boolean;
    onAsk: () => void; onCancel: () => void; onRemove: () => void;
}) {
    return (
        <div className="space-y-3">
            <p className="text-sm text-gray-300">{props.text}</p>
            {props.confirm ? (
                <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm text-gray-100">{props.confirmText}</span>
                    <button onClick={props.onRemove} disabled={props.busy} className="rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-500 disabled:opacity-40">
                        Remove
                    </button>
                    <button onClick={props.onCancel} disabled={props.busy} className="rounded-lg border border-gray-600 px-4 py-2 text-sm text-gray-300 hover:bg-gray-800">
                        Not yet
                    </button>
                </div>
            ) : (
                <button onClick={props.onAsk} disabled={props.busy} className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-40">
                    Remove the old unencrypted copy
                </button>
            )}
        </div>
    );
}

const DOTS: [string, string[]][] = [
    ['Recovery phrase', ['phrase']],
    ['Encrypt', ['encrypt', 'failed', 'running', 'restart']],
    ['Finish', ['verifying', 'verified', 'verify_failed', 'unverified', 'done']],
];

function StepDots({ step }: { step: string }) {
    const at = DOTS.findIndex(([, steps]) => steps.includes(step));
    if (at < 0) return null;
    return (
        <ol className="flex gap-4 text-xs">
            {DOTS.map(([label], i) => (
                <li key={label} className={i === at ? 'font-semibold text-blue-300' : i < at ? 'text-gray-400' : 'text-gray-600'}>
                    {i < at ? '✓' : i + 1}. {label}
                </li>
            ))}
        </ol>
    );
}
