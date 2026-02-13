import React, { useState, useEffect, useRef } from 'react';
import { BookOpen, Trash2 } from 'lucide-react';
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
  const [activeTagFilter, setActiveTagFilter] = useState<string | null>(null);
  const [autoTagging, setAutoTagging] = useState(false);
  const [showTagCloud, setShowTagCloud] = useState(false);
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

  // Compute tag counts from sources
  const tagCounts = React.useMemo(() => {
    const counts: Record<string, number> = {};
    sources.forEach(s => {
      (s.tags || []).forEach((tag: string) => {
        counts[tag] = (counts[tag] || 0) + 1;
      });
    });
    return Object.entries(counts)
      .sort((a, b) => b[1] - a[1]);
  }, [sources]);

  const untaggedCount = sources.filter(s => !s.tags || s.tags.length === 0).length;

  // Filter sources by active tag
  const filteredSources = activeTagFilter
    ? sources.filter(s => s.tags?.includes(activeTagFilter))
    : sources;

  const handleAutoTagAll = async () => {
    if (!notebookId || autoTagging) return;
    setAutoTagging(true);
    try {
      const result = await sourceService.autoTagAll(notebookId);
      console.log('[AutoTag]', result.message);
      // Poll for updates as tags are generated in background
      const poll = setInterval(async () => {
        await loadSources();
      }, 3000);
      // Stop polling after reasonable time (2s per source)
      setTimeout(() => {
        clearInterval(poll);
        setAutoTagging(false);
        loadSources();
      }, Math.max(15000, result.queued * 2000));
    } catch (err) {
      console.error('Auto-tag failed:', err);
      setError('Failed to start auto-tagging');
      setAutoTagging(false);
    }
  };

  // Clear filter when notebook changes
  useEffect(() => {
    setActiveTagFilter(null);
  }, [notebookId]);

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
      <div className="flex items-center justify-between mb-2">
        <h3 className="font-semibold text-gray-900 dark:text-white">
          Sources ({activeTagFilter ? `${filteredSources.length}/${sources.length}` : sources.length})
        </h3>
        {untaggedCount > 0 && (
          <button
            onClick={handleAutoTagAll}
            disabled={autoTagging}
            className="px-2 py-1 text-xs font-medium text-purple-600 dark:text-purple-400 hover:bg-purple-50 dark:hover:bg-purple-900/30 rounded transition-colors disabled:opacity-50"
            title={`Auto-tag ${untaggedCount} untagged sources`}
          >
            {autoTagging ? 'Tagging...' : `Tag ${untaggedCount} untagged`}
          </button>
        )}
      </div>

      {error && <ErrorMessage message={error} onDismiss={() => setError(null)} />}

      {/* Tag filter toggle + collapsible cloud */}
      {tagCounts.length > 0 && (
        <div className="mb-2" onClick={(e) => e.stopPropagation()}>
          <div className="flex items-center gap-2 mb-1">
            <button
              onClick={() => setShowTagCloud(!showTagCloud)}
              className="flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors text-gray-600 dark:text-gray-300"
            >
              <span>Tag ☁️</span>
              <span className="opacity-50">{tagCounts.length}</span>
              <span className="text-[10px] opacity-40">{showTagCloud ? '▲' : '▼'}</span>
            </button>
            {activeTagFilter && (
              <button
                onClick={() => setActiveTagFilter(null)}
                className="flex items-center gap-1 px-2 py-0.5 text-xs font-medium rounded bg-blue-600 text-white dark:bg-blue-500 hover:bg-blue-700 dark:hover:bg-blue-600 transition-colors"
              >
                {activeTagFilter} <span className="ml-1">✕</span>
              </button>
            )}
          </div>
          {showTagCloud && (
            <div className="flex flex-wrap gap-1 max-h-40 overflow-y-auto">
              {tagCounts.map(([tag, count]) => (
                <button
                  key={tag}
                  onClick={() => {
                    setActiveTagFilter(activeTagFilter === tag ? null : tag);
                    setShowTagCloud(false);
                  }}
                  className={`px-2 py-0.5 text-xs rounded transition-colors ${
                    activeTagFilter === tag
                      ? 'bg-blue-600 text-white dark:bg-blue-500'
                      : 'bg-blue-50 text-blue-700 dark:bg-blue-900/30 dark:text-blue-300 hover:bg-blue-100 dark:hover:bg-blue-900/50'
                  }`}
                >
                  {tag} <span className="opacity-60">{count}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {sources.length === 0 ? (
        <p className="text-sm text-gray-500 dark:text-gray-400">No sources uploaded yet</p>
      ) : filteredSources.length === 0 ? (
        <p className="text-sm text-gray-500 dark:text-gray-400">No sources match tag "{activeTagFilter}"</p>
      ) : (
        <div className="space-y-2">
          {filteredSources.map((source) => {
            const isSelected = selectedSourceId === source.id;
            const sourceTags = source.tags || [];
            // Show active filter tag first if present, then up to 1 more
            const displayTags: string[] = [];
            if (activeTagFilter && sourceTags.includes(activeTagFilter)) {
              displayTags.push(activeTagFilter);
            }
            for (const t of sourceTags) {
              if (displayTags.length >= 2) break;
              if (!displayTags.includes(t)) displayTags.push(t);
            }
            const remainingCount = sourceTags.length - displayTags.length;

            return (
              <div
                key={source.id}
                onClick={() => onSourceSelect?.(source.id)}
                className={`px-4 py-2 rounded border transition cursor-pointer overflow-hidden ${
                  isSelected
                    ? 'border-purple-500 dark:border-purple-400 bg-purple-50 dark:bg-purple-900/20 ring-1 ring-purple-500 dark:ring-purple-400'
                    : 'border-gray-300 dark:border-gray-600 hover:border-gray-400 dark:hover:border-gray-500 bg-white dark:bg-gray-700'
                }`}
              >
                <div className="flex items-center justify-between gap-1">
                  <p className={`font-medium text-sm truncate flex-1 min-w-0 ${isSelected ? 'text-purple-700 dark:text-purple-300' : 'text-gray-900 dark:text-white'}`} title={source.filename}>
                    {isSelected && <span className="text-purple-600 dark:text-purple-400 text-xs mr-1">●</span>}
                    {source.filename}
                  </p>
                  <div className="flex gap-0.5 shrink-0" onClick={(e) => e.stopPropagation()}>
                    <button
                      onClick={() => setViewingSource(source)}
                      className="p-1 text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 hover:bg-blue-50 dark:hover:bg-blue-900/30 rounded transition-colors"
                      title="View source & notes"
                    >
                      <BookOpen size={14} />
                    </button>
                    <button
                      onClick={() => handleDeleteSource(source.id)}
                      className="p-1 text-gray-300 hover:text-red-600 dark:text-gray-500 dark:hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/30 rounded transition-colors"
                      title="Delete source"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
                <div className="flex items-center gap-1.5 mt-0.5 text-[11px] text-gray-500 dark:text-gray-400 overflow-hidden min-w-0">
                  <span className="shrink-0">{source.format?.toUpperCase() || 'FILE'}</span>
                  <span className="shrink-0 opacity-30">·</span>
                  <span className="shrink-0">{((source.char_count || source.characters || 0) / 1000).toFixed(1)}k</span>
                  {source.status !== 'completed' && (
                    <>
                      <span className="shrink-0 opacity-30">·</span>
                      <span className="shrink-0 text-yellow-600 dark:text-yellow-400">{source.status}</span>
                    </>
                  )}
                  {displayTags.length > 0 && (
                    <>
                      <span className="shrink-0 opacity-30">·</span>
                      {displayTags.map((tag: string) => (
                        <span
                          key={tag}
                          className={`px-1 rounded truncate max-w-[7rem] ${
                            tag === activeTagFilter
                              ? 'bg-blue-600 text-white dark:bg-blue-500 text-[10px]'
                              : 'bg-blue-50 text-blue-600 dark:bg-blue-900/30 dark:text-blue-300 text-[10px]'
                          }`}
                        >
                          {tag}
                        </span>
                      ))}
                      {remainingCount > 0 && (
                        <span className="text-[10px] text-gray-400 shrink-0">+{remainingCount}</span>
                      )}
                    </>
                  )}
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
