/**
 * Link a folder — the one dialog all three entry points share.
 *
 * Notebook creation, the notebook context menu, and Settings all open THIS,
 * so the decision a user makes is identical wherever they start from. The
 * flow is deliberately: pick → **see what we found** → choose cadence →
 * choose backfill → link.
 *
 * The preview step is the important one. It is the first moment macOS can
 * deny access, and it is the only moment the user can confirm we are looking
 * at the right folder before anything is ingested. It reads nothing but
 * directory entries — no file is opened until they say go.
 *
 * There is deliberately no file-type picker. A linked folder takes what is in
 * it; the backend defaults to every format LocalBook can read. Asking the user
 * to predict which extensions their recorder writes is a question with no good
 * answer and a bad failure mode — a format they didn't list would be ignored
 * forever, silently.
 */
import { useCallback, useEffect, useState } from 'react';
import {
  FREQUENCY_LABELS,
  createFolderLink,
  pickFolder,
  previewPath,
  type FolderLink,
  type PathPreview,
} from '../../services/folders';

interface Props {
  notebookId?: string | null;          // null/undefined => Smart Folder
  notebookTitle?: string;
  onLinked: (link: FolderLink) => void;
  onClose: () => void;
}

export function LinkFolderDialog({ notebookId, notebookTitle, onLinked, onClose }: Props) {
  const [path, setPath] = useState('');
  const [preview, setPreview] = useState<PathPreview | null>(null);
  const [checking, setChecking] = useState(false);
  const [recursive, setRecursive] = useState(false);
  const [frequency, setFrequency] = useState('hourly');
  const [backfill, setBackfill] = useState<'all' | 'new_only'>('all');
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [manualEntry, setManualEntry] = useState(false);

  const check = useCallback(async (p: string) => {
    if (!p.trim()) { setPreview(null); return; }
    setChecking(true);
    setError(null);
    try {
      setPreview(await previewPath(p, undefined, recursive));
    } catch (e: any) {
      setPreview(null);
      setError(e?.message || 'Could not read that folder.');
    } finally {
      setChecking(false);
    }
  }, [recursive]);

  // Re-check when depth changes — the count the user is about to act on must
  // match the option currently selected.
  useEffect(() => {
    if (path && preview) void check(path);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recursive]);

  const browse = async () => {
    setError(null);
    const picked = await pickFolder();
    if (!picked) {
      if (!path) setManualEntry(true);   // not in Tauri, or cancelled with nothing chosen
      return;
    }
    setPath(picked);
    void check(picked);
  };

  const submit = async () => {
    if (!preview) return;
    setSaving(true);
    setError(null);
    try {
      const link = await createFolderLink({
        path: preview.path,
        notebook_id: notebookId ?? null,
        frequency, recursive, backfill,
      });
      onLinked(link);
    } catch (e: any) {
      setError(e?.message || 'Could not link that folder.');
      setSaving(false);
    }
  };

  const count = preview?.matching_files ?? 0;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
         onClick={onClose}>
      <div className="w-full max-w-lg rounded-xl bg-white dark:bg-gray-900 shadow-xl border dark:border-gray-700"
           onClick={(e) => e.stopPropagation()}>
        <div className="px-5 py-4 border-b dark:border-gray-700">
          <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
            Link a folder
          </h2>
          <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
            {notebookId
              ? <>New files land in <span className="font-medium">{notebookTitle || 'this notebook'}</span> automatically.</>
              : <>A Smart Folder — files are suggested to a notebook rather than filed automatically.</>}
          </p>
        </div>

        <div className="px-5 py-4 space-y-4 max-h-[60vh] overflow-y-auto">
          {/* 1 — pick */}
          <div>
            <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1.5">
              Folder
            </label>
            {manualEntry ? (
              <div className="flex gap-2">
                <input
                  value={path}
                  onChange={(e) => setPath(e.target.value)}
                  onBlur={() => void check(path)}
                  placeholder="/Users/you/Documents/Recordings"
                  className="flex-1 px-3 py-2 text-sm rounded-lg border dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                />
                <button onClick={() => void check(path)}
                        className="px-3 py-2 text-sm rounded-lg bg-gray-100 dark:bg-gray-800 hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-700 dark:text-gray-300">
                  Check
                </button>
              </div>
            ) : (
              <button
                onClick={browse}
                className="w-full flex items-center gap-2 px-3 py-2.5 text-sm rounded-lg border border-dashed dark:border-gray-600 hover:border-blue-500 hover:bg-blue-50/50 dark:hover:bg-blue-900/10 text-left"
              >
                <span className="text-lg leading-none">📁</span>
                <span className={path ? 'text-gray-900 dark:text-gray-100 truncate' : 'text-gray-500 dark:text-gray-400'}>
                  {preview?.display_path || path || 'Choose a folder…'}
                </span>
              </button>
            )}
            {checking && (
              <p className="mt-1.5 text-xs text-gray-500 dark:text-gray-400">Looking…</p>
            )}
          </div>

          {/* 2 — what we found. The trust step. */}
          {preview && (
            <div className="rounded-lg bg-gray-50 dark:bg-gray-800/60 px-3 py-2.5 text-xs">
              <p className="font-medium text-gray-800 dark:text-gray-200">
                {count === 0
                  ? 'This folder is empty for now — new files will be picked up as they arrive.'
                  : `${count} file${count === 1 ? '' : 's'} found.`}
              </p>
              {preview.sample.length > 0 && (
                <p className="mt-1 text-gray-500 dark:text-gray-400 truncate">
                  {preview.sample.slice(0, 3).join(' · ')}
                  {count > 3 ? ` · +${count - 3} more` : ''}
                </p>
              )}
            </div>
          )}

          {/* 3 — options */}
          <div className={preview ? '' : 'opacity-40 pointer-events-none'}>
            <label className="flex items-center gap-2 text-xs text-gray-600 dark:text-gray-400">
              <input type="checkbox" checked={recursive}
                     onChange={(e) => setRecursive(e.target.checked)} />
              Include subfolders
            </label>

            <label className="block mt-3 text-xs font-medium text-gray-600 dark:text-gray-400 mb-1.5">
              Check for new files
            </label>
            <select value={frequency} onChange={(e) => setFrequency(e.target.value)}
                    className="w-full px-3 py-2 text-sm rounded-lg border dark:border-gray-700 bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100">
              {Object.entries(FREQUENCY_LABELS).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>

            {/* 4 — the backfill question, with the count that makes it answerable */}
            {count > 0 && (
              <div className="mt-4">
                <label className="block text-xs font-medium text-gray-600 dark:text-gray-400 mb-1.5">
                  The {count} file{count === 1 ? '' : 's'} already in this folder
                </label>
                <div className="space-y-1.5">
                  <label className="flex items-start gap-2 text-xs text-gray-700 dark:text-gray-300">
                    <input type="radio" className="mt-0.5" checked={backfill === 'all'}
                           onChange={() => setBackfill('all')} />
                    <span>
                      Add {count === 1 ? 'it' : 'them all'} now
                      <span className="block text-gray-500 dark:text-gray-400">
                        Processed in the background while the machine is idle.
                      </span>
                    </span>
                  </label>
                  <label className="flex items-start gap-2 text-xs text-gray-700 dark:text-gray-300">
                    <input type="radio" className="mt-0.5" checked={backfill === 'new_only'}
                           onChange={() => setBackfill('new_only')} />
                    <span>
                      Only files added from now on
                      <span className="block text-gray-500 dark:text-gray-400">
                        The existing {count} {count === 1 ? 'file is' : 'files are'} left untouched.
                      </span>
                    </span>
                  </label>
                </div>
              </div>
            )}
          </div>

          {error && (
            <div className="rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 px-3 py-2 text-xs text-red-700 dark:text-red-300">
              {error}
            </div>
          )}

          <p className="text-[11px] text-gray-400 dark:text-gray-500">
            LocalBook only reads this folder. It never moves, renames, edits or deletes anything in it.
          </p>
        </div>

        <div className="px-5 py-3 border-t dark:border-gray-700 flex justify-end gap-2">
          <button onClick={onClose}
                  className="px-3 py-1.5 text-sm rounded-lg text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800">
            Cancel
          </button>
          <button onClick={submit} disabled={!preview || saving}
                  className="px-3.5 py-1.5 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40 disabled:cursor-not-allowed">
            {saving ? 'Linking…' : 'Link folder'}
          </button>
        </div>
      </div>
    </div>
  );
}
