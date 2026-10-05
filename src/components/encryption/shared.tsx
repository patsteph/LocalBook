import type { ReactNode } from 'react';

/** Shared by the encryption wizard and Settings › Encryption (LB-11). */

export type Check = { ok: boolean; [k: string]: any };

export type Report = {
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
    staged?: boolean;
};

/** The automatic check after the swap (`services/encryption_verify.py`). */
export type Verified = {
    state: 'running' | 'done';
    ok?: boolean;
    databases?: Record<string, { integrity: string; tables: number; ok: boolean }>;
    row_count_drift?: Record<string, any>;
    db_errors?: string[];
    files?: number;
    changed_since_swap?: number;
    removed_since_swap?: number;
    changed_files?: string[];
    removed_files?: string[];
    mismatched_files?: string[];
};

export type PlaintextCopy = { path: string; name: string; bytes: number };

export type SetupState = {
    encryption_enabled: boolean;
    mounted: boolean;
    preflight: { ready: boolean; checks: Record<string, Check>; data_bytes: number; data_dir: string };
    job: { running: boolean; kind: 'encrypt' | 'decrypt' | 'export'; started_at: string; finished_at?: string; report: Report } | null;
    pending: Record<string, any> | null;
    last_apply: {
        applied: boolean; error?: string; at?: string; plaintext_kept_at?: string; verified?: Verified;
    } | null;
    plaintext_copies: PlaintextCopy[];
    decrypt_pending: Record<string, any> | null;
    last_decrypt: { applied: boolean; error?: string; at?: string } | null;
    leftover_image: { path: string; bytes: number } | null;
};

export const JOB_FAILED: Record<string, string> = {
    encrypt: 'Encryption was not set up. Your data has not been touched.',
    decrypt: 'Encryption was not turned off. Your encrypted data has not been touched.',
    export: 'The export did not complete. Your encrypted data has not been touched.',
};

export const STAGE_LABELS: Record<string, string> = {
    queued: 'Starting…',
    starting: 'Checking…',
    'backing up': 'Taking a backup first',
    'creating the volume': 'Creating the encrypted volume',
    copying: 'Copying your data in',
    verifying: 'Verifying every database and file',
    detaching: 'Finishing up',
    staged: 'Ready',
    exported: 'Exported',
};

export function mb(bytes?: number): string {
    if (!bytes && bytes !== 0) return '—';
    if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
    if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(0)} MB`;
    return `${(bytes / 1024 ** 3).toFixed(2)} GB`;
}

export async function relaunchApp(): Promise<boolean> {
    try {
        const { relaunch } = await import('@tauri-apps/plugin-process');
        await relaunch();
        return true;
    } catch (e) {
        console.error('relaunch failed', e);
        return false;
    }
}

export function Banner({ tone, children }: { tone: 'red' | 'amber' | 'green'; children: ReactNode }) {
    const style = {
        red: 'border-red-500/40 bg-red-500/10 text-red-200',
        amber: 'border-amber-500/40 bg-amber-500/10 text-amber-100',
        green: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200',
    }[tone];
    return <div className={`rounded-lg border px-4 py-3 text-sm ${style}`}>{children}</div>;
}

/** Everything that went wrong in a failed job, as list items. */
export function ReportProblems({ report }: { report: Report }) {
    return (
        <ul className="mt-1 list-disc pl-5">
            {report.errors.map((e, i) => <li key={i}>{e}</li>)}
            {Object.keys(report.row_count_drift ?? {}).length > 0 && (
                <li>Row counts did not match in {Object.keys(report.row_count_drift).join(', ')}.</li>
            )}
            {report.mismatched_files?.length > 0 && (
                <li>{report.mismatched_files.length} file(s) did not verify.</li>
            )}
        </ul>
    );
}
