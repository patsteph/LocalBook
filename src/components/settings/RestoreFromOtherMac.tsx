import { useState } from 'react';
import { api } from '../../services/api';

/**
 * Recover another Mac's keys on this one (K-1 / LB-11).
 *
 * The replacement-Mac path: the old Mac's disk is gone, but its wrapped keys
 * are held by every paired Mac and inside its backups. With the OLD Mac's
 * phrase they come back here. This Mac's own volume password and sync identity
 * are never replaced — the backend refuses — and its login/backup keys are kept
 * unless the user asks to replace them.
 */

export type KeySet = {
    device_id: string;
    name: string | null;
    purposes: string[];
    sources: string[];
    this_mac: boolean;
};

type Result = { restored: string[]; kept: Record<string, string>; failed: Record<string, string> };

const LABEL: Record<string, string> = {
    credentials: 'site logins and email accounts',
    backup: 'its backups',
    device_identity: 'its sync identity',
    volume: 'its encrypted volume',
};

export function RestoreFromOtherMac({ sets, onDone }: { sets: KeySet[]; onDone: () => void }) {
    const others = sets.filter((s) => !s.this_mac);
    const [device, setDevice] = useState(others[0]?.device_id ?? '');
    const [phrase, setPhrase] = useState('');
    const [replace, setReplace] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [result, setResult] = useState<Result | null>(null);

    if (others.length === 0) return null;

    const run = async () => {
        setBusy(true); setError(null); setResult(null);
        try {
            const { data } = await api.post<Result>('/keyvault/recovery/restore', { phrase, device, replace });
            setResult(data);
            setPhrase('');
            onDone();
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? 'Nothing could be restored.');
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="rounded-lg border border-gray-700 bg-gray-900/40 p-4">
            <h3 className="text-sm font-semibold text-gray-200">Restore keys from another Mac</h3>
            <p className="mt-1 text-xs text-gray-400">
                Replacing a Mac? Its recovery copies are kept on your paired Macs and in its backups. Use
                <b> that Mac's</b> phrase to bring them back here. This Mac's own volume and sync identity are
                never replaced.
            </p>
            <div className="mt-3 space-y-1.5">
                {others.map((s) => (
                    <label key={s.device_id} className="flex items-start gap-2 text-sm text-gray-300">
                        <input type="radio" className="mt-1" checked={device === s.device_id}
                               onChange={() => setDevice(s.device_id)} />
                        <span>
                            {s.name || s.device_id}
                            <span className="block text-xs text-gray-500">
                                {s.purposes.map((p) => LABEL[p] ?? p).join(', ')} · from {s.sources.join(' and ')}
                            </span>
                        </span>
                    </label>
                ))}
            </div>
            <div className="mt-3 flex gap-2">
                <input type="password" autoComplete="off" value={phrase}
                       onChange={(e) => { setPhrase(e.target.value); setResult(null); }}
                       placeholder="that Mac's 24 words"
                       className="flex-1 rounded border border-gray-600 bg-gray-800 px-3 py-1.5 text-sm text-gray-100" />
                <button onClick={run}
                        disabled={busy || !device || phrase.trim().split(/\s+/).length < 24}
                        className="rounded border border-gray-600 px-3 py-1.5 text-sm text-gray-200 hover:bg-gray-800 disabled:opacity-40">
                    {busy ? 'Restoring…' : 'Restore'}
                </button>
            </div>
            <label className="mt-2 flex items-center gap-2 text-xs text-gray-400">
                <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />
                Use its login and backup keys instead of this Mac's (only if this Mac's are new and empty)
            </label>
            {error && <p className="mt-2 text-sm text-red-300">{error}</p>}
            {result && (
                <div className="mt-2 text-sm">
                    {result.restored.length > 0 && (
                        <p className="text-emerald-300">
                            Restored: {result.restored.map((p) => LABEL[p] ?? p).join(', ')}.
                        </p>
                    )}
                    {Object.keys(result.kept).length > 0 && (
                        <p className="text-gray-400">
                            Kept this Mac's own: {Object.keys(result.kept).map((p) => LABEL[p] ?? p).join(', ')}.
                        </p>
                    )}
                    {Object.entries(result.failed).map(([p, why]) => (
                        <p key={p} className="text-amber-300">{LABEL[p] ?? p}: {why}</p>
                    ))}
                </div>
            )}
        </div>
    );
}
