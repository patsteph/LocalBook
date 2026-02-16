import React from 'react';
import { audioService } from '../../services/audio';
import { AudioGeneration } from '../../types';
import { LoadingSpinner } from '../shared/LoadingSpinner';

interface AudioHistoryProps {
  audioGenerations: AudioGeneration[];
  generatedScript: string;
  showScript: boolean;
  onHideScript: () => void;
  onDelete: (audioId: string) => void;
}

const formatDuration = (seconds?: number) => {
  if (!seconds) return 'N/A';
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${mins}:${secs.toString().padStart(2, '0')}`;
};

const getStatusColor = (status: string) => {
  switch (status) {
    case 'completed':
      return 'bg-green-100 text-green-800';
    case 'processing':
      return 'bg-blue-100 text-blue-800';
    case 'failed':
      return 'bg-red-100 text-red-800';
    default:
      return 'bg-gray-100 text-gray-800';
  }
};

export const AudioHistory: React.FC<AudioHistoryProps> = ({
  audioGenerations,
  generatedScript,
  showScript,
  onHideScript,
  onDelete,
}) => {
  return (
    <>
      {/* Generated Script Preview */}
      {generatedScript && showScript && (
        <div className="border border-green-300 dark:border-green-700 bg-green-50 dark:bg-green-900/20 rounded-lg p-4">
          <div className="flex justify-between items-center mb-2">
            <h4 className="font-medium text-sm text-green-900 dark:text-green-100">✓ Content Generated Successfully</h4>
            <button
              onClick={onHideScript}
              className="text-sm text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300"
            >
              Hide
            </button>
          </div>
          <div className="mb-3 p-3 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-700 rounded text-sm text-blue-900 dark:text-blue-100">
            ✓ <strong>Content generated successfully!</strong> Audio is now being generated and will appear below when ready (usually 1-2 minutes).
          </div>
          <details className="mb-3">
            <summary className="text-sm font-medium text-gray-700 dark:text-gray-300 cursor-pointer hover:text-gray-900 dark:hover:text-white">
              View Generated Content
            </summary>
            <div className="mt-2 text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap max-h-64 overflow-y-auto bg-white dark:bg-gray-800 p-3 rounded border border-gray-200 dark:border-gray-600">
              {generatedScript}
            </div>
          </details>
        </div>
      )}

      {/* Previous Audio Generations */}
      <div>
        <h4 className="font-medium text-sm mb-3 text-gray-900 dark:text-white">Previous Audio Generations</h4>
        {audioGenerations.length === 0 ? (
          <p className="text-sm text-gray-500 dark:text-gray-400">No generations yet</p>
        ) : (
          <div className="space-y-3">
            {audioGenerations.map((gen) => (
              <div
                key={gen.audio_id}
                className="border border-gray-300 rounded-lg p-4"
              >
                <div className="flex justify-between items-start mb-2">
                  <div className="flex-1">
                    <span
                      className={`inline-block px-2 py-0.5 text-xs rounded ${getStatusColor(gen.status)}`}
                    >
                      {gen.status}
                    </span>
                    {gen.duration_seconds && (
                      <span className="ml-2 text-sm text-gray-600 dark:text-gray-400">
                        {formatDuration(gen.duration_seconds)}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-gray-500 dark:text-gray-400">
                      {new Date(gen.created_at).toLocaleString()}
                    </span>
                    <button
                      onClick={() => onDelete(gen.audio_id)}
                      className="text-red-500 hover:text-red-700 p-1"
                      title="Delete"
                    >
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                      </svg>
                    </button>
                  </div>
                </div>

                {/* Audio Player */}
                {gen.status === 'completed' && gen.audio_file_path && (
                  <div className="mt-3">
                    <audio
                      controls
                      className="w-full"
                      src={audioService.getDownloadUrl(gen.audio_id)}
                    >
                      Your browser does not support audio playback.
                    </audio>
                  </div>
                )}

                {(gen.status === 'pending' || gen.status === 'processing') && (
                  <div className="mt-3 flex items-center gap-2">
                    <LoadingSpinner size="sm" />
                    <span className="text-sm text-gray-600 dark:text-gray-400">
                      {gen.error_message || (gen.status === 'pending' ? 'Starting...' : 'Generating audio...')}
                    </span>
                  </div>
                )}

                {gen.status === 'failed' && gen.error_message && (
                  <div className="mt-3 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-700 rounded">
                    <p className="text-sm font-medium text-red-800 dark:text-red-200 mb-1">
                      ❌ Generation Failed
                    </p>
                    <p className="text-xs text-red-700 dark:text-red-300">
                      {gen.error_message}
                    </p>
                  </div>
                )}

                {/* Content Preview */}
                {gen.script && (
                  <details className="mt-3">
                    <summary className="text-sm text-blue-600 cursor-pointer hover:text-blue-700">
                      View Content
                    </summary>
                    <div className="mt-2 text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap max-h-48 overflow-y-auto bg-gray-50 dark:bg-gray-800 p-3 rounded">
                      {gen.script}
                    </div>
                  </details>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
};
