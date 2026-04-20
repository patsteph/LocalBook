import React, { useState, useRef } from 'react';
import { sourceService, UploadProgressEvent } from '../services/sources';
import { ErrorMessage } from './shared/ErrorMessage';

interface SourceUploadProps {
  notebookId: string;
  onUploadComplete: () => void;
}

interface FileUploadStatus {
  file: File;
  status: 'pending' | 'uploading' | 'success' | 'error';
  percent: number;
  stage: string;
  message: string;
  error?: string;
  history: { stage: string; message: string; percent: number }[];
}

// Human-readable stage descriptions for the "journey" expander.
// Keyed by backend stage id. Shown in the order stages first appear.
const STAGE_COPY: Record<string, { label: string; blurb: string }> = {
  received:         { label: 'Received',        blurb: 'File bytes received by the local backend.' },
  detecting:        { label: 'Detecting format', blurb: 'Identifying file type from extension and magic bytes.' },
  extracting:       { label: 'Extracting text', blurb: 'Pulling clean text out of the document (PDF/OCR/DOCX/audio transcription/etc).' },
  analyzing:        { label: 'Analyzing',        blurb: 'Finding the content date and other metadata.' },
  creating_record:  { label: 'Creating record',  blurb: 'Writing the source row into SQLite so it appears in the sidebar.' },
  chunking:         { label: 'Chunking',         blurb: 'Splitting text into semantic chunks sized for retrieval (source-type aware).' },
  summarizing:      { label: 'Summarizing',      blurb: 'Local LLM (olmo-3) generates a compact summary used as a quick-retrieval chunk.' },
  hyde_questions:   { label: 'HyDE questions',   blurb: 'Generating synthetic questions each chunk answers — boosts recall at query time.' },
  embedding:        { label: 'Embedding',        blurb: 'Computing 1024-dim vectors (snowflake-arctic-embed2 via Ollama) for every chunk + summary.' },
  indexing:         { label: 'Indexing',         blurb: 'Writing vectors and metadata into the notebook\'s LanceDB table.' },
  tagging:          { label: 'Auto-tagging',     blurb: 'Categorizing the document into notebook topic tags.' },
};

const STAGE_ORDER = [
  'received', 'detecting', 'extracting', 'analyzing',
  'creating_record', 'chunking', 'summarizing', 'hyde_questions',
  'embedding', 'indexing', 'tagging',
];

export const SourceUpload: React.FC<SourceUploadProps> = ({
  notebookId,
  onUploadComplete,
}) => {
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploadStatuses, setUploadStatuses] = useState<FileUploadStatus[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const [showJourney, setShowJourney] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dropZoneRef = useRef<HTMLDivElement>(null);

  const processFiles = async (files: FileList | File[]) => {
    if (!notebookId) return;

    const fileArray = Array.from(files);

    // Initialize upload statuses
    const statuses: FileUploadStatus[] = fileArray.map(file => ({
      file,
      status: 'pending',
      percent: 0,
      stage: '',
      message: 'Queued',
      history: [],
    }));
    setUploadStatuses(statuses);
    setUploading(true);
    setError(null);

    let successCount = 0;
    let errorCount = 0;

    // Upload files sequentially to avoid overwhelming the server
    for (let i = 0; i < fileArray.length; i++) {
      const file = fileArray[i];

      // Flip to "uploading" with an initial 2% so the bar visibly starts moving
      setUploadStatuses(prev => prev.map((s, idx) =>
        idx === i ? { ...s, status: 'uploading', percent: 2, message: 'Uploading...', stage: 'uploading' } : s
      ));

      try {
        const onProgress = (evt: UploadProgressEvent) => {
          setUploadStatuses(prev => prev.map((s, idx) => {
            if (idx !== i) return s;
            // Append to history only when the stage changes (keeps the journey tidy)
            const lastStage = s.history.length ? s.history[s.history.length - 1].stage : '';
            const nextHistory = evt.stage !== lastStage
              ? [...s.history, { stage: evt.stage, message: evt.message, percent: evt.percent }]
              : s.history;
            return {
              ...s,
              stage: evt.stage,
              percent: Math.max(s.percent, evt.percent),
              message: evt.message,
              history: nextHistory,
            };
          }));
        };

        await sourceService.uploadWithProgress(notebookId, file, onProgress);
        successCount++;

        setUploadStatuses(prev => prev.map((s, idx) =>
          idx === i
            ? { ...s, status: 'success', percent: 100, stage: 'complete', message: 'Ready' }
            : s
        ));
      } catch (err: any) {
        console.error('Upload failed:', file.name, err);
        const errorMessage = err?.message || 'Upload failed';
        errorCount++;

        setUploadStatuses(prev => prev.map((s, idx) =>
          idx === i
            ? { ...s, status: 'error', error: errorMessage, message: errorMessage }
            : s
        ));
      }
    }

    if (fileInputRef.current) fileInputRef.current.value = '';

    if (errorCount > 0) {
      setError(`Uploaded ${successCount} of ${fileArray.length} files. ${errorCount} failed.`);
    }

    // Notify completion after a short delay so the final status is visible
    setTimeout(() => {
      setUploading(false);
      if (successCount > 0) {
        onUploadComplete();
        // Clear statuses after successful uploads
        setTimeout(() => setUploadStatuses([]), 3000);
      }
    }, 800);
  };

  const handleFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files;
    if (!files || files.length === 0) return;
    await processFiles(files);
  };

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!uploading && notebookId) setIsDragging(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.currentTarget === dropZoneRef.current) setIsDragging(false);
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    if (uploading || !notebookId) return;
    const files = e.dataTransfer.files;
    if (files && files.length > 0) await processFiles(files);
  };

  const getStatusIcon = (status: FileUploadStatus['status']) => {
    switch (status) {
      case 'pending':
        return <span className="text-gray-400">⏸</span>;
      case 'uploading':
        return (
          <svg className="animate-spin h-4 w-4 text-blue-600" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
        );
      case 'success':
        return <span className="text-green-600">✓</span>;
      case 'error':
        return <span className="text-red-600">✗</span>;
    }
  };

  // Overall bar = average percent across files (smooth, not step-wise)
  const totalCount = uploadStatuses.length;
  const overallPercent = totalCount > 0
    ? Math.round(uploadStatuses.reduce((sum, s) => sum + s.percent, 0) / totalCount)
    : 0;
  const completedCount = uploadStatuses.filter(s => s.status === 'success').length;
  const errorCount = uploadStatuses.filter(s => s.status === 'error').length;

  return (
    <div className="px-3 py-2">
      {error && <ErrorMessage message={error} onDismiss={() => setError(null)} />}

      <input
        ref={fileInputRef}
        type="file"
        onChange={handleFileSelect}
        className="hidden"
        accept=".pdf,.docx,.doc,.txt,.md,.pptx,.ppt,.xlsx,.xls,.csv,.epub,.ipynb,.odt,.ods,.rtf,.tex,.bib,.svg,.heic,.heif,.png,.jpg,.jpeg,.webp,.tiff,.bmp,.gif,.mp3,.wav,.m4a,.aac,.ogg,.flac,.wma,.mp4,.mov,.avi,.mkv,.webm,.wmv,.flv,.m4v"
        multiple
        disabled={uploading || !notebookId}
      />

      {/* Drag and Drop Zone */}
      <div
        ref={dropZoneRef}
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        className={`
          border-2 border-dashed rounded-lg p-3 cursor-pointer transition-colors
          ${isDragging
            ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20'
            : 'border-gray-300 dark:border-gray-600 hover:border-gray-400 dark:hover:border-gray-500'
          }
          ${(!notebookId || uploading) ? 'opacity-50 cursor-not-allowed' : ''}
        `}
        onClick={() => !uploading && notebookId && fileInputRef.current?.click()}
      >
        <div className="flex flex-col gap-1">
          <p className="text-sm font-medium text-gray-700 dark:text-gray-300 text-left">
            {isDragging ? 'Drop files here' : 'Drag and drop files here, or click to browse'}
          </p>
          <div className="flex items-end gap-2">
            <p className="flex-1 text-xs leading-tight text-gray-500 dark:text-gray-400">
              PDF, DOCX, PPTX, XLSX, ODS, CSV, EPUB, Jupyter, ODT, RTF, LaTeX, SVG, Images (OCR/HEIC), Audio, Video
            </p>
            <svg
              className={`w-8 h-8 flex-shrink-0 ${isDragging ? 'text-blue-500' : 'text-gray-300 dark:text-gray-600'}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={1.5}
                d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"
              />
            </svg>
          </div>
          {!notebookId && (
            <p className="text-xs text-red-500 dark:text-red-400">
              Select a notebook first
            </p>
          )}
        </div>
      </div>

      {/* Upload Progress */}
      {uploadStatuses.length > 0 && (
        <div className="mt-4 space-y-3">
          {/* Overall Progress Bar */}
          <div className="space-y-2">
            <div className="flex justify-between text-sm">
              <span className="text-gray-700 dark:text-gray-300 font-medium">
                {completedCount}/{totalCount} ready{errorCount > 0 ? ` · ${errorCount} failed` : ''}
              </span>
              <button
                onClick={() => setShowJourney(v => !v)}
                className="text-xs text-blue-600 dark:text-blue-400 hover:underline"
              >
                {showJourney ? 'Hide journey' : 'Show journey'}
              </button>
            </div>
            <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2 overflow-hidden">
              <div
                className="bg-blue-600 h-2 rounded-full transition-all duration-500 ease-out"
                style={{ width: `${overallPercent}%` }}
              />
            </div>
          </div>

          {/* Per-file cards */}
          <div className="space-y-2 max-h-96 overflow-y-auto">
            {uploadStatuses.map((status, idx) => (
              <div
                key={idx}
                className="p-2 rounded-lg bg-gray-50 dark:bg-gray-800 space-y-1"
              >
                <div className="flex items-center gap-2 text-xs">
                  <div className="flex-shrink-0">{getStatusIcon(status.status)}</div>
                  <div className="flex-1 min-w-0">
                    <p className="truncate text-gray-700 dark:text-gray-300 font-medium">
                      {status.file.name}
                    </p>
                  </div>
                  <div className="flex-shrink-0 text-gray-500 dark:text-gray-400 tabular-nums">
                    {status.status === 'error' ? '—' : `${status.percent}%`}
                  </div>
                </div>

                {/* Per-file progress bar */}
                {status.status !== 'error' && (
                  <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-1 overflow-hidden">
                    <div
                      className={`h-1 rounded-full transition-all duration-500 ease-out ${
                        status.status === 'success' ? 'bg-green-500' : 'bg-blue-500'
                      }`}
                      style={{ width: `${status.percent}%` }}
                    />
                  </div>
                )}

                {/* Current stage message */}
                <p className={`text-xs ${status.status === 'error'
                  ? 'text-red-600 dark:text-red-400'
                  : 'text-gray-500 dark:text-gray-400'} truncate`}
                >
                  {status.message}
                </p>

                {/* Journey: ordered checklist of stages */}
                {showJourney && status.history.length > 0 && (
                  <ul className="mt-2 pt-2 border-t border-gray-200 dark:border-gray-700 space-y-1">
                    {STAGE_ORDER.map((stageId) => {
                      const hit = status.history.find(h => h.stage === stageId);
                      const isCurrent = status.stage === stageId && status.status === 'uploading';
                      const copy = STAGE_COPY[stageId];
                      if (!copy) return null;
                      return (
                        <li
                          key={stageId}
                          className={`flex items-start gap-2 text-[11px] leading-snug ${
                            hit ? 'text-gray-700 dark:text-gray-300' : 'text-gray-400 dark:text-gray-500'
                          }`}
                        >
                          <span className="flex-shrink-0 w-4 mt-0.5">
                            {hit
                              ? (isCurrent ? '◌' : '✓')
                              : '·'}
                          </span>
                          <span className="flex-1">
                            <span className="font-medium">{copy.label}</span>
                            <span className="text-gray-500 dark:text-gray-400"> — {copy.blurb}</span>
                          </span>
                        </li>
                      );
                    })}
                  </ul>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
