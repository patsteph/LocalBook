import React, { useState, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { Citation as CitationType } from '../types';

interface CitationProps {
  citation: CitationType;
  onViewSource?: (sourceId: string, sourceName: string, searchTerm: string) => void;
}

export const Citation: React.FC<CitationProps> = ({ citation, onViewSource }) => {
  const [showFull, setShowFull] = useState(false);
  const [showHover, setShowHover] = useState(false);
  const [hoverPos, setHoverPos] = useState<{ top: number; left: number } | null>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);

  const confidenceBadge = {
    high: { bg: 'bg-green-100 dark:bg-green-900/30', text: 'text-green-800 dark:text-green-300', icon: '🟢', label: 'High Confidence', border: 'border-green-300 dark:border-green-700' },
    medium: { bg: 'bg-yellow-100 dark:bg-yellow-900/30', text: 'text-yellow-800 dark:text-yellow-300', icon: '🟡', label: 'Medium Confidence', border: 'border-yellow-300 dark:border-yellow-700' },
    low: { bg: 'bg-red-100 dark:bg-red-900/30', text: 'text-red-800 dark:text-red-300', icon: '🔴', label: 'Low Confidence', border: 'border-red-300 dark:border-red-700' }
  }[citation.confidence_level];

  const handleMouseEnter = useCallback(() => {
    if (buttonRef.current) {
      const rect = buttonRef.current.getBoundingClientRect();
      setHoverPos({ top: rect.top, left: rect.left + rect.width / 2 });
    }
    setShowHover(true);
  }, []);

  return (
    <span className="inline-block">
      <button
        ref={buttonRef}
        onClick={() => setShowFull(!showFull)}
        onMouseEnter={handleMouseEnter}
        onMouseLeave={() => setShowHover(false)}
        className="text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300 hover:underline font-medium mx-0.5"
      >
        [{citation.number}]
      </button>

      {/* Hover Preview Tooltip — portalled to body so it's never clipped */}
      {showHover && !showFull && hoverPos && createPortal(
        <div
          className="fixed z-50 w-72 pointer-events-none"
          style={{ top: hoverPos.top - 8, left: hoverPos.left, transform: 'translate(-50%, -100%)' }}
        >
          <div className={`bg-white dark:bg-gray-800 rounded-lg shadow-lg border ${confidenceBadge.border} p-3`}>
            <div className="flex items-center gap-2 mb-2">
              <span className={`px-1.5 py-0.5 text-xs rounded ${confidenceBadge.bg} ${confidenceBadge.text}`}>
                {confidenceBadge.icon} {Math.round(citation.confidence * 100)}%
              </span>
              <span className="text-xs text-gray-500 dark:text-gray-400 truncate flex-1">{citation.filename}</span>
            </div>
            <p className="text-xs text-gray-700 dark:text-gray-300 line-clamp-4">{citation.snippet}</p>
            <p className="text-xs text-gray-400 mt-1 italic">Click to view full citation</p>
          </div>
          <div className="absolute left-1/2 -translate-x-1/2 -bottom-1 w-2 h-2 bg-white dark:bg-gray-800 border-r border-b border-gray-200 dark:border-gray-700 transform rotate-45"></div>
        </div>,
        document.body
      )}

      {showFull && createPortal(
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-lg shadow-xl max-w-2xl w-full max-h-[80vh] overflow-y-auto">
            {/* Header */}
            <div className="sticky top-0 bg-white border-b p-4 flex justify-between items-start">
              <div className="flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <h3 className="font-semibold text-lg">Citation [{citation.number}]</h3>
                  <span className={`px-2 py-0.5 text-xs rounded ${confidenceBadge.bg} ${confidenceBadge.text}`}>
                    {confidenceBadge.icon} {confidenceBadge.label} ({citation.confidence}%)
                  </span>
                </div>
                <p className="text-sm text-gray-600 dark:text-gray-400">
                  {citation.filename}
                  {citation.page && <span> · Page {citation.page}</span>}
                </p>
              </div>
              <button
                onClick={() => setShowFull(false)}
                className="text-gray-400 hover:text-gray-600 text-2xl leading-none"
              >
                ×
              </button>
            </div>

            {/* Content */}
            <div className="p-4">
              <h4 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Source Text:</h4>
              <div className="bg-gray-50 p-4 rounded border border-gray-200">
                <p className="text-sm text-gray-800 whitespace-pre-wrap leading-relaxed">
                  {citation.text}
                </p>
              </div>
            </div>

            {/* Footer */}
            <div className="border-t p-4 bg-gray-50 flex gap-2">
              {onViewSource && (
                <button
                  onClick={() => {
                    setShowFull(false);
                    // Extract a meaningful search term from the citation text (first 50 chars)
                    const searchTerm = citation.text.substring(0, 50).trim();
                    onViewSource(citation.source_id, citation.filename, searchTerm);
                  }}
                  className="flex-1 px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 transition-colors text-sm"
                >
                  📄 View Full Source
                </button>
              )}
              <button
                onClick={() => setShowFull(false)}
                className="flex-1 px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors text-sm"
              >
                Close
              </button>
            </div>
          </div>
        </div>,
        document.body
      )}
    </span>
  );
};

interface CitationListProps {
  citations: CitationType[];
  onViewSource?: (sourceId: string, sourceName: string, searchTerm: string) => void;
}

export const CitationList: React.FC<CitationListProps> = ({ citations, onViewSource }) => {
  if (citations.length === 0) return null;

  // Group citations by source
  const citationsBySource = citations.reduce((acc, citation) => {
    if (!acc[citation.filename]) {
      acc[citation.filename] = [];
    }
    acc[citation.filename].push(citation);
    return acc;
  }, {} as Record<string, CitationType[]>);

  const confidenceIcon = (level: string) => {
    switch (level) {
      case 'high': return '🟢';
      case 'medium': return '🟡';
      case 'low': return '🔴';
      default: return '⚪';
    }
  };

  return (
    <details className="mt-3 pt-3 border-t border-gray-200 dark:border-gray-600 group">
      <summary className="text-sm cursor-pointer font-medium flex items-center gap-2 text-blue-600 hover:text-blue-800 dark:text-blue-400 dark:hover:text-blue-300">
        <span className="transition-transform group-open:rotate-90">▶</span>
        <span className="text-base">📚</span>
        <span>{citations.length} {citations.length === 1 ? 'Source' : 'Sources'} Referenced</span>
        <span className="text-xs text-gray-500 dark:text-gray-400 font-normal">(click to expand)</span>
      </summary>
      <div className="mt-3 space-y-3">
        {Object.entries(citationsBySource).map(([filename, cites]) => (
          <div key={filename} className="bg-gray-50 dark:bg-gray-800 rounded-lg p-3">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-base">📄</span>
              <p className="font-medium text-gray-800 dark:text-gray-200 text-sm">{filename}</p>
            </div>
            <div className="space-y-2">
              {cites.map((cite) => (
                <div key={cite.number} className="flex items-start gap-2 pl-2 border-l-2 border-blue-300 dark:border-blue-600">
                  <div className="flex items-center gap-1.5 shrink-0">
                    <Citation citation={cite} onViewSource={onViewSource} />
                    <span className="text-xs flex items-center gap-0.5" title={`${cite.confidence_level} confidence: ${Math.round(cite.confidence * 100)}%`}>
                      {confidenceIcon(cite.confidence_level)}
                      <span className="text-gray-500 dark:text-gray-400">{Math.round(cite.confidence * 100)}%</span>
                    </span>
                    {cite.page && (
                      <span className="text-xs text-gray-500 dark:text-gray-400">· p.{cite.page}</span>
                    )}
                  </div>
                  <p className="text-gray-600 dark:text-gray-400 text-xs leading-relaxed line-clamp-2">
                    {cite.snippet}
                  </p>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </details>
  );
};
