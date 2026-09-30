import { useCallback, useEffect, useState } from 'react';
import { api } from '../../services/api';

/**
 * Create a recovery phrase: show 24 words, have a few typed back (K-1).
 *
 * `begin` commits nothing — only the type-back does. The type-back IS the proof
 * the phrase left the screen, so there is no separate "I wrote them down"
 * checkbox (dropped in the simplified flow: it asked the same thing twice).
 *
 * Used by the encryption wizard (step ①) and Settings › Recovery.
 */

type BeginPayload = {
    phrase: string;
    words: string[];
    verify_indices: number[];
    already_configured: boolean;
};

type Props = {
    onConfirmed: (wrapped: string[]) => void;
    onCancel?: () => void;
    confirmLabel?: string;
};

export function RecoveryPhraseStep({ onConfirmed, onCancel, confirmLabel = 'Confirm' }: Props) {
    const [begun, setBegun] = useState<BeginPayload | null>(null);
    const [typed, setTyped] = useState<Record<number, string>>({});
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const begin = useCallback(async () => {
        setBusy(true); setError(null);
        try {
            const { data } = await api.post<BeginPayload>('/keyvault/recovery/begin');
            setBegun(data);
            setTyped({});
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? 'Could not generate a recovery phrase.');
        } finally {
            setBusy(false);
        }
    }, []);

    useEffect(() => { void begin(); }, [begin]);

    const confirm = async () => {
        if (!begun) return;
        setBusy(true); setError(null);
        try {
            const { data } = await api.post('/keyvault/recovery/confirm', {
                phrase: begun.phrase,
                answers: typed,
            });
            onConfirmed(data.wrapped ?? []);
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? 'That did not match. Check the words and try again.');
        } finally {
            setBusy(false);
        }
    };

    const allTypedIn = begun?.verify_indices.every((i) => (typed[i] ?? '').trim().length > 0) ?? false;

    return (
        <div className="space-y-5 rounded-lg border border-gray-700 bg-gray-900/60 p-5">
            {error && (
                <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-200">
                    {error}
                    {!begun && (
                        <button onClick={() => void begin()} className="ml-2 underline underline-offset-2">
                            Try again
                        </button>
                    )}
                </div>
            )}

            {!begun && !error && <p className="text-sm text-gray-400">Generating your phrase…</p>}

            {begun && (
                <>
                    <div>
                        <h3 className="text-sm font-semibold text-gray-100">
                            Write these 24 words down, in order
                        </h3>
                        <p className="mt-1 text-xs text-gray-400">
                            They are the only way back in if this Mac's Keychain is lost. Shown once, never stored
                            on this Mac — keep them offline: paper or a password manager, not a screenshot.
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

                    <div className="border-t border-gray-700 pt-4">
                        <h3 className="text-sm font-semibold text-gray-100">
                            Now type these {begun.verify_indices.length} back
                        </h3>
                        <div className="mt-3 grid grid-cols-3 gap-3">
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
                            onClick={() => void confirm()}
                            disabled={busy || !allTypedIn}
                            className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-40"
                        >
                            {confirmLabel}
                        </button>
                        {onCancel && (
                            <button
                                onClick={onCancel}
                                disabled={busy}
                                className="rounded-lg border border-gray-600 px-4 py-2 text-sm text-gray-300 hover:bg-gray-800"
                            >
                                Cancel
                            </button>
                        )}
                    </div>
                </>
            )}
        </div>
    );
}
