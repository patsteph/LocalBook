/**
 * FindingsPanel.tsx - Display saved findings (visuals, answers, highlights, notes)
 * 
 * Part of the Canvas architecture - provides a centralized view of all
 * bookmarked/saved items from research.
 */

import React, { useState, useEffect, useCallback } from 'react';
import { findingsService, Finding, FindingsStats } from '../services/findings';
import { VisualCore } from './visual';

interface FindingsPanelProps {
  notebookId: string | null;
}

type FilterType = 'all' | 'visual' | 'answer' | 'highlight' | 'note';

const TYPE_ICONS: Record<string, string> = {
  visual: '🎨',
  answer: '💬',
  highlight: '✨',
  source: '📄',
  note: '📝',
};

const TYPE_COLORS: Record<string, string> = {
  visual: 'bg-green-100 dark:bg-green-900/30 border-green-300 dark:border-green-700',
  answer: 'bg-blue-100 dark:bg-blue-900/30 border-blue-300 dark:border-blue-700',
  highlight: 'bg-yellow-100 dark:bg-yellow-900/30 border-yellow-300 dark:border-yellow-700',
  source: 'bg-gray-100 dark:bg-gray-800 border-gray-300 dark:border-gray-600',
  note: 'bg-purple-100 dark:bg-purple-900/30 border-purple-300 dark:border-purple-700',
};

export const FindingsPanel: React.FC<FindingsPanelProps> = ({ notebookId }) => {
  const [findings, setFindings] = useState<Finding[]>([]);
  const [stats, setStats] = useState<FindingsStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<FilterType>('all');
  const [starredOnly, setStarredOnly] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const loadFindings = useCallback(async () => {
    if (!notebookId) return;
    
    setLoading(true);
    try {
      const [findingsResult, statsResult] = await Promise.all([
        findingsService.getFindings(notebookId, {
          type: filter === 'all' ? undefined : filter,
          starred: starredOnly || undefined,
        }),
        findingsService.getStats(notebookId),
      ]);
      
      setFindings(findingsResult.findings);
      setStats(statsResult);
    } catch (err) {
      console.error('Failed to load findings:', err);
    } finally {
      setLoading(false);
    }
  }, [notebookId, filter, starredOnly]);

  useEffect(() => {
    loadFindings();
  }, [loadFindings]);

  // Listen for findings updates from other components
  useEffect(() => {
    const handleFindingsUpdated = () => {
      loadFindings();
    };
    
    window.addEventListener('findingsUpdated', handleFindingsUpdated);
    return () => {
      window.removeEventListener('findingsUpdated', handleFindingsUpdated);
    };
  }, [loadFindings]);

  const handleToggleStar = async (finding: Finding) => {
    if (!notebookId) return;
    
    try {
      await findingsService.updateFinding(notebookId, finding.id, {
        starred: !finding.starred,
      });
      loadFindings();
    } catch (err) {
      console.error('Failed to toggle star:', err);
    }
  };

  const handleDelete = async (findingId: string) => {
    if (!notebookId) return;
    if (!confirm('Delete this finding?')) return;
    
    try {
      await findingsService.deleteFinding(notebookId, findingId);
      loadFindings();
    } catch (err) {
      console.error('Failed to delete finding:', err);
    }
  };

  const renderFindingContent = (finding: Finding) => {
    const content = finding.content as Record<string, unknown>;
    
    switch (finding.type) {
      case 'visual':
        return (
          <div className="mt-2">
            <VisualCore
              visual={{
                id: finding.id,
                type: (content.type as 'svg' | 'mermaid') || 'mermaid',
                code: (content.code as string) || '',
                title: finding.title,
              }}
              compact={expandedId !== finding.id}
            />
          </div>
        );
      
      case 'answer':
        return (
          <div className="mt-2 text-sm">
            <div className="text-gray-500 dark:text-gray-400 mb-1">
              Q: {(content.question as string) || ''}
            </div>
            <div className="text-gray-900 dark:text-gray-100 line-clamp-3">
              {(content.answer as string) || ''}
            </div>
          </div>
        );
      
      case 'highlight':
        return (
          <div className="mt-2">
            <blockquote className="border-l-2 border-yellow-400 pl-3 italic text-sm text-gray-700 dark:text-gray-300">
              "{(content.text as string) || ''}"
            </blockquote>
            <div className="text-xs text-gray-500 mt-1">
              — {(content.source_name as string) || 'Unknown source'}
            </div>
          </div>
        );
      
      case 'note':
        return (
          <div className="mt-2 text-sm text-gray-700 dark:text-gray-300 whitespace-pre-wrap">
            {(content.text as string) || ''}
          </div>
        );
      
      default:
        return (
          <div className="mt-2 text-sm text-gray-500">
            {JSON.stringify(content).substring(0, 200)}...
          </div>
        );
    }
  };

  if (!notebookId) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500 dark:text-gray-400">
        <p>Select a notebook to view findings</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header with filters */}
      <div className="flex-shrink-0 p-4 border-b border-gray-200 dark:border-gray-700">
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
            Findings
          </h2>
          {stats && (
            <span className="text-sm text-gray-500 dark:text-gray-400">
              {stats.total} saved
            </span>
          )}
        </div>
        
        {/* Filter tabs */}
        <div className="flex gap-1 flex-wrap">
          {(['all', 'visual', 'answer', 'highlight', 'note'] as FilterType[]).map((type) => (
            <button
              key={type}
              onClick={() => setFilter(type)}
              className={`px-2.5 py-1 text-xs rounded-full transition-colors ${
                filter === type
                  ? 'bg-blue-600 text-white'
                  : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600'
              }`}
            >
              {type === 'all' ? 'All' : `${TYPE_ICONS[type]} ${type}`}
              {stats && type !== 'all' && stats.by_type[type] && (
                <span className="ml-1 opacity-70">({stats.by_type[type]})</span>
              )}
            </button>
          ))}
          
          <button
            onClick={() => setStarredOnly(!starredOnly)}
            className={`px-2.5 py-1 text-xs rounded-full transition-colors ${
              starredOnly
                ? 'bg-yellow-500 text-white'
                : 'bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600'
            }`}
          >
            ⭐ Starred {stats && stats.starred > 0 && `(${stats.starred})`}
          </button>
        </div>
      </div>

      {/* Findings list */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {loading ? (
          <div className="flex items-center justify-center py-8">
            <div className="animate-spin h-6 w-6 border-2 border-blue-500 border-t-transparent rounded-full" />
          </div>
        ) : findings.length === 0 ? (
          <div className="text-center py-8 text-gray-500 dark:text-gray-400">
            <p className="text-2xl mb-2">📚</p>
            <p>No findings yet</p>
            <p className="text-sm mt-1">
              Save visuals, answers, and highlights from your research
            </p>
          </div>
        ) : (
          findings.map((finding) => (
            <div
              key={finding.id}
              className={`rounded-lg border p-3 ${TYPE_COLORS[finding.type]}`}
            >
              {/* Header */}
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <span className="text-lg flex-shrink-0">{TYPE_ICONS[finding.type]}</span>
                  <h3 className="font-medium text-gray-900 dark:text-white truncate">
                    {finding.title}
                  </h3>
                </div>
                <div className="flex items-center gap-1 flex-shrink-0">
                  <button
                    onClick={() => handleToggleStar(finding)}
                    className="p-1 hover:bg-black/10 dark:hover:bg-white/10 rounded"
                    title={finding.starred ? 'Unstar' : 'Star'}
                  >
                    {finding.starred ? '⭐' : '☆'}
                  </button>
                  <button
                    onClick={() => setExpandedId(expandedId === finding.id ? null : finding.id)}
                    className="p-1 hover:bg-black/10 dark:hover:bg-white/10 rounded text-sm"
                    title={expandedId === finding.id ? 'Collapse' : 'Expand'}
                  >
                    {expandedId === finding.id ? '▼' : '▶'}
                  </button>
                  <button
                    onClick={() => handleDelete(finding.id)}
                    className="p-1 hover:bg-red-200 dark:hover:bg-red-800/50 rounded text-red-600 dark:text-red-400"
                    title="Delete"
                  >
                    ✕
                  </button>
                </div>
              </div>

              {/* Content preview or expanded */}
              {(expandedId === finding.id || finding.type !== 'visual') && (
                renderFindingContent(finding)
              )}

              {/* Tags */}
              {finding.tags.length > 0 && (
                <div className="flex gap-1 mt-2 flex-wrap">
                  {finding.tags.map((tag) => (
                    <span
                      key={tag}
                      className="px-1.5 py-0.5 text-xs bg-gray-200 dark:bg-gray-600 text-gray-600 dark:text-gray-300 rounded"
                    >
                      #{tag}
                    </span>
                  ))}
                </div>
              )}

              {/* Timestamp */}
              <div className="text-xs text-gray-500 dark:text-gray-400 mt-2">
                {new Date(finding.created_at).toLocaleDateString()}
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
};

export default FindingsPanel;
