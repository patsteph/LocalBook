/**
 * The linked-folder list. One component, two scopes.
 *
 * Settings passes no notebook and gets the whole picture: a totals strip, a
 * filter, and rows grouped by destination. The notebook context menu passes a
 * notebook id and gets just that notebook's folders, flat.
 *
 * The list is **folder-first, not notebook-first**, deliberately. With fifty
 * notebooks a notebook-first tree buries the four folders that actually exist.
 * Users think in folders they own, so the folder is the row and the notebook
 * is an attribute of it.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  FREQUENCY_LABELS,
  deleteFolderLink,
  folderLedger,
  listFolderLinks,
  previewFolderLink,
  scanFolderLink,
  updateFolderLink,
  type FolderLink,
  type LedgerEntry,
  type ScanReport,
} from '../../services/folders';
import { LinkFolderDialog } from './LinkFolderDialog';
import { RoutingRulesList } from './RoutingRulesList';
import { SmartFolderQueue } from './SmartFolderQueue';
import { listPending } from '../../services/folders';

interface Props {
  notebookId?: string;            // scope to one notebook (context-menu use)
  notebookTitle?: string;
  /** Fired after a folder is successfully linked. The context-menu host uses
   *  it to close itself — a dialog that lingers after the job is done reads as
   *  "something didn't take". Settings passes nothing: the section IS the page. */
  onLinked?: () => void;
}

function ago(iso: string | null): string {
  if (!iso) return 'never';
  const then = new Date(iso.endsWith('Z') ? iso : `${iso}Z`).getTime();
  const mins = Math.floor((Date.now() - then) / 60000);
  if (!isFinite(mins) || mins < 0) return 'just now';
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function Row({ link, onChanged }: { link: FolderLink; onChanged: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [report, setReport] = useState<ScanReport | null>(null);
  const [peek, setPeek] = useState<{ new_files: number; files: any[] } | null>(null);
  const [ledger, setLedger] = useState<LedgerEntry[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [confirmUnlink, setConfirmUnlink] = useState(false);

  const act = async (label: string, fn: () => Promise<void>) => {
    setBusy(label); setErr(null);
    try { await fn(); } catch (e: any) { setErr(e?.message || 'Failed'); }
    finally { setBusy(null); }
  };

  return (
    <div className={`rounded-lg border px-3 py-2.5 ${
      link.exists
        ? 'border-gray-200 dark:border-gray-700'
        : 'border-amber-300 dark:border-amber-700 bg-amber-50/40 dark:bg-amber-900/10'
    }`}>
      <div className="flex items-start gap-3">
        <span className="text-base leading-none mt-0.5">
          {link.is_smart ? '✨' : '📁'}
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate"
             title={link.path}>
            {link.display_path}
          </p>
          <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
            {link.is_smart
              ? <span className="text-purple-600 dark:text-purple-400">Smart folder</span>
              : link.notebook_title || 'Unknown notebook'}
            {' · '}{FREQUENCY_LABELS[link.frequency] || link.frequency}
            {' · '}{link.stats.ingested} added
            {link.stats.failed > 0 && (
              <span className="text-red-600 dark:text-red-400"> · {link.stats.failed} failed</span>
            )}
            {' · scanned '}{ago(link.last_scan_at)}
          </p>
          {!link.exists && (
            <p className="mt-1 text-xs text-amber-700 dark:text-amber-400">
              This folder is no longer on disk. It may be on an unmounted drive.
            </p>
          )}
          {/* An exclusion that is invisible becomes a mystery six months later
              ("why didn't my HTML get picked up?"), so it is stated and
              removable rather than silently applied. */}
          {link.exclude?.length > 0 && (
            <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">
              Ignoring {link.exclude.join(', ')}
              <button
                onClick={() => act('unexclude', async () => {
                  await updateFolderLink(link.id, { exclude: [] });
                  onChanged();
                })}
                className="ml-1.5 underline hover:text-gray-700 dark:hover:text-gray-300"
              >
                include them
              </button>
            </p>
          )}

          {link.last_error && (
            <p className="mt-1 text-xs text-red-600 dark:text-red-400">{link.last_error}</p>
          )}
          {err && <p className="mt-1 text-xs text-red-600 dark:text-red-400">{err}</p>}

          {report && (
            <p className="mt-1.5 text-xs text-gray-600 dark:text-gray-300">
              {report.ingested > 0
                ? `Added ${report.ingested} file${report.ingested === 1 ? '' : 's'}.`
                : 'Nothing new.'}
              {report.pending > 0 && ` ${report.pending} still queued — they'll follow on the next pass.`}
              {report.failed > 0 && ` ${report.failed} failed.`}
            </p>
          )}
          {peek && (
            <p className="mt-1.5 text-xs text-gray-600 dark:text-gray-300">
              {peek.new_files === 0
                ? 'Nothing new to add right now.'
                : `Would add ${peek.new_files}: ${peek.files.slice(0, 3).map((f) => f.name).join(', ')}${peek.new_files > 3 ? '…' : ''}`}
            </p>
          )}
          {ledger && (
            <div className="mt-2 max-h-40 overflow-y-auto rounded border dark:border-gray-700 divide-y dark:divide-gray-700">
              {ledger.length === 0 && (
                <p className="px-2 py-1.5 text-xs text-gray-500">Nothing recorded yet.</p>
              )}
              {ledger.map((e) => (
                <div key={e.path} className="px-2 py-1 text-xs flex items-center gap-2">
                  <span className={
                    e.status === 'ingested' ? 'text-green-600 dark:text-green-400'
                      : e.status === 'failed' ? 'text-red-600 dark:text-red-400'
                      : 'text-gray-400'
                  }>
                    {e.status === 'ingested' ? '✓' : e.status === 'failed' ? '✕' : '–'}
                  </span>
                  <span className="truncate text-gray-700 dark:text-gray-300">{e.name}</span>
                  {e.error && <span className="ml-auto text-gray-400 truncate max-w-[45%]">{e.error}</span>}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="flex items-center gap-1 flex-shrink-0">
          <button
            title="Check the folder now"
            disabled={!!busy}
            onClick={() => act('scan', async () => {
              setPeek(null); setLedger(null);
              setReport(await scanFolderLink(link.id));
              onChanged();
            })}
            className="px-2 py-1 text-xs rounded-md bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300 disabled:opacity-40"
          >
            {busy === 'scan' ? '…' : 'Scan'}
          </button>
          <button
            title="See what a scan would add, without adding it"
            disabled={!!busy}
            onClick={() => act('peek', async () => {
              setReport(null); setLedger(null);
              setPeek(await previewFolderLink(link.id));
            })}
            className="px-2 py-1 text-xs rounded-md text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-40"
          >
            Preview
          </button>
          <button
            title="What this folder has added"
            disabled={!!busy}
            onClick={() => act('ledger', async () => {
              setReport(null); setPeek(null);
              if (ledger) { setLedger(null); return; }
              setLedger((await folderLedger(link.id)).entries);
            })}
            className="px-2 py-1 text-xs rounded-md text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-40"
          >
            History
          </button>
          <button
            title={link.enabled ? 'Pause this folder' : 'Resume this folder'}
            disabled={!!busy}
            onClick={() => act('toggle', async () => {
              await updateFolderLink(link.id, { enabled: !link.enabled });
              onChanged();
            })}
            className="px-2 py-1 text-xs rounded-md text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800 disabled:opacity-40"
          >
            {link.enabled ? 'Pause' : 'Resume'}
          </button>
          {confirmUnlink ? (
            <button
              onClick={() => act('unlink', async () => {
                await deleteFolderLink(link.id);
                onChanged();
              })}
              className="px-2 py-1 text-xs rounded-md bg-red-600 text-white hover:bg-red-700"
            >
              Confirm
            </button>
          ) : (
            <button
              title="Stop watching this folder"
              onClick={() => setConfirmUnlink(true)}
              className="px-2 py-1 text-xs rounded-md text-gray-400 hover:text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20"
            >
              Unlink
            </button>
          )}
        </div>
      </div>

      {confirmUnlink && (
        <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
          Stops watching this folder. The {link.stats.ingested} source
          {link.stats.ingested === 1 ? '' : 's'} it already added stay in the notebook.
          {' '}
          <button onClick={() => setConfirmUnlink(false)}
                  className="underline hover:text-gray-700 dark:hover:text-gray-200">
            Cancel
          </button>
        </p>
      )}

      {/* Cadence is edited in place — it is the setting people revisit. */}
      <div className="mt-2 flex items-center gap-2">
        <span className="text-[11px] text-gray-400 dark:text-gray-500">Check</span>
        <select
          value={link.frequency}
          onChange={(e) => act('freq', async () => {
            await updateFolderLink(link.id, { frequency: e.target.value });
            onChanged();
          })}
          className="text-[11px] px-1.5 py-0.5 rounded border dark:border-gray-700 bg-transparent text-gray-600 dark:text-gray-400"
        >
          {Object.entries(FREQUENCY_LABELS).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
        {!link.enabled && (
          <span className="text-[11px] px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-800 text-gray-500">
            Paused
          </span>
        )}
      </div>
    </div>
  );
}

export function FolderLinksPanel({ notebookId, notebookTitle, onLinked }: Props) {
  const [links, setLinks] = useState<FolderLink[]>([]);
  const [totals, setTotals] = useState({ folders: 0, ingested: 0, failed: 0, smart: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState('');
  const [adding, setAdding] = useState(false);
  const [tab, setTab] = useState<'folders' | 'review' | 'rules'>('folders');
  const [reviewCount, setReviewCount] = useState(0);

  const load = useCallback(async () => {
    try {
      const data = await listFolderLinks(notebookId);
      setLinks(data.links);
      setTotals(data.totals);
      setError(null);
      try {
        setReviewCount((await listPending(notebookId)).count);
      } catch { /* the queue is secondary — never block the folder list on it */ }
    } catch (e: any) {
      setError(e?.message || 'Could not load linked folders.');
    } finally {
      setLoading(false);
    }
  }, [notebookId]);

  useEffect(() => { void load(); }, [load]);

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return links;
    return links.filter((l) =>
      l.display_path.toLowerCase().includes(q) ||
      (l.notebook_title || '').toLowerCase().includes(q));
  }, [links, filter]);

  // Grouped by destination in the global view; flat when already scoped.
  const groups = useMemo(() => {
    if (notebookId) return [{ key: '', title: '', items: shown }];
    const by = new Map<string, FolderLink[]>();
    for (const l of shown) {
      const k = l.is_smart ? '✨ Smart folders' : (l.notebook_title || 'Unknown notebook');
      if (!by.has(k)) by.set(k, []);
      by.get(k)!.push(l);
    }
    return [...by.entries()]
      .sort(([a], [b]) => (a.startsWith('✨') ? -1 : b.startsWith('✨') ? 1 : a.localeCompare(b)))
      .map(([title, items]) => ({ key: title, title, items }));
  }, [shown, notebookId]);

  return (
    <div className="space-y-3">
      {!notebookId && (
        <div>
          <h2 className="text-lg font-semibold text-gray-900 dark:text-gray-100">Linked Folders</h2>
          <p className="mt-0.5 text-sm text-gray-500 dark:text-gray-400">
            Watch a folder on your Mac. Anything that lands in it becomes a source —
            searchable, chattable, and usable in everything else LocalBook makes.
          </p>
        </div>
      )}

      {/* The whole system's state in one line. */}
      {!notebookId && !loading && (
        <div className="flex items-center gap-3 text-xs text-gray-600 dark:text-gray-400">
          <span><span className="font-semibold text-gray-900 dark:text-gray-100">{totals.folders}</span> linked</span>
          <span>·</span>
          <span><span className="font-semibold text-gray-900 dark:text-gray-100">{totals.ingested}</span> files added</span>
          {totals.failed > 0 && (
            <>
              <span>·</span>
              <span className="text-red-600 dark:text-red-400">{totals.failed} failed</span>
            </>
          )}
          {totals.smart > 0 && (
            <>
              <span>·</span>
              <span className="text-purple-600 dark:text-purple-400">{totals.smart} smart</span>
            </>
          )}
        </div>
      )}

      {/* Review and Rules are only meaningful once a Smart Folder exists, so
          the tab strip appears when there is something behind it. */}
      {(totals.smart > 0 || reviewCount > 0) && (
        <div className="flex items-center gap-1 border-b dark:border-gray-700">
          {([
            ['folders', 'Folders', 0],
            ['review', 'Review', reviewCount],
            ['rules', 'Rules', 0],
          ] as const).map(([id, label, badge]) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              className={`px-3 py-1.5 text-sm border-b-2 -mb-px ${
                tab === id
                  ? 'border-blue-600 text-blue-700 dark:text-blue-300 font-medium'
                  : 'border-transparent text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'
              }`}
            >
              {label}
              {badge > 0 && (
                <span className="ml-1.5 px-1.5 py-0.5 text-[10px] rounded-full bg-purple-600 text-white">
                  {badge}
                </span>
              )}
            </button>
          ))}
        </div>
      )}

      {tab === 'review' && (
        <SmartFolderQueue notebookId={notebookId} onResolved={load} />
      )}
      {tab === 'rules' && <RoutingRulesList onChanged={load} />}

      {tab === 'folders' && (
      <div className="flex items-center gap-2">
        <button
          onClick={() => setAdding(true)}
          className="px-3 py-1.5 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700"
        >
          Link a folder
        </button>
        {!notebookId && links.length > 5 && (
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter by folder or notebook…"
            className="flex-1 px-3 py-1.5 text-sm rounded-lg border dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
          />
        )}
      </div>
      )}

      {error && (
        <div className="rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 px-3 py-2 text-sm text-red-700 dark:text-red-300">
          {error}
        </div>
      )}

      {loading && <p className="text-sm text-gray-500 dark:text-gray-400">Loading…</p>}

      {tab === 'folders' && !loading && links.length === 0 && (
        <div className="rounded-lg border border-dashed dark:border-gray-700 px-4 py-8 text-center">
          <p className="text-sm text-gray-600 dark:text-gray-300">No folders linked yet.</p>
          <p className="mt-1 text-xs text-gray-500 dark:text-gray-400 max-w-sm mx-auto">
            Point LocalBook at a folder your recorder, notes app or scanner writes into,
            and everything it produces from then on arrives here on its own.
          </p>
        </div>
      )}

      {tab === 'folders' && groups.map((g) => (
        <div key={g.key} className="space-y-2">
          {g.title && (
            <p className="text-xs font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
              {g.title}
            </p>
          )}
          {g.items.map((l) => <Row key={l.id} link={l} onChanged={load} />)}
        </div>
      ))}

      {adding && (
        <LinkFolderDialog
          notebookId={notebookId}
          notebookTitle={notebookTitle}
          onClose={() => setAdding(false)}
          onLinked={() => { setAdding(false); void load(); onLinked?.(); }}
        />
      )}
    </div>
  );
}
