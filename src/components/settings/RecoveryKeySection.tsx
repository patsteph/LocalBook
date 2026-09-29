import { useCallback, useEffect, useState } from 'react';
import { api } from '../../services/api';

/**
 * Recovery phrase setup (K-1).
 *
 * The screen exists because of an asymmetry that is easy to get wrong: LocalBook's
 * credential key used to be DERIVED from the machine name, so losing the Keychain
 * cost nothing. It is now random and lives only in the Keychain, which is far
 * better against a stolen backup and far worse against a wiped Keychain — unless
 * a copy exists, wrapped to a key only this phrase can produce.
 *
 * Two rules shape the flow:
 *   1. `begin` commits nothing. The phrase is generated and shown, and only the
 *      type-back confirms it. A phrase the user never wrote down, silently
 *      accepted, would read as "protected" forever while being worth nothing.
 *   2. "Configured" and "protected" are different claims, and the banner shows
 *      the second one. A key created before setup has no wrapped copy until
 *      the backfill runs.
 */

type Status = {
    device_id: string;
    recovery_key_configured: boolean;
    unprotected_purposes: string[];
    fully_protected: boolean;
    purposes: Record<string, { in_keychain: boolean; wrapped: boolean; error: string | null }>;
};

type BeginPayload = {
    phrase: string;
    words: string[];
    verify_indices: number[];
    already_configured: boolean;
};

const PURPOSE_LABELS: Record<string, string> = {
    credentials: 'Site logins, email accounts and saved browser sessions',
    backup: 'Encrypted backups',
    device_identity: "This Mac's identity for syncing",
};

export function RecoveryKeySection() {
    const [status, setStatus] = useState<Status | null>(null);
    const [begun, setBegun] = useState<BeginPayload | null>(null);
    const [typed, setTyped] = useState<Record<number, string>>({});
    const [acknowledged, setAcknowledged] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [done, setDone] = useState<string[] | null>(null);
    const [checkPhrase, setCheckPhrase] = useState('');
    const [checkResult, setCheckResult] = useState<boolean | null>(null);

    const refresh = useCallback(async () => {
        try {
            const { data } = await api.get<Status>('/keyvault/status');
            setStatus(data);
        } catch {
            setStatus(null);
        }
    }, []);

    useEffect(() => { void refresh(); }, [refresh]);

    const begin = async () => {
        setBusy(true); setError(null); setDone(null);
        try {
            const { data } = await api.post<BeginPayload>('/keyvault/recovery/begin');
            setBegun(data);
            setTyped({});
            setAcknowledged(false);
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? 'Could not generate a recovery phrase.');
        } finally {
            setBusy(false);
        }
    };

    const confirm = async () => {
        if (!begun) return;
        setBusy(true); setError(null);
        try {
            const { data } = await api.post('/keyvault/recovery/confirm', {
                phrase: begun.phrase,
                answers: typed,
            });
            setDone(data.wrapped ?? []);
            setBegun(null);
            setTyped({});
            await refresh();
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? 'That did not match. Check the words and try again.');
        } finally {
            setBusy(false);
        }
    };

    const rewrap = async () => {
        setBusy(true); setError(null);
        try {
            await api.post('/keyvault/rewrap');
            await refresh();
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? 'Could not protect the remaining keys.');
        } finally {
            setBusy(false);
        }
    };

    const runCheck = async () => {
        setBusy(true); setCheckResult(null); setError(null);
        try {
            const { data } = await api.post('/keyvault/recovery/check', { phrase: checkPhrase });
            setCheckResult(data.matches);
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? 'Could not check that phrase.');
        } finally {
            setBusy(false);
        }
    };

    const allTypedIn = begun?.verify_indices.every((i) => (typed[i] ?? '').trim().length > 0) ?? false;

    return (
        <div className="space-y-6">
            <header>
                <h2 className="text-lg font-semibold text-gray-100">Recovery Phrase</h2>
                <p className="mt-1 text-sm text-gray-400">
                    LocalBook encrypts your saved logins, email accounts and browser sessions with a key
                    held in this Mac's Keychain. A recovery phrase is the only way back in if that
                    Keychain is ever wiped or this Mac is replaced.
                </p>
            </header>

            {error && (
                <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-200">
                    {error}
                </div>
            )}

            {/* The banner reports PROTECTED, not merely configured. */}
            {status && (
                <div
                    className={`rounded-lg border px-4 py-3 text-sm ${
                        status.fully_protected
                            ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
                            : 'border-amber-500/40 bg-amber-500/10 text-amber-100'
                    }`}
                >
                    {status.fully_protected ? (
                        <>
                            <strong>Protected.</strong> Every key on this Mac has a copy that your
                            recovery phrase can unlock.
                        </>
                    ) : status.recovery_key_configured ? (
                        <>
                            <strong>Partly protected.</strong> A recovery phrase is set up, but{' '}
                            {status.unprotected_purposes.length} key
                            {status.unprotected_purposes.length === 1 ? '' : 's'} created since then
                            {status.unprotected_purposes.length === 1 ? ' has' : ' have'} no copy yet.
                            <button
                                onClick={rewrap}
                                disabled={busy}
                                className="ml-2 underline underline-offset-2 hover:text-white disabled:opacity-50"
                            >
                                Protect them now
                            </button>
                        </>
                    ) : (
                        <>
                            <strong>Not protected.</strong> If this Mac's Keychain is wiped or the Mac is
                            replaced, your saved logins and email accounts cannot be recovered. Set up a
                            recovery phrase below.
                        </>
                    )}
                </div>
            )}

            {done && (
                <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-4 py-3 text-sm text-emerald-200">
                    <strong>Recovery phrase saved.</strong> {done.length} key
                    {done.length === 1 ? '' : 's'} protected. Keep the phrase somewhere safe and offline —
                    it is not stored on this Mac and cannot be shown again.
                </div>
            )}

            {/* ── setup ── */}
            {!begun && (
                <button
                    onClick={begin}
                    disabled={busy}
                    className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-50"
                >
                    {status?.recovery_key_configured ? 'Replace recovery phrase' : 'Create recovery phrase'}
                </button>
            )}

            {begun && (
                <div className="space-y-5 rounded-lg border border-gray-700 bg-gray-900/60 p-5">
                    <div>
                        <h3 className="text-sm font-semibold text-gray-100">
                            Write these 24 words down, in order
                        </h3>
                        <p className="mt-1 text-xs text-gray-400">
                            This is shown once and is never stored on this Mac. Anyone with these words can
                            decrypt a copy of your saved logins, so keep them offline — paper or a password
                            manager, not a screenshot.
                        </p>
                    </div>

                    <ol className="grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-4">
                        {begun.words.map((word, i) => (
                            <li key={i} className="flex gap-2 font-mono text-sm text-gray-200">
                                <span className="w-6 shrink-0 text-right text-gray-500">{i + 1}</span>
                                <span>{word}</span>
                            </li>
                        ))}
                    </ol>

                    <div className="flex flex-wrap gap-2">
                        <button
                            onClick={() => void navigator.clipboard?.writeText(begun.phrase)}
                            className="rounded border border-gray-600 px-3 py-1.5 text-xs text-gray-200 hover:bg-gray-800"
                        >
                            Copy to password manager
                        </button>
                        <button
                            onClick={() => window.print()}
                            className="rounded border border-gray-600 px-3 py-1.5 text-xs text-gray-200 hover:bg-gray-800"
                        >
                            Print
                        </button>
                    </div>

                    <label className="flex items-start gap-2 text-xs text-gray-300">
                        <input
                            type="checkbox"
                            checked={acknowledged}
                            onChange={(e) => setAcknowledged(e.target.checked)}
                            className="mt-0.5"
                        />
                        <span>
                            I have written these words down somewhere I will still have if this Mac is lost.
                        </span>
                    </label>

                    <div className="border-t border-gray-700 pt-4">
                        <h3 className="text-sm font-semibold text-gray-100">
                            Now type these {begun.verify_indices.length} back
                        </h3>
                        <p className="mt-1 text-xs text-gray-400">
                            So we know the phrase really left this screen.
                        </p>
                        <div className="mt-3 grid grid-cols-2 gap-3 sm:grid-cols-4">
                            {begun.verify_indices.map((i) => (
                                <label key={i} className="flex flex-col gap-1">
                                    <span className="text-xs text-gray-500">Word {i}</span>
                                    <input
                                        type="text"
                                        autoComplete="off"
                                        spellCheck={false}
                                        value={typed[i] ?? ''}
                                        onChange={(e) => setTyped({ ...typed, [i]: e.target.value })}
                                        className="rounded border border-gray-600 bg-gray-800 px-2 py-1 font-mono text-sm text-gray-100"
                                    />
                                </label>
                            ))}
                        </div>
                    </div>

                    <div className="flex gap-2">
                        <button
                            onClick={confirm}
                            disabled={busy || !acknowledged || !allTypedIn}
                            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-40"
                        >
                            Confirm and protect my keys
                        </button>
                        <button
                            onClick={() => { setBegun(null); setTyped({}); }}
                            disabled={busy}
                            className="rounded-lg border border-gray-600 px-4 py-2 text-sm text-gray-300 hover:bg-gray-800"
                        >
                            Cancel
                        </button>
                    </div>
                </div>
            )}

            {/* ── what is covered ── */}
            {status && (
                <div className="rounded-lg border border-gray-700 bg-gray-900/40 p-4">
                    <h3 className="text-sm font-semibold text-gray-200">What this covers</h3>
                    <ul className="mt-2 space-y-1.5 text-sm">
                        {Object.entries(status.purposes).map(([purpose, state]) => (
                            <li key={purpose} className="flex items-start gap-2">
                                <span className="mt-0.5">
                                    {!state.in_keychain ? '·' : state.wrapped ? '✅' : '⚠️'}
                                </span>
                                <span className={state.in_keychain ? 'text-gray-300' : 'text-gray-500'}>
                                    {PURPOSE_LABELS[purpose] ?? purpose}
                                    {!state.in_keychain && ' — not in use yet'}
                                    {state.in_keychain && !state.wrapped && ' — no recovery copy'}
                                </span>
                            </li>
                        ))}
                    </ul>
                    <p className="mt-3 text-xs text-gray-500">This Mac: {status.device_id}</p>
                </div>
            )}

            {/* ── periodic confirmation ── */}
            {status?.recovery_key_configured && (
                <div className="rounded-lg border border-gray-700 bg-gray-900/40 p-4">
                    <h3 className="text-sm font-semibold text-gray-200">Check you still have it</h3>
                    <p className="mt-1 text-xs text-gray-400">
                        Type your phrase to confirm it still matches this Mac. Nothing is changed, and the
                        phrase is not stored.
                    </p>
                    <div className="mt-3 flex gap-2">
                        <input
                            type="password"
                            autoComplete="off"
                            value={checkPhrase}
                            onChange={(e) => { setCheckPhrase(e.target.value); setCheckResult(null); }}
                            placeholder="your 24 words"
                            className="flex-1 rounded border border-gray-600 bg-gray-800 px-3 py-1.5 text-sm text-gray-100"
                        />
                        <button
                            onClick={runCheck}
                            disabled={busy || !checkPhrase.trim()}
                            className="rounded border border-gray-600 px-3 py-1.5 text-sm text-gray-200 hover:bg-gray-800 disabled:opacity-40"
                        >
                            Check
                        </button>
                    </div>
                    {checkResult !== null && (
                        <p className={`mt-2 text-sm ${checkResult ? 'text-emerald-300' : 'text-red-300'}`}>
                            {checkResult
                                ? 'That is the right phrase.'
                                : 'That phrase does not match this Mac.'}
                        </p>
                    )}
                </div>
            )}
        </div>
    );
}
