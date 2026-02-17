import React, { useState, useRef, useEffect } from 'react';
import { voiceService, VoiceStatus, TranscriptionResult } from '../services/voice';
import { Button } from './shared/Button';
import { LoadingSpinner } from './shared/LoadingSpinner';

interface VoicePanelProps {
  notebookId: string;
  onSourceAdded?: () => void;
}

export const VoicePanel: React.FC<VoicePanelProps> = ({ notebookId, onSourceAdded }) => {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<VoiceStatus | null>(null);
  const [result, setResult] = useState<TranscriptionResult | null>(null);
  const [title, setTitle] = useState('');
  const [addAsSource, setAddAsSource] = useState(true);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    checkStatus();
  }, []);

  const checkStatus = async () => {
    try {
      const data = await voiceService.getStatus();
      setStatus(data);
    } catch (err) {
      console.error('Failed to check voice status:', err);
    }
  };

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      setSelectedFile(file);
      setResult(null);
      setError(null);
    }
  };

  const handleTranscribe = async () => {
    if (!selectedFile) return;

    setLoading(true);
    setError(null);
    try {
      const data = await voiceService.transcribe(
        selectedFile,
        notebookId,
        title || undefined,
        addAsSource
      );
      setResult(data);
      if (data.source_id && onSourceAdded) {
        onSourceAdded();
      }
    } catch (err: any) {
      setError(err.message || 'Failed to transcribe audio');
    } finally {
      setLoading(false);
    }
  };

  const formatDuration = (seconds: number) => {
    const mins = Math.floor(seconds / 60);
    const secs = Math.floor(seconds % 60);
    return `${mins}:${secs.toString().padStart(2, '0')}`;
  };

  if (status && !status.available) {
    return (
      <div className="text-center py-6 text-gray-500 dark:text-gray-400">
        <p className="text-3xl mb-2">🎤</p>
        <p className="font-medium">Voice Notes Unavailable</p>
        <p className="text-sm mt-1">{status.message}</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Status */}
      {status?.available && (
        <div className="text-xs text-green-600 dark:text-green-400 flex items-center gap-1">
          <span className="w-2 h-2 bg-green-500 rounded-full"></span>
          Whisper ready ({status.model} model)
        </div>
      )}

      {/* File Upload */}
      <div 
        className="border-2 border-dashed border-gray-300 dark:border-gray-600 rounded-lg p-6 text-center cursor-pointer hover:border-blue-400 transition-colors"
        onClick={() => fileInputRef.current?.click()}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".mp3,.wav,.m4a,.webm,.ogg,.flac"
          onChange={handleFileSelect}
          className="hidden"
        />
        
        {selectedFile ? (
          <div>
            <p className="text-2xl mb-2">🎵</p>
            <p className="font-medium text-gray-900 dark:text-white">{selectedFile.name}</p>
            <p className="text-sm text-gray-500">
              {(selectedFile.size / 1024 / 1024).toFixed(2)} MB
            </p>
          </div>
        ) : (
          <div>
            <p className="text-3xl mb-2">🎤</p>
            <p className="text-gray-600 dark:text-gray-400">
              Drop audio file or click to select
            </p>
            <p className="text-xs text-gray-500 mt-1">
              MP3, WAV, M4A, WebM, OGG, FLAC
            </p>
          </div>
        )}
      </div>

      {/* Options */}
      {selectedFile && (
        <div className="space-y-3">
          <div>
            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">
              Title (optional)
            </label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Voice note title..."
              className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
            />
          </div>

          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={addAsSource}
              onChange={(e) => setAddAsSource(e.target.checked)}
              className="rounded border-gray-300"
            />
            <span className="text-sm text-gray-700 dark:text-gray-300">
              Add transcription as source
            </span>
          </label>

          <Button onClick={handleTranscribe} disabled={loading} className="w-full">
            {loading ? (
              <>
                <LoadingSpinner size="sm" />
                <span className="ml-2">Transcribing...</span>
              </>
            ) : (
              '🎯 Transcribe'
            )}
          </Button>
        </div>
      )}

      {error && (
        <div className="bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 p-3 rounded-lg text-sm">
          {error}
        </div>
      )}

      {/* Result */}
      {result && (
        <div className="space-y-3">
          <div className="flex justify-between text-sm text-gray-500">
            <span>Duration: {formatDuration(result.duration_seconds)}</span>
            <span>Language: {result.language || 'auto'}</span>
          </div>

          <div className="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg p-4 max-h-48 overflow-y-auto">
            <p className="text-sm text-gray-900 dark:text-white whitespace-pre-wrap">
              {result.text}
            </p>
          </div>

          {result.source_id && (
            <div className="bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400 p-3 rounded-lg text-sm">
              ✓ Added as source to notebook
            </div>
          )}

          <Button
            variant="secondary"
            onClick={() => {
              setSelectedFile(null);
              setResult(null);
              setTitle('');
            }}
            className="w-full"
          >
            Transcribe Another
          </Button>
        </div>
      )}
    </div>
  );
};
