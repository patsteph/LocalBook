import { describe, expect, it } from 'vitest';
import type { SetupState } from './shared';
import { initialBackupFolder, shouldAutoRestart, shouldPoll, wizardStep } from './wizardSteps';

function state(over: Partial<SetupState> = {}, checks: Record<string, any> = {}): SetupState {
    return {
        encryption_enabled: false,
        mounted: false,
        preflight: {
            ready: false, data_bytes: 1, data_dir: '/d',
            checks: {
                recovery_phrase: { ok: true },
                backup_destination: { ok: false, path: null, proposed_path: '/Users/x/LocalBook Backups' },
                free_space: { ok: true },
                ...checks,
            },
        },
        job: null,
        pending: null,
        last_apply: null,
        plaintext_copies: [],
        decrypt_pending: null,
        last_decrypt: null,
        leftover_image: null,
        ...over,
    };
}

const report = (ok: boolean) => ({
    ok, stage: 'x', bytes_total: 1, bytes_copied: 1, files_copied: 1, backup_path: null,
    row_count_drift: {}, mismatched_files: [], errors: [], seconds: 1,
});

const encrypted = (verified: any, kept = '/p/LocalBook.plaintext-1') => state({
    encryption_enabled: true,
    mounted: true,
    plaintext_copies: [{ path: '/p/LocalBook.plaintext-1', name: 'LocalBook.plaintext-1', bytes: 5 }],
    last_apply: { applied: true, plaintext_kept_at: kept, verified },
});

describe('wizardStep', () => {
    it('asks for a recovery phrase first when there is none', () => {
        expect(wizardStep(state({}, { recovery_phrase: { ok: false } }))).toBe('phrase');
    });

    it('skips the phrase step on a Mac that already has one', () => {
        expect(wizardStep(state())).toBe('encrypt');
    });

    it('follows the job: running, then staged, then failed', () => {
        expect(wizardStep(state({ job: { running: true, kind: 'encrypt', started_at: 't', report: report(false) } }))).toBe('running');
        expect(wizardStep(state({ pending: { prepared_at: 't' }, job: { running: false, kind: 'encrypt', started_at: 't', report: report(true) } }))).toBe('restart');
        expect(wizardStep(state({ job: { running: false, kind: 'encrypt', started_at: 't', report: report(false) } }))).toBe('failed');
    });

    it('an export or decrypt job never reads as a failed encryption', () => {
        expect(wizardStep(state({ job: { running: false, kind: 'export', started_at: 't', report: report(false) } }))).toBe('encrypt');
    });

    it('after the switch: verifying, then verified or not', () => {
        expect(wizardStep(encrypted(undefined))).toBe('verifying');
        expect(wizardStep(encrypted({ state: 'running' }))).toBe('verifying');
        expect(wizardStep(encrypted({ state: 'done', ok: true }))).toBe('verified');
        expect(wizardStep(encrypted({ state: 'done', ok: false }))).toBe('verify_failed');
    });

    it('a copy the check does not belong to is never "verified"', () => {
        expect(wizardStep(encrypted({ state: 'done', ok: true }, '/p/some-other-copy'))).toBe('unverified');
    });

    it('done once no copy is left', () => {
        expect(wizardStep(state({ encryption_enabled: true, mounted: true }))).toBe('done');
    });
});

describe('restart and polling', () => {
    it('restarts on its own only after watching the copy finish, and never once cancelled', () => {
        expect(shouldAutoRestart('restart', true, false)).toBe(true);
        expect(shouldAutoRestart('restart', false, false)).toBe(false);   // reopened onto a staged migration
        expect(shouldAutoRestart('restart', true, true)).toBe(false);    // Cancel leaves it staged
    });

    it('polls only while the user is waiting on the backend', () => {
        expect(shouldPoll('running')).toBe(true);
        expect(shouldPoll('verifying')).toBe(true);
        expect(shouldPoll('encrypt')).toBe(false);
        expect(shouldPoll('restart')).toBe(false);
    });
});

describe('initialBackupFolder', () => {
    it('shows the saved folder, else the proposed default', () => {
        expect(initialBackupFolder(state())).toBe('/Users/x/LocalBook Backups');
        expect(initialBackupFolder(state({}, { backup_destination: { ok: true, path: '/Volumes/B', proposed_path: null } }))).toBe('/Volumes/B');
    });
});
