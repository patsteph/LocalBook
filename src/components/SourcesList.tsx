import React, { useState, useEffect, useRef } from 'react';
import { sourceService } from '../services/sources';
import { Source } from '../types';
import { LoadingSpinner } from './shared/LoadingSpinner';
import { ErrorMessage } from './shared/ErrorMessage';
import { SourceNotesViewer } from './SourceNotesViewer';
import { API_BASE_URL } from '../services/api';

interface SourcesListProps {
  notebookId: string | null;
  onSourcesChange?: () => void;
  selectedSourceId?: string | null;  // Currently selected source for constellation filtering
  onSourceSelect?: (sourceId: string) => void;  // Callback when a source is clicked
}

export const SourcesList: React.FC<SourcesListProps> = ({ notebookId, onSourcesChange, selectedSourceId, onSourceSelect }) => {
  const [sources, setSources] = useState<Source[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [viewingSource, setViewingSource] = useState<Source | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  // WebSocket connection for real-time source updates
  useEffect(() => {
    if (!notebookId) return;

    const wsUrl = API_BASE_URL.replace('http', 'ws') + '/constellation/ws';
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === 'source_updated' && message.data?.notebook_id === notebookId) {
          // Refresh sources list when a source is updated
          loadSources();
          // Also refresh notebook counts in header
          onSourcesChange?.();
        }
      } catch (e) {
        console.error('WebSocket message parse error:', e);
      }
    };

    ws.onerror = (e) => console.error('WebSocket error:', e);

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [notebookId, onSourcesChange]);

  useEffect(() => {
    console.log('SourcesList useEffect triggered, notebookId:', notebookId);
    if (notebookId) {
      loadSources();
    } else {
      setSources([]);
    }
  }, [notebookId]);

  const loadSources = async () => {
    if (!notebookId) return;

    console.log('Loading sources for notebook:', notebookId);
    setLoading(true);
    setError(null);
    try {
      const data = await sourceService.list(notebookId);
      console.log('Sources loaded:', data);
      console.log('DEBUG: First source structure:', data[0] ? Object.keys(data[0]) : 'no sources');
      setSources(data);
    } catch (err: any) {
      console.error('Failed to load sources:', err);
      console.error('Error response:', err.response?.data);
      setError('Failed to load documents');
    } finally {
      setLoading(false);
    }
  };

  const handleDeleteSource = async (sourceId: string) => {
    if (!notebookId) return;

    const confirmed = window.confirm('Are you sure you want to delete this source? This action cannot be undone.');
    if (!confirmed) return;

    try {
      await sourceService.delete(notebookId, sourceId);
      // Reload sources after deletion
      await loadSources();
      // Notify parent to refresh notebook counts
      onSourcesChange?.();
    } catch (err: any) {
      console.error('Failed to delete source:', err);
      setError('Failed to delete source');
    }
  };


  if (!notebookId) {
    return (
      <div className="p-4">
        <h3 className="font-semibold mb-2 text-gray-900 dark:text-white">Sources</h3>
        <p className="text-sm text-gray-500 dark:text-gray-400">Select a notebook to view sources</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="p-4">
        <h3 className="font-semibold mb-2 text-gray-900 dark:text-white">Sources</h3>
        <LoadingSpinner />
      </div>
    );
  }

  console.log('Rendering SourcesList, sources count:', sources.length);

  return (
    <div className="p-4">
      <h3 className="font-semibold mb-2 text-gray-900 dark:text-white">Sources ({sources.length})</h3>

      {error && <ErrorMessage message={error} onDismiss={() => setError(null)} />}

      {sources.length === 0 ? (
        <p className="text-sm text-gray-500 dark:text-gray-400">No sources uploaded yet</p>
      ) : (
        <div className="space-y-2">
          {sources.map((source) => {
            const isSelected = selectedSourceId === source.id;
            return (
              <div
                key={source.id}
                onClick={() => onSourceSelect?.(source.id)}
                className={`p-3 rounded border transition cursor-pointer ${
                  isSelected
                    ? 'border-purple-500 dark:border-purple-400 bg-purple-50 dark:bg-purple-900/20 ring-1 ring-purple-500 dark:ring-purple-400'
                    : 'border-gray-300 dark:border-gray-600 hover:border-gray-400 dark:hover:border-gray-500 bg-white dark:bg-gray-700'
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      {isSelected && (
                        <span className="text-purple-600 dark:text-purple-400 text-xs">●</span>
                      )}
                      <p className={`font-medium text-sm truncate ${isSelected ? 'text-purple-700 dark:text-purple-300' : 'text-gray-900 dark:text-white'}`} title={source.filename}>
                        {source.filename}
                      </p>
                    </div>
                    <div className="flex gap-3 mt-1 text-xs text-gray-600 dark:text-gray-400">
                      <span>{source.format?.toUpperCase() || 'FILE'}</span>
                      {(source.chunks ?? 0) > 0 && <span>{source.chunks} chunks</span>}
                      <span>{((source.char_count || source.characters || 0) / 1000).toFixed(1)}k chars</span>
                    </div>
                    {/* Tags display */}
                    {source.tags && source.tags.length > 0 && (
                      <div className="flex flex-wrap gap-1 mt-1">
                        {source.tags.slice(0, 3).map((tag: string) => (
                          <span
                            key={tag}
                            className="px-1.5 py-0.5 text-xs bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300 rounded"
                          >
                            {tag}
                          </span>
                        ))}
                        {source.tags.length > 3 && (
                          <span className="px-1.5 py-0.5 text-xs text-gray-500 dark:text-gray-400">
                            +{source.tags.length - 3}
                          </span>
                        )}
                      </div>
                    )}
                    <span className={`inline-block mt-1 px-2 py-0.5 text-xs rounded ${
                      source.status === 'completed'
                        ? 'bg-green-100 text-green-800 dark:bg-green-900/30 dark:text-green-400'
                        : 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-400'
                    }`}>
                      {source.status}
                    </span>
                  </div>
                  <div className="flex gap-2" onClick={(e) => e.stopPropagation()}>
                    <button
                      onClick={() => setViewingSource(source)}
                      className="px-3 py-1 text-xs font-medium text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300 hover:bg-blue-50 dark:hover:bg-blue-900/30 rounded transition-colors"
                      title="View source & notes"
                    >
                      View
                    </button>
                    <button
                      onClick={() => handleDeleteSource(source.id)}
                      className="px-3 py-1 text-xs font-medium text-red-600 dark:text-red-400 hover:text-red-800 dark:hover:text-red-300 hover:bg-red-50 dark:hover:bg-red-900/30 rounded transition-colors"
                      title="Delete source"
                    >
                      Delete
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Source & Notes Viewer Modal */}
      {viewingSource && notebookId && (
        <SourceNotesViewer
          notebookId={notebookId}
          sourceId={viewingSource.id}
          sourceName={viewingSource.filename}
          onClose={() => setViewingSource(null)}
        />
      )}
    </div>
  );
};
