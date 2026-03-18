import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Play, Pause, Loader2, AlertCircle, RefreshCw } from 'lucide-react';
import { audioService } from '../../services/audio';
import { AudioGeneration } from '../../types';

interface AudioCanvasPlayerProps {
  audioId: string;
  notebookId: string;
  title: string;
  onStatusChange?: (status: string) => void;
}

const STATUS_LABELS: Record<string, string> = {
  pending: 'Queued — waiting to start…',
  generating_script: 'Writing script…',
  processing: 'Synthesizing audio…',
  completed: 'Ready to play',
  failed: 'Generation failed',
};

export const AudioCanvasPlayer: React.FC<AudioCanvasPlayerProps> = ({
  audioId,
  notebookId,
  title,
  onStatusChange,
}) => {
  const [audio, setAudio] = useState<AudioGeneration | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const data = await audioService.get(notebookId, audioId);
      setAudio(data);
      onStatusChange?.(data.status);

      // Stop polling once terminal
      if (data.status === 'completed' || data.status === 'failed') {
        if (pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
      }
    } catch (err: any) {
      setError(err.message || 'Failed to fetch audio status');
    }
  }, [audioId, notebookId, onStatusChange]);

  // Start polling on mount
  useEffect(() => {
    fetchStatus();
    pollRef.current = setInterval(fetchStatus, 5000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [fetchStatus]);

  const togglePlay = () => {
    if (!audioRef.current) return;
    if (playing) {
      audioRef.current.pause();
    } else {
      audioRef.current.play();
    }
    setPlaying(!playing);
  };

  const status = audio?.status || 'pending';
  const isTerminal = status === 'completed' || status === 'failed';
  const isReady = status === 'completed';
  const downloadUrl = isReady ? audioService.getDownloadUrl(audioId) : null;

  // Duration display
  const formatDuration = (seconds: number) => {
    const m = Math.floor(seconds / 60);
    const s = seconds % 60;
    return `${m}:${s.toString().padStart(2, '0')}`;
  };

  return (
    <div className="rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/40 overflow-hidden">
      {/* Status header */}
      <div className="flex items-center gap-3 px-3.5 py-2.5">
        {/* Icon / play button */}
        {isReady ? (
          <button
            onClick={togglePlay}
            className="flex-shrink-0 w-9 h-9 rounded-full bg-blue-600 hover:bg-blue-700 text-white flex items-center justify-center transition-colors shadow-sm"
          >
            {playing ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4 ml-0.5" />}
          </button>
        ) : status === 'failed' ? (
          <div className="flex-shrink-0 w-9 h-9 rounded-full bg-red-100 dark:bg-red-900/30 text-red-500 flex items-center justify-center">
            <AlertCircle className="w-4 h-4" />
          </div>
        ) : (
          <div className="flex-shrink-0 w-9 h-9 rounded-full bg-blue-100 dark:bg-blue-900/30 text-blue-500 flex items-center justify-center">
            <Loader2 className="w-4 h-4 animate-spin" />
          </div>
        )}

        {/* Info */}
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-gray-900 dark:text-white truncate">{title}</p>
          <p className={`text-xs ${status === 'failed' ? 'text-red-500' : 'text-gray-500 dark:text-gray-400'}`}>
            {error || audio?.error_message || STATUS_LABELS[status] || status}
            {audio?.duration_seconds != null && audio.duration_seconds > 0 && isReady && (
              <span className="ml-2 text-gray-400">· {formatDuration(audio.duration_seconds)}</span>
            )}
          </p>
        </div>

        {/* Progress indicator for non-terminal states */}
        {!isTerminal && (
          <div className="flex-shrink-0">
            <RefreshCw className="w-3.5 h-3.5 text-gray-400 animate-spin" />
          </div>
        )}
      </div>

      {/* Audio player — only shown when ready */}
      {downloadUrl && (
        <div className="px-3.5 pb-2.5">
          <audio
            ref={audioRef}
            src={downloadUrl}
            onEnded={() => setPlaying(false)}
            onPause={() => setPlaying(false)}
            onPlay={() => setPlaying(true)}
            controls
            className="w-full h-8 [&::-webkit-media-controls-panel]:bg-gray-100 dark:[&::-webkit-media-controls-panel]:bg-gray-800"
            style={{ height: '32px' }}
          />
        </div>
      )}

      {/* Progress bar for non-terminal states */}
      {!isTerminal && (
        <div className="h-0.5 bg-gray-200 dark:bg-gray-700 overflow-hidden">
          <div className="h-full bg-blue-500 animate-pulse" style={{ width: status === 'processing' ? '66%' : '33%' }} />
        </div>
      )}
    </div>
  );
};
