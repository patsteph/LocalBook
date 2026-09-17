/**
 * Settings → Companions.
 *
 * One card per tool, one state word, one primary action. The card answers three
 * questions and stops: what is this, is it working, and what would I do next.
 *
 * Everything else lives behind ⋯. That restraint is deliberate — a companion
 * has a lot of state (installed, connected, which model, which notebook, how
 * many files) and showing all of it turns a status card into a control panel.
 *
 * Install is honest about not being one-click: the tool needs Homebrew, an
 * audio driver, and an admin password. Pretending otherwise with a spinner that
 * cannot finish would be worse than handing over a command to paste.
 */
import { useCallback, useEffect, useState } from 'react';
import {
  STATE_DOT,
  STATE_LABEL,
  connectCompanion,
  controlCompanion,
  disconnectCompanion,
  listCompanions,
  revokeCompanionKey,
  shortModel,
  type Companion,
} from '../../services/companions';

function InstallPanel({ c, onClose }: { c: Companion; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const cmd = c.install?.command || '';

  return (
    <div className="mt-3 rounded-lg border dark:border-gray-700 bg-gray-50 dark:bg-gray-800/60 p-3 space-y-2.5">
      <p className="text-xs text-gray-600 dark:text-gray-300">
        {c.name} installs itself from the terminal — it needs Homebrew and your
        admin password for the audio driver, so it can't run inside this window.
        Paste this, then come back and press Connect.
      </p>

      <div className="flex items-center gap-2">
        <code className="flex-1 px-2 py-1.5 text-[11px] rounded bg-white dark:bg-gray-900 border dark:border-gray-700 text-gray-800 dark:text-gray-200 overflow-x-auto whitespace-nowrap">
          {cmd}
        </code>
        <button
          onClick={() => {
            void navigator.clipboard.writeText(cmd);
            setCopied(true);
            setTimeout(() => setCopied(false), 1600);
          }}
          className="px-2.5 py-1.5 text-xs rounded bg-gray-900 dark:bg-gray-100 text-white dark:text-gray-900 hover:opacity-90"
        >
          {copied ? 'Copied' : 'Copy'}
        </button>
      </div>

      {(c.install?.notes || []).length > 0 && (
        <ul className="space-y-1">
          {c.install!.notes!.map((n) => (
            <li key={n} className="text-[11px] text-gray-500 dark:text-gray-400 flex gap-1.5">
              <span className="text-gray-400">•</span>{n}
            </li>
          ))}
        </ul>
      )}

      <div className="flex items-center gap-3 pt-0.5">
        {c.homepage && (
          <a href={c.homepage} target="_blank" rel="noreferrer"
             className="text-[11px] text-blue-600 dark:text-blue-400 hover:underline">
            Read the source first
          </a>
        )}
        <button onClick={onClose}
                className="ml-auto text-[11px] text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
          Close
        </button>
      </div>
    </div>
  );
}

function ConnectPanel({ c, notebooks, onDone, onCancel }: {
  c: Companion;
  notebooks: Array<{ id: string; title: string }>;
  onDone: () => void;
  onCancel: () => void;
}) {
  const [notebookId, setNotebookId] = useState(c.linked_notebook_id || '');
  const [backfill, setBackfill] = useState<'all' | 'new_only'>('new_only');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const go = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await connectCompanion(c.id, {
        notebook_id: notebookId || null,
        backfill,
      });
      if (res.link_error) setError(`Connected, but the folder wasn't linked: ${res.link_error}`);
      onDone();
    } catch (e: any) {
      setError(e?.message || 'Could not connect.');
      setBusy(false);
    }
  };

  return (
    <div className="mt-3 rounded-lg border dark:border-gray-700 bg-gray-50 dark:bg-gray-800/60 p-3 space-y-3">
      <p className="text-xs text-gray-600 dark:text-gray-300">
        LocalBook will point {c.name} at its own engine, so it stops loading a
        second copy of the model — and file what it writes into a notebook.
      </p>

      <div>
        <label className="block text-[11px] font-medium text-gray-500 dark:text-gray-400 mb-1">
          Notes go to
        </label>
        <select
          value={notebookId}
          onChange={(e) => setNotebookId(e.target.value)}
          className="w-full px-2 py-1.5 text-xs rounded border dark:border-gray-700 bg-white dark:bg-gray-900 text-gray-900 dark:text-gray-100"
        >
          <option value="">Don't file them anywhere yet</option>
          {notebooks.map((n) => <option key={n.id} value={n.id}>{n.title}</option>)}
        </select>
      </div>

      {notebookId && c.output_exists && (
        <label className="flex items-start gap-2 text-[11px] text-gray-600 dark:text-gray-400">
          <input type="checkbox" className="mt-0.5" checked={backfill === 'all'}
                 onChange={(e) => setBackfill(e.target.checked ? 'all' : 'new_only')} />
          Also add the notes already in that folder
        </label>
      )}

      {error && <p className="text-[11px] text-red-600 dark:text-red-400">{error}</p>}

      <div className="flex justify-end gap-2">
        <button onClick={onCancel} disabled={busy}
                className="px-2.5 py-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
          Cancel
        </button>
        <button onClick={go} disabled={busy}
                className="px-3 py-1 text-xs font-medium rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40">
          {busy ? 'Connecting…' : 'Connect'}
        </button>
      </div>
    </div>
  );
}

function Card({ c, notebooks, onChanged }: {
  c: Companion;
  notebooks: Array<{ id: string; title: string }>;
  onChanged: () => void;
}) {
  const [panel, setPanel] = useState<'none' | 'install' | 'connect'>('none');
  const [menu, setMenu] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true); setError(null); setMenu(false);
    try { await fn(); onChanged(); }
    catch (e: any) { setError(e?.message || 'Failed'); }
    finally { setBusy(false); }
  };

  return (
    <div className="rounded-xl border border-gray-200 dark:border-gray-700 p-4">
      <div className="flex items-start gap-3">
        <span className="text-xl leading-none mt-0.5">{c.icon}</span>

        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            <h3 className="text-sm font-semibold text-gray-900 dark:text-gray-100">{c.name}</h3>
            {c.author && (
              <span className="text-[11px] text-gray-400 dark:text-gray-500">by {c.author}</span>
            )}
          </div>
          <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">{c.tagline}</p>

          {/* State — one line, always true, never remembered. */}
          <div className="mt-2.5 flex items-center gap-1.5 text-xs">
            <span className={`w-1.5 h-1.5 rounded-full ${STATE_DOT[c.state]}`} />
            <span className="font-medium text-gray-700 dark:text-gray-300">
              {STATE_LABEL[c.state]}
            </span>
            {c.connected && c.using_model && (
              <span className="text-gray-400 dark:text-gray-500">
                · using your {shortModel(c.using_model)}
              </span>
            )}
          </div>

          {c.linked_notebook_title && (
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              Notes → <span className="text-gray-700 dark:text-gray-300">{c.linked_notebook_title}</span>
            </p>
          )}

          {error && <p className="mt-1.5 text-xs text-red-600 dark:text-red-400">{error}</p>}

          {panel === 'install' && <InstallPanel c={c} onClose={() => setPanel('none')} />}
          {panel === 'connect' && (
            <ConnectPanel c={c} notebooks={notebooks}
                          onDone={() => { setPanel('none'); onChanged(); }}
                          onCancel={() => setPanel('none')} />
          )}
        </div>

        {/* One primary action for the state you're in. Everything else in ⋯ */}
        <div className="flex items-center gap-1 flex-shrink-0">
          {c.state === 'not_installed' && panel === 'none' && (
            <button onClick={() => setPanel('install')}
                    className="px-3 py-1.5 text-xs font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700">
              Install…
            </button>
          )}
          {c.state === 'installed' && panel === 'none' && (
            <button onClick={() => setPanel('connect')}
                    className="px-3 py-1.5 text-xs font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700">
              Connect
            </button>
          )}
          {c.state === 'recording' && c.can_control && (
            <button disabled={busy}
                    onClick={() => act(() => controlCompanion(c.id, 'stop'))}
                    className="px-3 py-1.5 text-xs font-medium rounded-lg bg-red-600 text-white hover:bg-red-700 disabled:opacity-40">
              Stop
            </button>
          )}

          {c.installed && (
            <div className="relative">
              <button onClick={() => setMenu(!menu)}
                      className="px-2 py-1.5 text-xs rounded-lg text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800">
                ⋯
              </button>
              {menu && (
                <div className="absolute right-0 mt-1 w-56 z-10 rounded-lg border dark:border-gray-700 bg-white dark:bg-gray-900 shadow-lg py-1">
                  {c.connected && (
                    <button onClick={() => setPanel('connect')}
                            className="w-full text-left px-3 py-1.5 text-xs text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
                      Change where notes go
                    </button>
                  )}
                  {c.connected && (
                    <button onClick={() => act(() => disconnectCompanion(c.id))}
                            className="w-full text-left px-3 py-1.5 text-xs text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
                      Disconnect (restore its own settings)
                    </button>
                  )}
                  {c.homepage && (
                    <a href={c.homepage} target="_blank" rel="noreferrer"
                       className="block px-3 py-1.5 text-xs text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
                      View the source
                    </a>
                  )}
                  <button
                    onClick={() => act(async () => { await revokeCompanionKey(); })}
                    className="w-full text-left px-3 py-1.5 text-xs text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20"
                    title="Every connected tool loses access until reconnected"
                  >
                    Revoke access key
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function CompanionsSection() {
  const [data, setData] = useState<{ companions: Companion[]; notebooks: any[] } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await listCompanions());
      setError(null);
    } catch (e: any) {
      setError(e?.message || 'Could not load companions.');
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  // A recording starts and stops outside this window, so the card polls while
  // it is open rather than showing a state that quietly goes stale.
  useEffect(() => {
    const anyControllable = data?.companions.some((c) => c.can_control && c.installed);
    if (!anyControllable) return;
    const t = setInterval(() => void load(), 5000);
    return () => clearInterval(t);
  }, [data, load]);

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Companions</h2>
        <p className="mt-0.5 text-sm text-gray-500 dark:text-gray-400">
          Local tools that work alongside LocalBook. Connecting one lets it use the
          model you already have loaded, instead of running its own.
        </p>
      </div>

      {error && (
        <div className="rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 px-3 py-2 text-sm text-red-700 dark:text-red-300">
          {error}
        </div>
      )}

      {(data?.companions || []).map((c) => (
        <Card key={c.id} c={c} notebooks={data!.notebooks} onChanged={load} />
      ))}

      {data && data.companions.length === 0 && (
        <p className="text-sm text-gray-500 dark:text-gray-400">No companions available.</p>
      )}
    </div>
  );
}
