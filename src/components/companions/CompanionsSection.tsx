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
  acceptCompanionUpdate,
  checkCompanionUpdates,
  checkInstallScript,
  installExtra,
  runPreflight,
  removeExtra,
  verifyCompanion,
  STATE_LABEL,
  connectCompanion,
  controlCompanion,
  disconnectCompanion,
  listCompanions,
  revokeCompanionKey,
  shortModel,
  type Companion,
  type VerifyResult,
} from '../../services/companions';

/**
 * Step one of installing: prepare the Mac, asking for the password once.
 *
 * Only three things in the whole install actually need root, and only one of
 * them is a package. Doing them here — together — means the companion's own
 * installer has nothing left to ask for, without our having changed a byte of
 * their repository.
 *
 * The list is shown before the prompt, with a reason beside each item. Granting
 * admin to a list of package names is not consent.
 */
function PreflightPanel({ c, onDone, onClose, onRefresh }: {
  c: Companion; onDone: () => void; onClose: () => void; onRefresh: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [log, setLog] = useState<string[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [details, setDetails] = useState<{
    status?: number; tried?: Array<{ name: string; uid?: string }>;
  } | null>(null);
  const plan = c.preflight;
  if (!plan) return null;

  const outstanding = plan.steps.filter((s) => !s.done && !s.incidental);

  const go = async () => {
    setBusy(true); setError(null);
    try {
      const res = await runPreflight(c.id);
      setLog(res.log || []);
      setWarnings(res.warnings || []);
      setDetails(res.details || null);
      onRefresh();
      // The endpoint now RETURNS failures rather than throwing, so the evidence
      // survives — which means the error lives here, not only in catch().
      if (res.ok === false) {
        setError(res.error || 'Preparation did not finish.');
        return;
      }
      // A skipped optional step is not a failure, but it must not be silent:
      // stay on the panel so the user reads it rather than being moved along.
      if (!(res.warnings || []).length) onDone();
    } catch (e: any) {
      setError(e?.message || 'Preparation failed.');
      // Refresh even on failure: a partial run changes what is outstanding, and
      // a stale checklist showing everything undone hides the progress made.
      onRefresh();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-3 rounded-lg border dark:border-gray-700 bg-gray-50 dark:bg-gray-800/60 p-3 space-y-2.5">
      <p className="text-xs text-gray-600 dark:text-gray-300">{plan.summary}</p>
      {plan.after_install && (
        <p className="text-[11px] text-gray-500 dark:text-gray-400">
          This is the step {c.name}'s own installer asks you to do by hand.
        </p>
      )}

      <div className="space-y-1.5">
        {plan.steps.filter((s) => !s.incidental).map((s) => (
          <div key={s.id} className="flex items-start gap-1.5 text-[11px]">
            <span className={s.done ? 'text-green-600 dark:text-green-400' : 'text-gray-400'}>
              {s.done ? '✓' : '○'}
            </span>
            <span className={s.done
              ? 'text-gray-400 dark:text-gray-500 line-through'
              : 'text-gray-700 dark:text-gray-300'}>
              {s.label}
              <span className="text-gray-400 dark:text-gray-500"> — {s.why}</span>
              {s.needs_admin && !s.done && (
                <span className="ml-1 text-amber-600 dark:text-amber-400">needs your password</span>
              )}
            </span>
          </div>
        ))}
      </div>

      {plan.will_prompt && (
        <p className="text-[11px] text-gray-500 dark:text-gray-400">
          macOS will ask for your password once. LocalBook never sees it — the
          prompt is the system's own.
        </p>
      )}

      {log.length > 0 && (
        <p className="text-[11px] text-green-700 dark:text-green-400">{log.join(' · ')}</p>
      )}
      {warnings.map((w) => (
        <p key={w} className="text-[11px] text-amber-700 dark:text-amber-400">
          ⚠ {w}
        </p>
      ))}
      {warnings.length > 0 && (
        <p className="text-[11px] text-gray-500 dark:text-gray-400">
          Everything essential is ready — you can carry on installing.
        </p>
      )}
      {error && <p className="text-[11px] text-red-600 dark:text-red-400">{error}</p>}
      {details?.tried && details.tried.length > 0 && (
        <p className="text-[11px] text-gray-500 dark:text-gray-400">
          Tried to combine: {details.tried.map((d) => d.name).join(' + ')}
        </p>
      )}

      <div className="flex justify-end gap-2">
        <button onClick={onClose} disabled={busy}
                className="px-2.5 py-1 text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
          Cancel
        </button>
        <button onClick={warnings.length ? onDone : go}
                disabled={busy || (!warnings.length && outstanding.length === 0)}
                className="px-3 py-1 text-xs font-medium rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40">
          {busy ? 'Preparing…'
            : warnings.length ? 'Continue'
            : outstanding.length === 0 ? 'Nothing to do' : 'Prepare'}
        </button>
      </div>
    </div>
  );
}

function InstallPanel({ c, onClose }: { c: Companion; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  const [checked, setChecked] = useState<{ ok: boolean; error?: string } | null>(null);
  const cmd = c.install?.command || '';

  // Confirm the pinned script still hashes to what we recorded, BEFORE the user
  // runs it. A mismatch means upstream moved under the pin — which is either a
  // force-push or something worse, and either way the answer is to stop.
  useEffect(() => {
    let live = true;
    void checkInstallScript(c.id)
      .then((r) => live && setChecked({ ok: r.verification.ok, error: r.verification.error }))
      .catch((e) => live && setChecked({ ok: false, error: e?.message }));
    return () => { live = false; };
  }, [c.id]);

  return (
    <div className="mt-3 rounded-lg border dark:border-gray-700 bg-gray-50 dark:bg-gray-800/60 p-3 space-y-2.5">
      <p className="text-xs text-gray-600 dark:text-gray-300">
        {c.name} installs itself from the terminal — it needs Homebrew and your
        admin password for the audio driver, so it can't run inside this window.
        Paste this and let it finish; LocalBook takes over from there.
      </p>

      {/* Provenance. This command downloads and runs someone else's script, so
          the exact revision and its checksum are shown before it is run — not
          buried in a manifest the user never sees. */}
      {c.install?.short_ref && (
        <div className="rounded border dark:border-gray-700 bg-white/60 dark:bg-gray-900/40 px-2.5 py-2 space-y-1">
          <p className="text-[11px] text-gray-600 dark:text-gray-300">
            Pinned to <span className="font-mono">{c.install.repo}@{c.install.short_ref}</span>
            {c.install.ref_date && <> · {c.install.ref_date}</>}
          </p>
          <p className="text-[10px] font-mono text-gray-400 dark:text-gray-500 break-all">
            sha256 {c.install.sha256?.slice(0, 32)}…
          </p>
          <p className="text-[11px] text-gray-500 dark:text-gray-400">
            The command checks that hash before running anything.
            {checked?.ok && <span className="text-green-600 dark:text-green-400"> Verified just now.</span>}
            {checked && !checked.ok && (
              <span className="text-red-600 dark:text-red-400"> {checked.error}</span>
            )}
          </p>
        </div>
      )}

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
        {(c.install?.review_url || c.homepage) && (
          <a href={c.install?.review_url || c.homepage} target="_blank" rel="noreferrer"
             className="text-[11px] text-blue-600 dark:text-blue-400 hover:underline">
            Read this exact version first
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

/**
 * Optional add-ons. Shown only when the tool is installed, because offering a
 * menu-bar control for something that isn't there yet is noise.
 */
function Extras({ c, onChanged }: { c: Companion; onChanged: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!c.installed || !c.extras?.length) return null;

  const toggle = async (id: string, on: boolean) => {
    setBusy(id); setError(null);
    try {
      await (on ? installExtra(c.id, id) : removeExtra(c.id, id));
      onChanged();
    } catch (e: any) {
      setError(e?.message || 'Failed');
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="mt-3 space-y-2">
      {c.extras.map((e) => (
        <div key={e.id} className="flex items-start gap-2.5">
          <button
            role="switch"
            aria-checked={e.installed}
            disabled={busy === e.id}
            onClick={() => toggle(e.id, !e.installed)}
            className={`mt-0.5 w-8 h-4.5 rounded-full flex-shrink-0 transition-colors relative ${
              e.installed ? 'bg-blue-600' : 'bg-gray-300 dark:bg-gray-600'
            } disabled:opacity-50`}
            style={{ height: '18px', width: '32px' }}
          >
            <span className={`absolute top-0.5 w-3.5 h-3.5 rounded-full bg-white transition-all ${
              e.installed ? 'left-[15px]' : 'left-0.5'
            }`} />
          </button>
          <div className="min-w-0 flex-1">
            <p className="text-xs font-medium text-gray-800 dark:text-gray-200">
              {e.name}
              {busy === e.id && <span className="ml-1.5 text-gray-400">working…</span>}
            </p>
            <p className="text-[11px] text-gray-500 dark:text-gray-400">{e.tagline}</p>
            {!e.installed && e.host_cask && !e.host_installed && (
              <p className="text-[11px] text-gray-400 dark:text-gray-500">
                Installs {e.host_cask} first — no password needed.
              </p>
            )}
          </div>
        </div>
      ))}
      {error && <p className="text-[11px] text-red-600 dark:text-red-400">{error}</p>}
    </div>
  );
}

/**
 * Health check. Its exit code is not evidence — the installer swallows a failed
 * audio-driver install and still exits 0, so we look for the artifacts instead.
 * Partial results matter: "recording works, it just can't hear the far side" is
 * far more useful than "install failed".
 */
function HealthPanel({ c, onClose }: { c: Companion; onClose: () => void }) {
  const [result, setResult] = useState<VerifyResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    void verifyCompanion(c.id)
      .then((r) => live && setResult(r))
      .catch((e) => live && setError(e?.message || 'Check failed'));
    return () => { live = false; };
  }, [c.id]);

  return (
    <div className="mt-3 rounded-lg border dark:border-gray-700 bg-gray-50 dark:bg-gray-800/60 p-3 space-y-2">
      {!result && !error && <p className="text-xs text-gray-500">Checking…</p>}
      {error && <p className="text-xs text-red-600 dark:text-red-400">{error}</p>}
      {result && (
        <>
          <p className={`text-xs font-medium ${
            result.ok ? 'text-green-700 dark:text-green-400' : 'text-amber-700 dark:text-amber-400'
          }`}>
            {result.summary}
          </p>
          <div className="space-y-1.5">
            {result.checks.map((chk) => (
              <div key={chk.label} className="text-[11px]">
                <div className="flex items-start gap-1.5">
                  <span className={chk.ok
                    ? 'text-green-600 dark:text-green-400'
                    : 'text-red-600 dark:text-red-400'}>
                    {chk.ok ? '✓' : '✕'}
                  </span>
                  <span className={chk.ok
                    ? 'text-gray-600 dark:text-gray-400'
                    : 'text-gray-800 dark:text-gray-200'}>
                    {chk.label}
                  </span>
                </div>
                {!chk.ok && chk.fix && (
                  <p className="ml-4 text-gray-500 dark:text-gray-400">{chk.fix}</p>
                )}
              </div>
            ))}
          </div>
        </>
      )}
      <button onClick={onClose}
              className="text-[11px] text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
        Close
      </button>
    </div>
  );
}

/**
 * Upstream changes. The pin protects the user from a moving target; without
 * this it would also freeze them — an upstream bug fix nobody ever hears about.
 *
 * So the pin stays and updating is an explicit act, with the diff one click
 * away. "Something changed" without somewhere to read it is not information
 * anyone can act on, since accepting means running someone else's code.
 */
function UpdatesPanel({ c, onChanged, onClose }: {
  c: Companion; onChanged: () => void; onClose: () => void;
}) {
  const [state, setState] = useState(c.updates || null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);

  const recheck = async () => {
    setBusy('check'); setError(null); setNote(null);
    try {
      setState(await checkCompanionUpdates(c.id));
    } catch (e: any) {
      setError(e?.message || 'Could not reach GitHub.');
    } finally {
      setBusy(null);
    }
  };

  const accept = async (artifactId: string) => {
    setBusy(artifactId); setError(null);
    try {
      const res = await acceptCompanionUpdate(c.id, artifactId);
      setNote(res.reinstalled
        ? `Updated to ${res.ref}.`
        : `Pinned to ${res.ref}. ${res.note || ''}`);
      setState(await checkCompanionUpdates(c.id));
      onChanged();
    } catch (e: any) {
      setError(e?.message || 'Could not update.');
    } finally {
      setBusy(null);
    }
  };

  const changed = (state?.artifacts || []).filter((a) => a.changed);

  return (
    <div className="mt-3 rounded-lg border dark:border-gray-700 bg-gray-50 dark:bg-gray-800/60 p-3 space-y-2.5">
      <div className="flex items-center gap-2">
        <p className="text-xs font-medium text-gray-700 dark:text-gray-300">
          {state?.summary || 'Not checked yet.'}
        </p>
        <button onClick={recheck} disabled={!!busy}
                className="ml-auto text-[11px] text-blue-600 dark:text-blue-400 hover:underline disabled:opacity-40">
          {busy === 'check' ? 'Checking…' : 'Check now'}
        </button>
      </div>

      {state?.checked_at && (
        <p className="text-[11px] text-gray-400 dark:text-gray-500">
          Last checked {new Date(state.checked_at + 'Z').toLocaleString()}
        </p>
      )}

      {changed.map((a) => (
        <div key={a.id} className="rounded border dark:border-gray-700 bg-white dark:bg-gray-900 px-2.5 py-2">
          <p className="text-xs font-medium text-gray-900 dark:text-gray-100">{a.label}</p>
          <p className="mt-0.5 text-[11px] text-gray-500 dark:text-gray-400">
            You have <span className="font-mono">{a.current_ref}</span>
            {a.new_ref && <> · upstream is <span className="font-mono">{a.new_ref}</span></>}
            {a.new_date && <> ({a.new_date})</>}
          </p>
          {a.message && (
            <p className="mt-0.5 text-[11px] text-gray-600 dark:text-gray-300 italic">
              “{a.message}”
            </p>
          )}
          <div className="mt-1.5 flex items-center gap-2">
            {a.compare_url && (
              <a href={a.compare_url} target="_blank" rel="noreferrer"
                 className="text-[11px] text-blue-600 dark:text-blue-400 hover:underline">
                See what changed
              </a>
            )}
            <button
              disabled={!!busy}
              onClick={() => accept(a.id)}
              className="ml-auto px-2.5 py-1 text-[11px] font-medium rounded bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40"
            >
              {busy === a.id ? 'Updating…' : 'Update'}
            </button>
          </div>
        </div>
      ))}

      {(state?.artifacts || []).some((a) => a.error) && (
        <p className="text-[11px] text-gray-500 dark:text-gray-400">
          Some items could not be checked — GitHub may be unreachable.
        </p>
      )}
      {note && <p className="text-[11px] text-green-700 dark:text-green-400">{note}</p>}
      {error && <p className="text-[11px] text-red-600 dark:text-red-400">{error}</p>}

      <button onClick={onClose}
              className="text-[11px] text-gray-500 hover:text-gray-700 dark:hover:text-gray-300">
        Close
      </button>
    </div>
  );
}

function Card({ c, notebooks, onChanged }: {
  c: Companion;
  notebooks: Array<{ id: string; title: string }>;
  onChanged: () => void;
}) {
  const [panel, setPanel] = useState<'none' | 'prep' | 'install' | 'connect' | 'health' | 'updates'>('none');
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
            {c.updates?.has_updates && (
              <button
                onClick={() => setPanel('updates')}
                className="ml-1 px-1.5 py-0.5 text-[10px] rounded-full bg-blue-600 text-white hover:bg-blue-700"
                title="A newer version is available upstream"
              >
                Update available
              </button>
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
          {panel === 'health' && <HealthPanel c={c} onClose={() => setPanel('none')} />}
          {panel === 'updates' && (
            <UpdatesPanel c={c} onChanged={onChanged} onClose={() => setPanel('none')} />
          )}
          {panel === 'prep' && (
            <PreflightPanel c={c}
                            onDone={() => {
                              onChanged();
                              setPanel(c.installed ? 'connect' : 'install');
                            }}
                            onRefresh={onChanged}
                            onClose={() => setPanel('none')} />
          )}

          <Extras c={c} onChanged={onChanged} />
        </div>

        {/* One primary action for the state you're in. Everything else in ⋯ */}
        <div className="flex items-center gap-1 flex-shrink-0">
          {c.state === 'not_installed' && panel === 'none' && (
            <button
              onClick={() => setPanel(c.preflight?.needed && !c.preflight.after_install
                ? 'prep' : 'install')}
              className="px-3 py-1.5 text-xs font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700"
            >
              {c.preflight?.needed && !c.preflight.after_install ? 'Set up…' : 'Install…'}
            </button>
          )}
          {c.state === 'installed' && panel === 'none' && (
            c.preflight?.needed ? (
              <button onClick={() => setPanel('prep')}
                      className="px-3 py-1.5 text-xs font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700">
                Finish setup
              </button>
            ) : (
              <button onClick={() => setPanel('connect')}
                      className="px-3 py-1.5 text-xs font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700">
                Connect
              </button>
            )
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
                  <button onClick={() => { setMenu(false); setPanel('updates'); }}
                          className="w-full text-left px-3 py-1.5 text-xs text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
                    Check for updates
                  </button>
                  {c.has_checks && (
                    <button onClick={() => { setMenu(false); setPanel('health'); }}
                            className="w-full text-left px-3 py-1.5 text-xs text-gray-700 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800">
                      Check it's working
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
