import React, { useState, useEffect } from 'react';
import { Video, Trash2, Play, Loader2, AlertCircle } from 'lucide-react';
import { videoService, VideoGeneration } from '../../services/video';
import { API_BASE_URL } from '../../services/api';

interface VideoHistoryProps {
  notebookId: string | null;
}

const formatDuration = (seconds?: number | null) => {
  if (!seconds) return '';
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${mins}:${secs.toString().padStart(2, '0')}`;
};

const STATUS_LABELS: Record<string, string> = {
  pending: 'Queued',
  processing: 'Generating…',
  completed: 'Ready',
  failed: 'Failed',
};

const STATUS_COLORS: Record<string, string> = {
  pending: 'bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300',
  processing: 'bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300',
  completed: 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-300',
  failed: 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-300',
};

export const VideoHistory: React.FC<VideoHistoryProps> = ({ notebookId }) => {
  const [videos, setVideos] = useState<VideoGeneration[]>([]);
  const [loading, setLoading] = useState(false);
  const [playingId, setPlayingId] = useState<string | null>(null);

  const loadVideos = async () => {
    if (!notebookId) return;
    try {
      const list = await videoService.list(notebookId);
      setVideos(list);
    } catch (err) {
      console.error('Failed to load videos:', err);
    }
  };

  useEffect(() => {
    setLoading(true);
    loadVideos().finally(() => setLoading(false));
  }, [notebookId]);

  // Poll for active generations
  useEffect(() => {
    const hasActive = videos.some(v => v.status === 'pending' || v.status === 'processing');
    if (hasActive) {
      const interval = setInterval(loadVideos, 5000);
      return () => clearInterval(interval);
    }
  }, [videos]);

  const handleDelete = async (videoId: string) => {
    if (!confirm('Delete this video?')) return;
    try {
      await videoService.delete(videoId);
      setVideos(prev => prev.filter(v => v.video_id !== videoId));
    } catch (err) {
      console.error('Failed to delete video:', err);
    }
  };

  if (!notebookId) {
    return <p className="text-sm text-gray-400 dark:text-gray-500 text-center py-4">Select a notebook to view videos</p>;
  }

  if (loading && videos.length === 0) {
    return (
      <div className="flex items-center justify-center py-8">
        <Loader2 className="w-5 h-5 text-gray-400 animate-spin" />
      </div>
    );
  }

  if (videos.length === 0) {
    return (
      <div className="text-center py-6">
        <Video className="w-8 h-8 text-gray-300 dark:text-gray-600 mx-auto mb-2" />
        <p className="text-sm text-gray-500 dark:text-gray-400">No videos yet</p>
        <p className="text-xs text-gray-400 dark:text-gray-500 mt-1">Use the Video pill in the action bar to generate one</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <h4 className="text-xs font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wider">Video History</h4>
      {videos.map(v => (
        <div key={v.video_id} className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden bg-white dark:bg-gray-800">
          {/* Player area */}
          {v.status === 'completed' && playingId === v.video_id && (
            <video
              controls
              autoPlay
              className="w-full max-h-[300px] bg-black"
              src={`${API_BASE_URL}/video/stream/${v.video_id}`}
            />
          )}

          {/* Info row */}
          <div className="px-3 py-2">
            <div className="flex items-center justify-between gap-2">
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-gray-800 dark:text-gray-200 truncate">
                  {v.topic || 'Video Explainer'}
                </p>
                <div className="flex items-center gap-2 mt-0.5">
                  <span className={`px-1.5 py-0.5 text-[10px] font-medium rounded-full ${STATUS_COLORS[v.status] || STATUS_COLORS.pending}`}>
                    {STATUS_LABELS[v.status] || v.status}
                  </span>
                  <span className="text-[10px] text-gray-400">
                    {v.visual_style} · {v.format_type}
                    {v.duration_seconds ? ` · ${formatDuration(v.duration_seconds)}` : ''}
                  </span>
                  {v.status === 'processing' && v.error_message && (
                    <span className="text-[10px] text-blue-500 dark:text-blue-400 truncate max-w-[200px]">{v.error_message}</span>
                  )}
                </div>
              </div>

              <div className="flex items-center gap-1 flex-shrink-0">
                {v.status === 'completed' && (
                  <button
                    onClick={() => setPlayingId(playingId === v.video_id ? null : v.video_id)}
                    className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-500 hover:text-rose-600 transition-colors"
                    title="Play"
                  >
                    <Play className="w-4 h-4" />
                  </button>
                )}
                {v.status === 'processing' && (
                  <Loader2 className="w-4 h-4 text-blue-500 animate-spin" />
                )}
                {v.status === 'failed' && (
                  <AlertCircle className="w-4 h-4 text-red-500" />
                )}
                <button
                  onClick={() => handleDelete(v.video_id)}
                  className="p-1.5 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 text-gray-400 hover:text-red-500 transition-colors"
                  title="Delete"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>

            {v.status === 'failed' && v.error_message && (
              <p className="text-xs text-red-500 dark:text-red-400 mt-1 truncate">{v.error_message}</p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
};
