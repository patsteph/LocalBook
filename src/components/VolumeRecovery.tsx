import { useCallback, useEffect, useState } from 'react';
import { api, clearLocked, type LockedDetail } from '../services/api';

/**
 * The recovery screen (LB-11).
 *
 * Shown instead of the app when the encrypted volume is not open. The whole
 * design brief is one sentence: **this must never read as data loss.**
 *
 * The natural interpretation of "LocalBook cannot open your notebooks" is
 * "LocalBook has lost my notebooks", and a user who believes that does
 * destructive things — reinstalls, deletes the app folder, starts over. What
 * has actually happened is that an encrypted image did not mount. The data is
 * intact and a click away. So the headline says so first, before anything
 * technical, and every path out of here is offered before any explanation of
 * what went wrong.
 *
 * The other rule: no action here may write anything. Unlock and recover both
 * only mount. Nothing on this screen can make the situation worse.
 */

type VolumeInfo = {
    gate: { state: string; locked: boolean; reason?: string | null; detail?: string | null };
    encryption_enabled: boolean;
    has_key?: boolean;
    volume?: {
        image_path?: string;
        mount_point?: string;
        exists?: boolean;
        mounted?: boolean;
        encrypted?: boolean | null;
    };
};

export function VolumeRecovery({ locked }: { locked: LockedDetail }) {
    const [info, setInfo] = useState<VolumeInfo | null>(null);
    const [phrase, setPhrase] = useState('');
    const [showPhrase, setShowPhrase] = useState(false);
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [done, setDone] = useState(false);

    const refresh = useCallback(async () => {
        try {
            const { data } = await api.get<VolumeInfo>('/system/volume');
            setInfo(data);
        } catch {
            /* the status route is itself allowlisted; if it fails, show the
               generic copy rather than nothing */
        }
    }, []);

    useEffect(() => { void refresh(); }, [refresh]);

    const unlock = async () => {
        setBusy('unlock'); setError(null);
        try {
            const { data } = await api.post('/system/volume/unlock');
            if (data.unlocked) {
                setDone(true);
                clearLocked();
            } else {
                setError(data.detail || 'The volume did not unlock.');
                await refresh();
            }
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? 'Could not unlock the volume.');
        } finally {
            setBusy(null);
        }
    };

    const recover = async () => {
        setBusy('recover'); setError(null);
        try {
            const { data } = await api.post('/system/volume/recover', { phrase });
            if (data.unlocked) {
                setDone(true);
                clearLocked();
            } else {
                setError('The phrase was accepted but the volume still did not open.');
            }
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? 'That phrase did not work.');
        } finally {
            setBusy(null);
            setPhrase('');
        }
    };

    if (done) {
        return (
            <Shell>
                <h1 className="text-2xl font-semibold text-emerald-300">Your notebooks are back.</h1>
                <p className="mt-3 text-gray-300">
                    The volume is open. Quit and reopen LocalBook to finish — nothing was
                    loaded while it was locked, so a restart is what brings everything up.
                </p>
            </Shell>
        );
    }

    const volumeMissing = info?.volume?.exists === false;
    const keyMissing = info?.has_key === false;

    return (
        <Shell>
            {/* The reassurance comes FIRST, before any diagnosis. */}
            <h1 className="text-2xl font-semibold text-gray-100">
                Your notebooks are safe — LocalBook just can&rsquo;t open them yet.
            </h1>
            <p className="mt-3 text-gray-300">
                {locked.detail
                    || info?.gate?.detail
                    || 'Your data is encrypted on this Mac and the volume holding it is not open. Nothing has been lost or changed.'}
            </p>

            {error && (
                <div className="mt-5 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-200">
                    {error}
                </div>
            )}

            {/* ── the ways back in, before any explanation of what went wrong ── */}
            <div className="mt-7 space-y-4">
                {!volumeMissing && !keyMissing && (
                    <Action
                        title="Unlock it"
                        body="This Mac still has the key. Most of the time this is all it takes."
                        button={busy === 'unlock' ? 'Unlocking…' : 'Unlock'}
                        disabled={!!busy}
                        onClick={unlock}
                    />
                )}

                {!volumeMissing && (
                    <div className="rounded-lg border border-gray-700 bg-gray-900/50 p-5">
                        <h2 className="text-sm font-semibold text-gray-100">
                            Use your recovery phrase
                        </h2>
                        <p className="mt-1 text-sm text-gray-400">
                            {keyMissing
                                ? 'This Mac no longer has the key — its Keychain was reset, or this is a different Mac. Your 24 words will put it back.'
                                : 'If unlocking does not work, your 24 words will restore the key.'}
                        </p>
                        <div className="mt-3 flex gap-2">
                            <input
                                type={showPhrase ? 'text' : 'password'}
                                value={phrase}
                                onChange={(e) => setPhrase(e.target.value)}
                                placeholder="your 24 words"
                                autoComplete="off"
                                spellCheck={false}
                                className="min-w-0 flex-1 rounded border border-gray-600 bg-gray-800 px-3 py-2 text-sm text-gray-100"
                            />
                            <button
                                onClick={() => setShowPhrase((s) => !s)}
                                className="rounded border border-gray-600 px-3 text-xs text-gray-300 hover:bg-gray-800"
                            >
                                {showPhrase ? 'Hide' : 'Show'}
                            </button>
                            <button
                                onClick={recover}
                                disabled={!!busy || phrase.trim().split(/\s+/).length < 24}
                                className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-40"
                            >
                                {busy === 'recover' ? 'Recovering…' : 'Recover'}
                            </button>
                        </div>
                        <p className="mt-2 text-xs text-gray-600">
                            The phrase is used on this Mac only and is never stored or sent anywhere.
                        </p>
                    </div>
                )}

                {volumeMissing && (
                    <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-5">
                        <h2 className="text-sm font-semibold text-amber-100">
                            The encrypted volume is not where LocalBook left it
                        </h2>
                        <p className="mt-1 text-sm text-amber-100/80">
                            It should be at{' '}
                            <code className="font-mono text-xs">{info?.volume?.image_path}</code>.
                            If it was moved, put it back and unlock. If it was deleted, restore
                            from a backup — LocalBook keeps encrypted archives, and your recovery
                            phrase opens them on any Mac.
                        </p>
                    </div>
                )}
            </div>

            {/* The technical detail comes last, and is optional reading. */}
            {info && (
                <details className="mt-8 text-xs text-gray-500">
                    <summary className="cursor-pointer hover:text-gray-300">
                        What LocalBook found
                    </summary>
                    <dl className="mt-3 space-y-1">
                        <Detail label="Reason">{info.gate?.reason ?? locked.reason ?? '—'}</Detail>
                        <Detail label="Volume">{info.volume?.image_path ?? '—'}</Detail>
                        <Detail label="Present">{info.volume?.exists ? 'yes' : 'no'}</Detail>
                        <Detail label="Key on this Mac">{info.has_key ? 'yes' : 'no'}</Detail>
                        <Detail label="Mount point">{info.volume?.mount_point ?? '—'}</Detail>
                    </dl>
                </details>
            )}

            <p className="mt-8 text-xs text-gray-600">
                Nothing on this screen changes or deletes anything. Unlocking only opens the
                volume that is already there.
            </p>
        </Shell>
    );
}

function Shell({ children }: { children: React.ReactNode }) {
    return (
        <div className="flex h-screen items-center justify-center bg-gray-950 p-8">
            <div className="w-full max-w-2xl">
                <div className="mb-6 flex items-center gap-3 text-gray-500">
                    <span className="text-2xl">🔒</span>
                    <span className="text-sm font-medium">LocalBook</span>
                </div>
                {children}
            </div>
        </div>
    );
}

function Action({ title, body, button, disabled, onClick }: {
    title: string; body: string; button: string; disabled: boolean; onClick: () => void;
}) {
    return (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-gray-700 bg-gray-900/50 p-5">
            <div className="min-w-0">
                <h2 className="text-sm font-semibold text-gray-100">{title}</h2>
                <p className="mt-1 text-sm text-gray-400">{body}</p>
            </div>
            <button
                onClick={onClick}
                disabled={disabled}
                className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-40"
            >
                {button}
            </button>
        </div>
    );
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
    return (
        <div className="flex gap-3">
            <dt className="w-32 shrink-0 text-gray-600">{label}</dt>
            <dd className="min-w-0 break-all font-mono text-gray-400">{children}</dd>
        </div>
    );
}
