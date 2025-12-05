import React, { useState, useRef } from 'react';
import { sourceService } from '../services/sources';
import { Button } from './shared/Button';
import { ErrorMessage } from './shared/ErrorMessage';

interface SourceUploadProps {
  notebookId: string;
  onUploadComplete: () => void;
}

interface FileUploadStatus {
  file: File;
  status: 'pending' | 'uploading' | 'success' | 'error';
  error?: string;
}

export const SourceUpload: React.FC<SourceUploadProps> = ({
  notebookId,
  onUploadComplete,
}) => {
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploadStatuses, setUploadStatuses] = useState<FileUploadStatus[]>([]);
  const [isDragging, setIsDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dropZoneRef = useRef<HTMLDivElement>(null);

  const processFiles = async (files: FileList | File[]) => {
    if (!notebookId) {
      console.log('No notebook ID');
      return;
    }

    const fileArray = Array.from(files);
    console.log(`Starting batch upload: ${fileArray.length} files to notebook:`, notebookId);

    // Initialize upload statuses
    const statuses: FileUploadStatus[] = fileArray.map(file => ({
      file,
      status: 'pending'
    }));
    setUploadStatuses(statuses);
    setUploading(true);
    setError(null);

    let successCount = 0;
    let errorCount = 0;

    // Upload files sequentially to avoid overwhelming the server
    for (let i = 0; i < fileArray.length; i++) {
      const file = fileArray[i];

      // Update status to uploading
      setUploadStatuses(prev => prev.map((status, idx) =>
        idx === i ? { ...status, status: 'uploading' } : status
      ));

      try {
        await sourceService.upload(notebookId, file);
        console.log('Upload successful:', file.name);
        successCount++;

        // Update status to success
        setUploadStatuses(prev => prev.map((status, idx) =>
          idx === i ? { ...status, status: 'success' } : status
        ));
      } catch (err: any) {
        console.error('Upload failed:', file.name, err);
        const errorMessage = err.response?.data?.detail || err.message || 'Upload failed';
        errorCount++;

        // Update status to error
        setUploadStatuses(prev => prev.map((status, idx) =>
          idx === i ? { ...status, status: 'error', error: errorMessage } : status
        ));
      }
    }

    // Clear file input
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }

    // Show summary
    if (errorCount > 0) {
      setError(`Uploaded ${successCount} of ${fileArray.length} files. ${errorCount} failed.`);
    }

    // Notify completion after a delay to show final status
    setTimeout(() => {
      setUploading(false);
      if (successCount > 0) {
        onUploadComplete();
        // Clear statuses after successful uploads
        setTimeout(() => setUploadStatuses([]), 2000);
      }
    }, 1000);
  };

  const handleFileSelect = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files;
    if (!files || files.length === 0) {
      return;
    }
    await processFiles(files);
  };

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!uploading && notebookId) {
      setIsDragging(true);
    }
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    // Only set dragging to false if leaving the drop zone entirely
    if (e.currentTarget === dropZoneRef.current) {
      setIsDragging(false);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);

    if (uploading || !notebookId) {
      return;
    }

    const files = e.dataTransfer.files;
    if (files && files.length > 0) {
      await processFiles(files);
    }
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

  const completedCount = uploadStatuses.filter(s => s.status === 'success').length;
  const totalCount = uploadStatuses.length;
  const progressPercentage = totalCount > 0 ? (completedCount / totalCount) * 100 : 0;

  return (
    <div className="p-3 border-b dark:border-gray-700">
      {error && <ErrorMessage message={error} onDismiss={() => setError(null)} />}

      <input
        ref={fileInputRef}
        type="file"
        onChange={handleFileSelect}
        className="hidden"
        accept=".pdf,.docx,.txt,.md,.pptx,.xlsx,.mp3,.wav,.m4a,.aac,.ogg,.flac,.wma,.mp4,.mov,.avi,.mkv,.webm,.flv,.m4v"
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
          border-2 border-dashed rounded-lg p-4 text-center cursor-pointer transition-colors
          ${isDragging
            ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20'
            : 'border-gray-300 dark:border-gray-600 hover:border-gray-400 dark:hover:border-gray-500'
          }
          ${(!notebookId || uploading) ? 'opacity-50 cursor-not-allowed' : ''}
        `}
        onClick={() => !uploading && notebookId && fileInputRef.current?.click()}
      >
        <div className="flex flex-col items-center gap-1.5">
          <svg
            className={`w-10 h-10 ${isDragging ? 'text-blue-500' : 'text-gray-400 dark:text-gray-500'}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12"
            />
          </svg>
          <div>
            <p className="text-sm font-medium text-gray-700 dark:text-gray-300">
              {isDragging ? 'Drop files here' : 'Drag and drop files here, or click to browse'}
            </p>
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
              Supports: PDF, DOCX, PPTX, XLSX, TXT, MD, Audio (MP3, WAV, etc.), Video (MP4, MOV, etc.)
            </p>
          </div>
          {!notebookId && (
            <p className="text-sm text-red-500 dark:text-red-400 mt-2">
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
                Upload Progress: {completedCount} / {totalCount} files
              </span>
              <span className="text-gray-500 dark:text-gray-400">
                {Math.round(progressPercentage)}%
              </span>
            </div>
            <div className="w-full bg-gray-200 dark:bg-gray-700 rounded-full h-2">
              <div
                className="bg-blue-600 h-2 rounded-full transition-all duration-300"
                style={{ width: `${progressPercentage}%` }}
              />
            </div>
          </div>

          {/* Individual File Progress */}
          <div className="space-y-1 max-h-48 overflow-y-auto">
            {uploadStatuses.map((status, idx) => (
            <div
              key={idx}
              className="flex items-center gap-2 text-xs p-2 rounded bg-gray-50 dark:bg-gray-800"
            >
              <div className="flex-shrink-0">
                {getStatusIcon(status.status)}
              </div>
              <div className="flex-1 min-w-0">
                <p className="truncate text-gray-700 dark:text-gray-300">{status.file.name}</p>
                {status.error && (
                  <p className="text-red-600 dark:text-red-400 text-xs">{status.error}</p>
                )}
              </div>
              <div className="flex-shrink-0 text-gray-500 dark:text-gray-400">
                {(status.file.size / 1024).toFixed(1)} KB
              </div>
            </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
