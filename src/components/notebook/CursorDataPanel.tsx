import React, { useState, useEffect, useCallback } from 'react';
import { Database, FolderOpen, FileText, RefreshCw, AlertTriangle, Loader2, ShieldCheck, Table2, ChevronDown, CheckCircle2 } from 'lucide-react';
import { notebookService, CursorDataStatus, CursorTableInfo } from '../../services/notebooks';
import { isTauri } from '../../services/sources';

interface CursorDataPanelProps {
  notebookId: string | null;
}

// Format an ISO timestamp into a short, human "last refreshed" label.
function formatRefreshedAt(iso?: string): string {
  if (!iso) return 'never';
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return iso;
  const d = new Date(t);
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}

type InlineNote = { kind: 'success' | 'error' | 'info'; text: string } | null;

/**
 * Status + Refresh panel for "Cursor Style" notebooks — a notebook backed by a
 * folder that holds a SQLite .db plus AGENTS.md governance docs, queried via
 * text-to-SQL. Shows the connected folder, the .db file, the tables (row +
 * column counts), the governance files found, and a Refresh control that
 * re-reads the folder. The underlying database is never modified.
 */
export const CursorDataPanel: React.FC<CursorDataPanelProps> = ({ notebookId }) => {
  const [status, setStatus] = useState<CursorDataStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [schemaChanged, setSchemaChanged] = useState(false);
  const [note, setNote] = useState<InlineNote>(null);

  // Reconnect / change-folder affordance — collapsed by default.
  const [showReconnect, setShowReconnect] = useState(false);
  const [folderInput, setFolderInput] = useState('');
  const [reconnecting, setReconnecting] = useState(false);
  const inTauri = isTauri();

  // Native folder picker — Tauri dialog when available; text input is the fallback.
  const handleBrowseFolder = async () => {
    if (reconnecting) return;
    try {
      const { open } = await import('@tauri-apps/plugin-dialog');
      const selected = await open({ directory: true, multiple: false });
      if (typeof selected === 'string') {
        setFolderInput(selected);
      }
    } catch (err) {
      console.error('Folder picker failed:', err);
      setNote({ kind: 'error', text: 'Folder picker failed.' });
    }
  };

  const loadStatus = useCallback(async () => {
    if (!notebookId) return;
    setLoading(true);
    setLoadError(null);
    try {
      const data = await notebookService.dataStatus(notebookId);
      setStatus(data);
      setFolderInput(data.folder_path || '');
    } catch (err) {
      console.error('Failed to load cursor data status:', err);
      setLoadError('Failed to load data status.');
    } finally {
      setLoading(false);
    }
  }, [notebookId]);

  useEffect(() => {
    // Reset transient UI when the notebook changes, then load.
    setStatus(null);
    setSchemaChanged(false);
    setNote(null);
    setShowReconnect(false);
    loadStatus();
  }, [notebookId, loadStatus]);

  const handleRefresh = async () => {
    if (!notebookId || refreshing) return;
    setRefreshing(true);
    setNote(null);
    setSchemaChanged(false);
    try {
      const result = await notebookService.refreshData(notebookId);
      if (!result.ok) {
        setNote({ kind: 'error', text: result.error || 'Refresh failed.' });
      } else {
        setSchemaChanged(!!result.schema_changed);
        const tableCount = result.table_count ?? result.tables?.length ?? 0;
        const rowTotal = result.row_total ?? 0;
        setNote({
          kind: 'success',
          text: `Refreshed — ${tableCount} table${tableCount === 1 ? '' : 's'}, ${rowTotal.toLocaleString()} row${rowTotal === 1 ? '' : 's'}.`,
        });
        // Reload the full status so tables / governance / refreshed_at reflect the new read.
        await loadStatus();
      }
    } catch (err) {
      console.error('Cursor refresh failed:', err);
      setNote({ kind: 'error', text: 'Refresh failed. Check that the folder is still accessible.' });
    } finally {
      setRefreshing(false);
    }
  };

  const handleReconnect = async () => {
    if (!notebookId || reconnecting) return;
    const path = folderInput.trim();
    if (!path) return;
    setReconnecting(true);
    setNote(null);
    setSchemaChanged(false);
    try {
      const result = await notebookService.connectFolder(notebookId, path);
      if (!result.ok) {
        setNote({ kind: 'error', text: result.error || 'Could not connect to that folder.' });
      } else {
        setNote({ kind: 'success', text: 'Folder connected.' });
        setShowReconnect(false);
        await loadStatus();
      }
    } catch (err) {
      console.error('Cursor reconnect failed:', err);
      setNote({ kind: 'error', text: 'Could not connect to that folder.' });
    } finally {
      setReconnecting(false);
    }
  };

  if (!notebookId) return null;

  const tables: CursorTableInfo[] = status?.tables || [];
  const governance: string[] = status?.governance_files || [];
  const connected = status?.connected ?? false;

  return (
    <div className="px-3 py-2 space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 min-w-0">
          <Database className="w-4 h-4 text-indigo-500 dark:text-indigo-400 shrink-0" />
          <h3 className="text-sm font-semibold text-gray-900 dark:text-white truncate">Connected Data</h3>
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshing || loading}
          className="flex items-center gap-1 px-2 py-1 text-xs font-medium text-white bg-indigo-600 hover:bg-indigo-700 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          title="Re-read the folder (schema + row counts)"
        >
          {refreshing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5" />}
          {refreshing ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      {loading && !status ? (
        <div className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400 py-2">
          <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading data status…
        </div>
      ) : loadError ? (
        <div className="text-xs text-red-600 dark:text-red-400 py-1">{loadError}</div>
      ) : (
        <>
          {/* Connection status */}
          {!connected && (
            <div className="flex items-start gap-1.5 text-xs text-amber-700 dark:text-amber-300 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg px-2 py-1.5">
              <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <span>No folder connected yet. Use “Change folder” below to point at a data folder.</span>
            </div>
          )}

          {/* Ready-to-query badge */}
          {(refreshing || reconnecting) ? (
            <div className="flex items-center gap-1.5 text-xs text-indigo-700 dark:text-indigo-300 bg-indigo-50 dark:bg-indigo-900/20 border border-indigo-200 dark:border-indigo-800 rounded-lg px-2 py-1.5">
              <Loader2 className="w-3.5 h-3.5 shrink-0 animate-spin" />
              <span className="font-medium">Indexing data…</span>
            </div>
          ) : (status?.ready || (connected && status?.data_status === 'ready')) ? (
            <div className="text-xs text-green-700 dark:text-green-300 bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-800 rounded-lg px-2 py-1.5">
              <div className="flex items-center gap-1.5">
                <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
                <span className="font-semibold">Ready to query</span>
              </div>
              <p className="text-[11px] text-green-600 dark:text-green-400 mt-0.5 pl-5">
                {status?.table_count ?? tables.length} table{(status?.table_count ?? tables.length) === 1 ? '' : 's'}
                {' · '}{(status?.row_total ?? 0).toLocaleString()} rows
                {status?.views ? ` · ${status.views} views` : ''}
              </p>
            </div>
          ) : null}

          {/* Folder + db file */}
          <div className="space-y-1.5">
            <div className="flex items-start gap-1.5 min-w-0">
              <FolderOpen className="w-3.5 h-3.5 text-gray-400 shrink-0 mt-0.5" />
              <div className="min-w-0">
                <p className="text-[10px] font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wide">Folder</p>
                <p className="text-xs text-gray-700 dark:text-gray-300 break-all" title={status?.folder_path}>
                  {status?.folder_path || '—'}
                </p>
              </div>
            </div>
            <div className="flex items-start gap-1.5 min-w-0">
              <Database className="w-3.5 h-3.5 text-gray-400 shrink-0 mt-0.5" />
              <div className="min-w-0">
                <p className="text-[10px] font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wide">Database</p>
                <p className="text-xs text-gray-700 dark:text-gray-300 break-all" title={status?.db_filename}>
                  {status?.db_filename || '—'}
                </p>
              </div>
            </div>
          </div>

          {/* Schema-changed highlight */}
          {schemaChanged && (
            <div className="flex items-start gap-1.5 text-xs text-orange-700 dark:text-orange-300 bg-orange-50 dark:bg-orange-900/20 border border-orange-200 dark:border-orange-800 rounded-lg px-2 py-1.5">
              <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
              <span>Schema changed since last refresh — tables or columns are different.</span>
            </div>
          )}

          {/* Inline note (toast-style, but inline so the panel is self-contained) */}
          {note && (
            <div
              className={`text-xs rounded-lg px-2 py-1.5 border ${
                note.kind === 'error'
                  ? 'text-red-700 dark:text-red-300 bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800'
                  : note.kind === 'success'
                    ? 'text-green-700 dark:text-green-300 bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800'
                    : 'text-gray-700 dark:text-gray-300 bg-gray-50 dark:bg-gray-800 border-gray-200 dark:border-gray-700'
              }`}
            >
              {note.text}
            </div>
          )}

          {/* Tables */}
          <div>
            <div className="flex items-center gap-1.5 mb-1">
              <Table2 className="w-3.5 h-3.5 text-gray-400" />
              <p className="text-[10px] font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wide">
                Tables ({tables.length})
              </p>
            </div>
            {tables.length === 0 ? (
              <p className="text-xs text-gray-400 dark:text-gray-500 italic pl-5">No tables found</p>
            ) : (
              <div className="space-y-1">
                {tables.map((t) => (
                  <div
                    key={t.table_name}
                    className="flex items-center justify-between gap-2 px-2 py-1 rounded-md bg-gray-50 dark:bg-gray-700/50 border border-gray-200 dark:border-gray-600"
                  >
                    <span className="text-xs font-medium text-gray-800 dark:text-gray-200 truncate" title={t.table_name}>
                      {t.table_name}
                    </span>
                    <span className="text-[11px] text-gray-500 dark:text-gray-400 shrink-0 whitespace-nowrap">
                      {t.row_count.toLocaleString()} row{t.row_count === 1 ? '' : 's'} · {t.columns.length} col{t.columns.length === 1 ? '' : 's'}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Governance files */}
          <div>
            <div className="flex items-center gap-1.5 mb-1">
              <FileText className="w-3.5 h-3.5 text-gray-400" />
              <p className="text-[10px] font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wide">
                Governance files ({governance.length})
              </p>
            </div>
            {governance.length === 0 ? (
              <p className="text-xs text-gray-400 dark:text-gray-500 italic pl-5">None found (e.g. AGENTS.md)</p>
            ) : (
              <div className="flex flex-wrap gap-1 pl-5">
                {governance.map((g) => (
                  <span
                    key={g}
                    className="px-1.5 py-0.5 text-[11px] rounded bg-indigo-50 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-300"
                    title={g}
                  >
                    {g}
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* Last refreshed */}
          <p className="text-[11px] text-gray-400 dark:text-gray-500">
            Last refreshed: {formatRefreshedAt(status?.refreshed_at)}
          </p>

          {/* Read-only reassurance */}
          <div className="flex items-center gap-1.5 text-[11px] text-gray-500 dark:text-gray-400 border-t border-gray-100 dark:border-gray-700 pt-2">
            <ShieldCheck className="w-3.5 h-3.5 text-green-500 dark:text-green-400 shrink-0" />
            <span>Read-only — the database is never modified.</span>
          </div>

          {/* Reconnect / change folder (collapsed, secondary) */}
          <div className="border-t border-gray-100 dark:border-gray-700 pt-2">
            <button
              onClick={() => setShowReconnect((s) => !s)}
              className="flex items-center gap-1 text-[11px] font-medium text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 transition-colors"
            >
              <ChevronDown className={`w-3 h-3 transition-transform ${showReconnect ? 'rotate-180' : ''}`} />
              Change folder
            </button>
            {showReconnect && (
              <div className="mt-2 space-y-1.5">
                <div className="flex gap-1.5">
                  <input
                    value={folderInput}
                    onChange={(e) => setFolderInput(e.target.value)}
                    onKeyDown={(e) => { if (e.key === 'Enter') handleReconnect(); }}
                    placeholder="/path/to/data/folder"
                    className="flex-1 min-w-0 text-xs px-2 py-1 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white outline-none focus:border-indigo-400"
                  />
                  {inTauri && (
                    <button
                      onClick={handleBrowseFolder}
                      disabled={reconnecting}
                      className="flex items-center gap-1 px-2 py-1 text-xs font-medium text-gray-600 dark:text-gray-300 bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 border border-gray-300 dark:border-gray-600 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed shrink-0"
                      title="Choose a folder"
                    >
                      <FolderOpen className="w-3.5 h-3.5" />
                      Browse…
                    </button>
                  )}
                </div>
                <button
                  onClick={handleReconnect}
                  disabled={reconnecting || !folderInput.trim()}
                  className="flex items-center gap-1 px-2 py-1 text-xs font-medium text-indigo-600 dark:text-indigo-400 hover:bg-indigo-50 dark:hover:bg-indigo-900/30 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {reconnecting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FolderOpen className="w-3.5 h-3.5" />}
                  {reconnecting ? 'Connecting…' : 'Connect'}
                </button>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
};
