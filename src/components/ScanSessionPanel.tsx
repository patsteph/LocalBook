// ScanSessionPanel — Sprint 8 (multi-page scanning session)
//
// Appears next to the Scan menu when Session Mode is ON. Shows a thumbnail
// grid of captured pages, lets the user reorder (up/down arrows) and delete
// individual pages, and commits the whole batch to /scan/process-batch with
// a live progress bar driven by the same SSE event shape as document upload.
//
// State is lifted: the parent owns `session` so it can route new captures
// (Continuity or file picker) directly into `onAddPage`. Persistence to
// localStorage is handled by the parent via scanSession.ts helpers.

import React, { useState } from 'react';
import { convertFileSrc } from '@tauri-apps/api/core';
import { ArrowUp, ArrowDown, X, Loader2, CheckCircle2, AlertCircle, Play } from 'lucide-react';
import { ScanSessionState } from '../services/scanSession';
import { scanService, ScanProgressEvent } from '../services/scanService';

interface Props {
  session: ScanSessionState;
  onReorder: (fromIndex: number, toIndex: number) => void;
  onDelete: (index: number) => void;
  onFinish: (noteId?: string) => void;
  onCancel: () => void;
}

interface ProgressState {
  running: boolean;
  percent: number;
  stage: string;
  message: string;
  error?: string;
}

const initialProgress: ProgressState = {
  running: false,
  percent: 0,
  stage: '',
  message: '',
};

export const ScanSessionPanel: React.FC<Props> = ({
  session,
  onReorder,
  onDelete,
  onFinish,
  onCancel,
}) => {
  const [progress, setProgress] = useState<ProgressState>(initialProgress);

  const canFinish = session.pages.length > 0 && !progress.running;

  const handleFinish = async () => {
    if (!canFinish) return;
    setProgress({ running: true, percent: 0, stage: 'starting', message: 'Starting…' });

    try {
      const result = await scanService.processBatchWithProgress(
        session.pages.map(p => p.path),
        {
          notebookId: session.notebookId,
          mode: session.mode,
          onProgress: (evt: ScanProgressEvent) => {
            setProgress({
              running: true,
              percent: evt.percent,
              stage: evt.stage,
              message: evt.message,
            });
          },
        },
      );
      setProgress({
        running: false,
        percent: 100,
        stage: 'complete',
        message: result.title ? `Saved "${result.title}"` : 'Saved',
      });
      onFinish(result.note_id);
    } catch (err: any) {
      setProgress({
        running: false,
        percent: progress.percent,
        stage: 'error',
        message: '',
        error: err?.message || String(err),
      });
    }
  };

  return (
    <div className="mt-3 p-3 border border-blue-200 dark:border-blue-800 bg-blue-50/60 dark:bg-blue-900/20 rounded-lg">
      <div className="flex items-center justify-between mb-2">
        <div>
          <div className="text-sm font-semibold text-gray-900 dark:text-gray-100">
            Scanning Session
            <span className="ml-2 text-xs font-normal text-gray-500 dark:text-gray-400">
              {session.pages.length} page{session.pages.length !== 1 ? 's' : ''} · {session.mode}
            </span>
          </div>
          <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
            Add pages from iPhone or file, then finish to merge them into one note.
          </div>
        </div>
        <button
          onClick={onCancel}
          disabled={progress.running}
          className="text-xs text-gray-500 hover:text-red-600 dark:hover:text-red-400 disabled:opacity-50"
        >
          Cancel session
        </button>
      </div>

      {/* Thumbnails */}
      {session.pages.length === 0 ? (
        <div className="py-6 text-center text-sm text-gray-500 dark:text-gray-400">
          No pages yet. Use the Scan menu to add pages.
        </div>
      ) : (
        <div className="grid grid-cols-4 gap-2 md:grid-cols-6">
          {session.pages.map((p, idx) => (
            <PageThumb
              key={p.path + idx}
              page={p}
              index={idx}
              total={session.pages.length}
              disabled={progress.running}
              onMoveUp={() => onReorder(idx, idx - 1)}
              onMoveDown={() => onReorder(idx, idx + 1)}
              onDelete={() => onDelete(idx)}
            />
          ))}
        </div>
      )}

      {/* Progress / Finish button */}
      <div className="mt-3">
        {progress.running || progress.stage === 'complete' ? (
          <ProgressBar progress={progress} />
        ) : progress.error ? (
          <div className="flex items-center gap-2 text-sm text-red-600 dark:text-red-400">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span className="min-w-0 truncate">{progress.error}</span>
            <button
              onClick={handleFinish}
              className="ml-auto px-2 py-1 text-xs bg-red-100 dark:bg-red-900/40 hover:bg-red-200 dark:hover:bg-red-900/60 rounded"
            >
              Retry
            </button>
          </div>
        ) : (
          <button
            onClick={handleFinish}
            disabled={!canFinish}
            className="w-full flex items-center justify-center gap-2 px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Play className="w-4 h-4" />
            Finish & Transcribe ({session.pages.length} page{session.pages.length !== 1 ? 's' : ''})
          </button>
        )}
      </div>
    </div>
  );
};

// ── Thumbnail ────────────────────────────────────────────────────────────────
interface PageThumbProps {
  page: { path: string; label: string };
  index: number;
  total: number;
  disabled: boolean;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onDelete: () => void;
}

const PageThumb: React.FC<PageThumbProps> = ({
  page, index, total, disabled, onMoveUp, onMoveDown, onDelete,
}) => {
  // convertFileSrc turns an absolute filesystem path into an asset:// URL
  // that Tauri's asset protocol can serve (scope is set in tauri.conf.json).
  const src = React.useMemo(() => {
    try {
      return convertFileSrc(page.path);
    } catch {
      return '';
    }
  }, [page.path]);

  return (
    <div className="group relative aspect-[3/4] rounded-md overflow-hidden border border-gray-200 dark:border-gray-700 bg-gray-100 dark:bg-gray-800">
      {src ? (
        <img
          src={src}
          alt={page.label}
          className="w-full h-full object-cover"
          loading="lazy"
        />
      ) : (
        <div className="w-full h-full flex items-center justify-center text-[10px] text-gray-400">
          (preview unavailable)
        </div>
      )}

      {/* Page number badge */}
      <div className="absolute top-1 left-1 px-1.5 py-0.5 text-[10px] font-semibold text-white bg-black/60 rounded">
        {index + 1}
      </div>

      {/* Hover controls */}
      {!disabled && (
        <div className="absolute inset-0 opacity-0 group-hover:opacity-100 transition bg-black/40 flex flex-col items-stretch justify-between p-1">
          <div className="flex justify-end">
            <button
              onClick={onDelete}
              title="Remove page"
              className="p-1 text-white bg-red-600/80 hover:bg-red-600 rounded"
            >
              <X className="w-3 h-3" />
            </button>
          </div>
          <div className="flex justify-between">
            <button
              onClick={onMoveUp}
              disabled={index === 0}
              title="Move earlier"
              className="p-1 text-white bg-gray-700/80 hover:bg-gray-700 rounded disabled:opacity-30 disabled:cursor-not-allowed"
            >
              <ArrowUp className="w-3 h-3" />
            </button>
            <button
              onClick={onMoveDown}
              disabled={index === total - 1}
              title="Move later"
              className="p-1 text-white bg-gray-700/80 hover:bg-gray-700 rounded disabled:opacity-30 disabled:cursor-not-allowed"
            >
              <ArrowDown className="w-3 h-3" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

// ── Progress bar ─────────────────────────────────────────────────────────────
const ProgressBar: React.FC<{ progress: ProgressState }> = ({ progress }) => {
  const done = progress.stage === 'complete';
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2 text-xs text-gray-700 dark:text-gray-300">
        {done ? (
          <CheckCircle2 className="w-4 h-4 text-green-600" />
        ) : (
          <Loader2 className="w-4 h-4 animate-spin" />
        )}
        <span className="min-w-0 truncate flex-1">{progress.message || progress.stage}</span>
        <span className="tabular-nums text-gray-500">{progress.percent}%</span>
      </div>
      <div className="h-1.5 bg-gray-200 dark:bg-gray-700 rounded overflow-hidden">
        <div
          className={`h-full transition-all duration-300 ${done ? 'bg-green-500' : 'bg-blue-500'}`}
          style={{ width: `${progress.percent}%` }}
        />
      </div>
    </div>
  );
};
