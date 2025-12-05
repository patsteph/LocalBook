import React, { useState } from 'react';
import { createPortal } from 'react-dom';
import { Citation as CitationType } from '../types';

interface CitationProps {
  citation: CitationType;
  onViewSource?: (sourceId: string, sourceName: string, searchTerm: string) => void;
}

export const Citation: React.FC<CitationProps> = ({ citation, onViewSource }) => {
  const [showFull, setShowFull] = useState(false);

  const confidenceBadge = {
    high: { bg: 'bg-green-100', text: 'text-green-800', icon: '🟢', label: 'High Confidence' },
    medium: { bg: 'bg-yellow-100', text: 'text-yellow-800', icon: '🟡', label: 'Medium Confidence' },
    low: { bg: 'bg-red-100', text: 'text-red-800', icon: '🔴', label: 'Low Confidence' }
  }[citation.confidence_level];

  return (
    <>
      <button
        onClick={() => setShowFull(!showFull)}
        className="text-blue-600 hover:text-blue-800 hover:underline font-medium mx-0.5"
        title={`${citation.filename}${citation.page ? `, p.${citation.page}` : ''} - ${confidenceBadge.label}`}
      >
        [{citation.number}]
      </button>

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
    </>
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
