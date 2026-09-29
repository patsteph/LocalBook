import { useCallback, useEffect, useState } from 'react';
import { api } from '../../services/api';

/**
 * Agent keys and the call log (LB-0 + LB-2).
 *
 * Separate from CompanionsSection because that file is already ~900 lines and
 * this is a distinct job: CompanionsSection installs and configures *tools*
 * that have a manifest and a config file on disk. An agent like Jocasta has
 * neither — nothing to install, nothing to rewrite — so the only way it can
 * ever hold a key is to be handed one here.
 *
 * Two things the UI has to be honest about:
 *   - A key is shown ONCE. It is stored as a salted hash and cannot be read
 *     back, so the screen says so rather than implying it can be found later.
 *   - The call log stores argument NAMES and a hash, never values. A tool call
 *     carries the user's own questions, and a plaintext log of those would be a
 *     worse leak than the thing the keys protect.
 */

type KeyRow = {
    companion_id: string;
    scopes: string[];
    created_at: string | null;
    last_used_at: string | null;
};

type CallRow = {
    id: number;
    companion_id: string;
    tool: string;
    args_preview: string | null;
    ts: number;
    ms: number;
    outcome: string;
    detail: string | null;
};

const ALL_SCOPES = ['llm', 'mcp', 'audio', 'memory', 'events'] as const;

const SCOPE_HELP: Record<string, string> = {
    llm: 'Use the local models',
    mcp: 'Search and read your notebooks',
    audio: 'Speech to text and text to speech',
    memory: 'Read and add to memory',
    events: 'See what LocalBook has been doing',
};

const OUTCOME_STYLE: Record<string, string> = {
    ok: 'text-emerald-300',
    busy: 'text-amber-300',
    denied: 'text-red-300',
    error: 'text-red-300',
};

function when(value: string | number | null): string {
    if (!value) return 'never';
    const d = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toLocaleString();
}

export function AgentKeysPanel() {
    const [keys, setKeys] = useState<KeyRow[]>([]);
    const [calls, setCalls] = useState<CallRow[]>([]);
    const [newId, setNewId] = useState('jocasta');
    const [scopes, setScopes] = useState<string[]>(['mcp', 'events']);
    const [issued, setIssued] = useState<{ companion_id: string; key: string } | null>(null);
    const [copied, setCopied] = useState(false);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [confirmRevoke, setConfirmRevoke] = useState<string | null>(null);

    const refresh = useCallback(async () => {
        try {
            const [k, c] = await Promise.all([
                api.get<{ keys: KeyRow[] }>('/companions/keys'),
                api.get<{ calls: CallRow[] }>('/companions/calls', { params: { limit: 50 } }),
            ]);
            setKeys(k.data.keys ?? []);
            setCalls(c.data.calls ?? []);
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? 'Could not load companion keys.');
        }
    }, []);

    useEffect(() => { void refresh(); }, [refresh]);

    const issue = async () => {
        const id = newId.trim();
        if (!id) return;
        setBusy(true); setError(null); setCopied(false);
        try {
            const { data } = await api.post('/companions/keys/issue', {
                companion_id: id,
                scopes,
            });
            setIssued({ companion_id: data.companion_id, key: data.key });
            await refresh();
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? 'Could not issue a key.');
        } finally {
            setBusy(false);
        }
    };

    const revoke = async (companionId: string) => {
        setBusy(true); setError(null);
        try {
            await api.post(`/companions/${encodeURIComponent(companionId)}/key/revoke`);
            setConfirmRevoke(null);
            await refresh();
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? 'Could not revoke that key.');
        } finally {
            setBusy(false);
        }
    };

    const purge = async (companionId?: string) => {
        setBusy(true); setError(null);
        try {
            await api.delete('/companions/calls', {
                params: companionId ? { companion_id: companionId } : undefined,
            });
            await refresh();
        } catch (e: any) {
            setError(e?.response?.data?.detail ?? 'Could not clear the log.');
        } finally {
            setBusy(false);
        }
    };

    const existing = keys.find((k) => k.companion_id === newId.trim());

    return (
        <div className="space-y-6">
            <header>
                <h3 className="text-base font-semibold text-gray-100">Agent keys</h3>
                <p className="mt-1 text-sm text-gray-400">
                    Keys for agents that talk to LocalBook directly rather than being installed as a
                    tool. Each one is separate: revoking an agent leaves every other companion
                    working, and a key only reaches what its scopes allow.
                </p>
            </header>

            {error && (
                <div className="rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-3 text-sm text-red-200">
                    {error}
                </div>
            )}

            {/* ── the key, shown once ── */}
            {issued && (
                <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/10 p-4">
                    <p className="text-sm font-semibold text-emerald-200">
                        Key for {issued.companion_id}
                    </p>
                    <p className="mt-1 text-xs text-emerald-100/80">
                        Copy it now. LocalBook stores only a hash, so this cannot be shown again —
                        issuing another one replaces this.
                    </p>
                    <div className="mt-3 flex gap-2">
                        <code className="flex-1 overflow-x-auto rounded bg-gray-900 px-3 py-2 font-mono text-xs text-gray-100">
                            {issued.key}
                        </code>
                        <button
                            onClick={() => {
                                void navigator.clipboard?.writeText(issued.key);
                                setCopied(true);
                            }}
                            className="rounded border border-emerald-500/50 px-3 py-1.5 text-xs text-emerald-100 hover:bg-emerald-500/20"
                        >
                            {copied ? 'Copied' : 'Copy'}
                        </button>
                        <button
                            onClick={() => { setIssued(null); setCopied(false); }}
                            className="rounded border border-gray-600 px-3 py-1.5 text-xs text-gray-300 hover:bg-gray-800"
                        >
                            Done
                        </button>
                    </div>
                </div>
            )}

            {/* ── issue ── */}
            <div className="rounded-lg border border-gray-700 bg-gray-900/40 p-4">
                <h4 className="text-sm font-semibold text-gray-200">Issue a key</h4>
                <div className="mt-3 flex flex-wrap items-end gap-3">
                    <label className="flex flex-col gap-1">
                        <span className="text-xs text-gray-500">Agent name</span>
                        <input
                            type="text"
                            value={newId}
                            onChange={(e) => setNewId(e.target.value)}
                            spellCheck={false}
                            className="w-48 rounded border border-gray-600 bg-gray-800 px-2 py-1.5 text-sm text-gray-100"
                        />
                    </label>
                    <button
                        onClick={issue}
                        disabled={busy || !newId.trim() || scopes.length === 0}
                        className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-500 disabled:opacity-40"
                    >
                        {existing ? 'Replace key' : 'Issue key'}
                    </button>
                </div>

                <div className="mt-4 space-y-2">
                    <span className="text-xs text-gray-500">What it may do</span>
                    {ALL_SCOPES.map((scope) => (
                        <label key={scope} className="flex items-start gap-2 text-sm">
                            <input
                                type="checkbox"
                                checked={scopes.includes(scope)}
                                onChange={(e) =>
                                    setScopes(
                                        e.target.checked
                                            ? [...scopes, scope]
                                            : scopes.filter((s) => s !== scope),
                                    )
                                }
                                className="mt-1"
                            />
                            <span>
                                <span className="font-mono text-xs text-gray-300">{scope}</span>
                                <span className="ml-2 text-gray-400">{SCOPE_HELP[scope]}</span>
                            </span>
                        </label>
                    ))}
                </div>

                {existing && (
                    <p className="mt-3 text-xs text-amber-300">
                        {existing.companion_id} already has a key. Issuing replaces it, and whatever
                        holds the old one stops working immediately.
                    </p>
                )}
            </div>

            {/* ── who holds a key ── */}
            <div className="rounded-lg border border-gray-700 bg-gray-900/40 p-4">
                <h4 className="text-sm font-semibold text-gray-200">Who holds a key</h4>
                {keys.length === 0 ? (
                    <p className="mt-2 text-sm text-gray-500">Nothing is connected yet.</p>
                ) : (
                    <ul className="mt-3 space-y-2">
                        {keys.map((k) => (
                            <li
                                key={k.companion_id}
                                className="flex flex-wrap items-center justify-between gap-2 rounded border border-gray-700/60 bg-gray-800/40 px-3 py-2"
                            >
                                <div className="min-w-0">
                                    <div className="text-sm text-gray-100">{k.companion_id}</div>
                                    <div className="text-xs text-gray-500">
                                        {k.scopes.join(' · ') || 'no scopes'} — last used{' '}
                                        {when(k.last_used_at)}
                                    </div>
                                </div>
                                {confirmRevoke === k.companion_id ? (
                                    <span className="flex gap-2">
                                        <button
                                            onClick={() => revoke(k.companion_id)}
                                            disabled={busy}
                                            className="rounded bg-red-600 px-2.5 py-1 text-xs text-white hover:bg-red-500"
                                        >
                                            Revoke it
                                        </button>
                                        <button
                                            onClick={() => setConfirmRevoke(null)}
                                            className="rounded border border-gray-600 px-2.5 py-1 text-xs text-gray-300"
                                        >
                                            Cancel
                                        </button>
                                    </span>
                                ) : (
                                    <button
                                        onClick={() => setConfirmRevoke(k.companion_id)}
                                        className="rounded border border-gray-600 px-2.5 py-1 text-xs text-gray-300 hover:bg-gray-800"
                                    >
                                        Revoke
                                    </button>
                                )}
                            </li>
                        ))}
                    </ul>
                )}
            </div>

            {/* ── what they did ── */}
            <div className="rounded-lg border border-gray-700 bg-gray-900/40 p-4">
                <div className="flex items-center justify-between">
                    <h4 className="text-sm font-semibold text-gray-200">What they have done</h4>
                    {calls.length > 0 && (
                        <button
                            onClick={() => purge()}
                            disabled={busy}
                            className="text-xs text-gray-400 underline underline-offset-2 hover:text-gray-200"
                        >
                            Clear log
                        </button>
                    )}
                </div>
                <p className="mt-1 text-xs text-gray-500">
                    Argument names only — never their values. A call can carry your own questions,
                    so only a hash of them is kept.
                </p>
                {calls.length === 0 ? (
                    <p className="mt-3 text-sm text-gray-500">Nothing yet.</p>
                ) : (
                    <div className="mt-3 max-h-80 overflow-y-auto">
                        <table className="w-full text-left text-xs">
                            <thead className="text-gray-500">
                                <tr>
                                    <th className="pb-1 pr-3 font-normal">When</th>
                                    <th className="pb-1 pr-3 font-normal">Agent</th>
                                    <th className="pb-1 pr-3 font-normal">Tool</th>
                                    <th className="pb-1 pr-3 font-normal">Arguments</th>
                                    <th className="pb-1 pr-3 font-normal">Took</th>
                                    <th className="pb-1 font-normal">Result</th>
                                </tr>
                            </thead>
                            <tbody className="text-gray-300">
                                {calls.map((c) => (
                                    <tr key={c.id} className="border-t border-gray-800">
                                        <td className="py-1 pr-3 text-gray-500">{when(c.ts)}</td>
                                        <td className="py-1 pr-3">{c.companion_id}</td>
                                        <td className="py-1 pr-3 font-mono">{c.tool}</td>
                                        <td className="py-1 pr-3 text-gray-500">
                                            {c.args_preview || '—'}
                                        </td>
                                        <td className="py-1 pr-3 text-gray-500">{c.ms}ms</td>
                                        <td
                                            className={`py-1 ${OUTCOME_STYLE[c.outcome] ?? 'text-gray-400'}`}
                                            title={c.detail ?? undefined}
                                        >
                                            {c.outcome}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>
        </div>
    );
}
