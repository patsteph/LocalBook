import type { SetupState, Verified } from './shared';

/**
 * Which screen the encryption wizard shows, derived from the backend's state
 * alone — so reopening the wizard, or a restart in the middle, always lands on
 * the right step. Pure, so it is tested without a DOM.
 */
export type WizardStep =
    | 'loading'
    | 'phrase'          // ① save a recovery phrase (skipped when one exists)
    | 'encrypt'         // ② explanation + backup folder + [Encrypt now]
    | 'running'         //    the background copy
    | 'failed'          //    the copy failed; nothing was touched
    | 'restart'         //    staged — countdown / Restart now / Don't switch
    | 'verifying'       // ③ the automatic check is still running
    | 'verified'        //    check passed — offer one-click removal
    | 'verify_failed'   //    check found differences — the copy is kept
    | 'unverified'      //    a copy from before the check existed
    | 'done';           //    encrypted, nothing left over

/** The check that belongs to the copy the last swap kept, if that copy is still here. */
export function currentVerification(s: SetupState): Verified | null | undefined {
    const kept = s.last_apply?.plaintext_kept_at;
    if (!kept || !s.plaintext_copies.some((c) => c.path === kept)) return undefined;   // no check will come
    return s.last_apply?.verified ?? null;                                               // null: not started yet
}

export function wizardStep(s: SetupState | null): WizardStep {
    if (!s) return 'loading';

    if (s.encryption_enabled && s.mounted) {
        if (s.plaintext_copies.length === 0) return 'done';
        const v = currentVerification(s);
        if (v === undefined) return 'unverified';
        if (v === null || v.state !== 'done') return 'verifying';
        return v.ok ? 'verified' : 'verify_failed';
    }

    const job = s.job;
    if (job?.running) return 'running';
    if (s.pending) return 'restart';
    if (job && job.kind === 'encrypt' && !job.report.ok) return 'failed';
    if (!s.preflight.checks.recovery_phrase?.ok) return 'phrase';
    return 'encrypt';
}

/** Poll while the backend is doing something the user is waiting on. */
export function shouldPoll(step: WizardStep): boolean {
    return step === 'running' || step === 'verifying';
}

/**
 * Restart on its own only when THIS wizard watched the copy finish. Reopening
 * the wizard onto a migration staged earlier must not restart the app under
 * the user without a click.
 */
export function shouldAutoRestart(step: WizardStep, sawRunning: boolean, cancelled: boolean): boolean {
    return step === 'restart' && sawRunning && !cancelled;
}

export const RESTART_COUNTDOWN_SECONDS = 5;

/** The backup folder to show pre-filled: the saved one, else the proposed default. */
export function initialBackupFolder(s: SetupState): string {
    const c = s.preflight.checks.backup_destination ?? {};
    return (c.path as string | null) ?? (c.proposed_path as string | null) ?? '';
}
