import React, { useState, useEffect, useCallback, useRef } from 'react';
import { sourceViewerService, SourceContent } from '../services/sourceViewer';
import { sourceService } from '../services/sources';
import { highlightService } from '../services/highlights';
import { Highlight } from '../types';
import { LoadingSpinner } from './shared/LoadingSpinner';
import { ErrorMessage } from './shared/ErrorMessage';

interface SourceNotesViewerProps {
  notebookId: string;
  sourceId: string;
  sourceName: string;
  onClose: () => void;
  initialSearchTerm?: string;
}

export const SourceNotesViewer: React.FC<SourceNotesViewerProps> = ({
  notebookId,
  sourceId,
  sourceName,
  onClose,
  initialSearchTerm = '',
}) => {
  const [content, setContent] = useState<SourceContent | null>(null);
  const [notes, setNotes] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [searchTerm, setSearchTerm] = useState(initialSearchTerm);
  const [lastSaved, setLastSaved] = useState<Date | null>(null);
  const [currentMatchIndex, setCurrentMatchIndex] = useState(0);
  const [totalMatches, setTotalMatches] = useState(0);
  const contentRef = useRef<HTMLDivElement>(null);

  // Highlighting state
  const [highlights, setHighlights] = useState<Highlight[]>([]);
  const [showAnnotationModal, setShowAnnotationModal] = useState(false);
  const [pendingHighlight, setPendingHighlight] = useState<{
    start: number;
    end: number;
    text: string;
  } | null>(null);
  const [annotationText, setAnnotationText] = useState('');
  const [selectedColor, setSelectedColor] = useState('yellow');

  // Tagging state (v0.6.0)
  const [tags, setTags] = useState<string[]>([]);
  const [newTag, setNewTag] = useState('');
  const [showTagInput, setShowTagInput] = useState(false);

  useEffect(() => {
    loadData();
  }, [notebookId, sourceId]);

  const loadData = async () => {
    setLoading(true);
    setError(null);

    try {
      const [contentData, notesData, highlightsData, tagsData] = await Promise.all([
        sourceViewerService.getContent(notebookId, sourceId),
        sourceViewerService.getNotes(notebookId, sourceId),
        highlightService.list(notebookId, sourceId),
        sourceService.getTags(notebookId, sourceId),
      ]);

      setContent(contentData);
      setNotes(notesData);
      setHighlights(highlightsData);
      setTags(tagsData);
    } catch (err: any) {
      console.error('Failed to load source data:', err);
      setError(err.response?.data?.detail || 'Failed to load source data');
    } finally {
      setLoading(false);
    }
  };

  // Auto-save notes with debounce
  const saveNotes = useCallback(async (notesToSave: string) => {
    setSaving(true);
    try {
      await sourceViewerService.saveNotes(notebookId, sourceId, notesToSave);
      setLastSaved(new Date());
    } catch (err) {
      console.error('Failed to save notes:', err);
    } finally {
      setSaving(false);
    }
  }, [notebookId, sourceId]);

  useEffect(() => {
    const timer = setTimeout(() => {
      if (notes !== undefined) {
        saveNotes(notes);
      }
    }, 1000); // Auto-save after 1 second of inactivity

    return () => clearTimeout(timer);
  }, [notes, saveNotes]);

  // Calculate total matches when search term or content changes
  useEffect(() => {
    if (!searchTerm.trim() || !content) {
      setTotalMatches(0);
      setCurrentMatchIndex(0);
      return;
    }

    const regex = new RegExp(`(${searchTerm.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
    const matches = content.content.match(regex);
    setTotalMatches(matches ? matches.length : 0);
    setCurrentMatchIndex(0);
  }, [searchTerm, content]);

  const highlightSearchTerm = (text: string) => {
    if (!searchTerm.trim()) {
      return text;
    }

    const regex = new RegExp(`(${searchTerm.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi');
    let matchIndex = 0;
    return text.replace(regex, (match) => {
      const index = matchIndex++;
      const isCurrent = index === currentMatchIndex;
      return `<mark class="${isCurrent ? 'bg-orange-300' : 'bg-yellow-200'} px-1 rounded" data-match-index="${index}">${match}</mark>`;
    });
  };

  // Scroll to current match
  useEffect(() => {
    if (totalMatches > 0 && contentRef.current) {
      const marks = contentRef.current.querySelectorAll('mark');
      if (marks[currentMatchIndex]) {
        marks[currentMatchIndex].scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }
  }, [currentMatchIndex, totalMatches]);

  const navigateMatch = (direction: 'next' | 'prev') => {
    if (totalMatches === 0) return;

    if (direction === 'next') {
      setCurrentMatchIndex((prev) => (prev + 1) % totalMatches);
    } else {
      setCurrentMatchIndex((prev) => (prev - 1 + totalMatches) % totalMatches);
    }
  };

  // Handle text selection for highlighting
  const handleTextSelection = () => {
    const selection = window.getSelection();
    if (!selection || !content || selection.isCollapsed) return;

    const selectedText = selection.toString().trim();
    if (!selectedText || selectedText.length === 0) return;

    // Get the selection range relative to the content
    const range = selection.getRangeAt(0);
    const preSelectionRange = range.cloneRange();
    preSelectionRange.selectNodeContents(contentRef.current!);
    preSelectionRange.setEnd(range.startContainer, range.startOffset);
    const start = preSelectionRange.toString().length;
    const end = start + selectedText.length;

    // Show annotation modal
    setPendingHighlight({ start, end, text: selectedText });
    setShowAnnotationModal(true);
    setAnnotationText('');

    // Clear selection
    selection.removeAllRanges();
  };

  const handleSaveHighlight = async () => {
    if (!pendingHighlight) return;

    try {
      const newHighlight = await highlightService.create({
        notebook_id: notebookId,
        source_id: sourceId,
        start_offset: pendingHighlight.start,
        end_offset: pendingHighlight.end,
        highlighted_text: pendingHighlight.text,
        color: selectedColor,
        annotation: annotationText,
      });

      setHighlights([...highlights, newHighlight]);
      setShowAnnotationModal(false);
      setPendingHighlight(null);
      setAnnotationText('');
    } catch (err) {
      console.error('Failed to save highlight:', err);
      setError('Failed to save highlight');
    }
  };

  const handleDeleteHighlight = async (highlightId: string) => {
    try {
      await highlightService.delete(highlightId);
      setHighlights(highlights.filter(h => h.highlight_id !== highlightId));
    } catch (err) {
      console.error('Failed to delete highlight:', err);
      setError('Failed to delete highlight');
    }
  };

  // Tag management handlers (v0.6.0)
  const handleAddTag = async () => {
    if (!newTag.trim()) return;
    try {
      const updatedTags = await sourceService.addTag(notebookId, sourceId, newTag.trim());
      setTags(updatedTags);
      setNewTag('');
      setShowTagInput(false);
    } catch (err) {
      console.error('Failed to add tag:', err);
      setError('Failed to add tag');
    }
  };

  const handleRemoveTag = async (tag: string) => {
    try {
      const updatedTags = await sourceService.removeTag(notebookId, sourceId, tag);
      setTags(updatedTags);
    } catch (err) {
      console.error('Failed to remove tag:', err);
      setError('Failed to remove tag');
    }
  };

  // Render content with highlights applied
  const renderContentWithHighlights = (text: string) => {
    if (!text) return '';

    // First apply search highlighting if active
    let processedText = searchTerm.trim() ? highlightSearchTerm(text) : text;

    // Sort highlights by start position to apply them correctly
    const sortedHighlights = [...highlights].sort((a, b) => a.start_offset - b.start_offset);

    // Apply user highlights
    let offset = 0;
    let result = '';

    sortedHighlights.forEach((highlight) => {
      // Add text before highlight
      result += processedText.slice(offset, highlight.start_offset);

      // Add highlighted text with wrapper
      const colorClass = {
        yellow: 'bg-yellow-200 border-b-2 border-yellow-400',
        green: 'bg-green-200 border-b-2 border-green-400',
        blue: 'bg-blue-200 border-b-2 border-blue-400',
        pink: 'bg-pink-200 border-b-2 border-pink-400',
      }[highlight.color] || 'bg-yellow-200 border-b-2 border-yellow-400';

      const annotationAttr = highlight.annotation ? `title="${highlight.annotation.replace(/"/g, '&quot;')}"` : '';

      result += `<span class="${colorClass} cursor-pointer relative group px-1" data-highlight-id="${highlight.highlight_id}" ${annotationAttr}>`;
      result += processedText.slice(highlight.start_offset, highlight.end_offset);

      // Add annotation indicator and tooltip
      if (highlight.annotation) {
        result += `<span class="ml-1 text-xs text-gray-600">💬</span>`;
      }

      // Add delete button (appears on hover)
      result += `<button class="hidden group-hover:inline-block absolute -top-6 right-0 text-xs bg-red-500 text-white px-2 py-1 rounded shadow" onclick="window.deleteHighlight('${highlight.highlight_id}')">×</button>`;

      result += '</span>';

      offset = highlight.end_offset;
    });

    // Add remaining text
    result += processedText.slice(offset);

    return result;
  };

  // Expose delete function to window for onclick handler
  useEffect(() => {
    (window as any).deleteHighlight = handleDeleteHighlight;
    return () => {
      delete (window as any).deleteHighlight;
    };
  }, [highlights]);

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-2xl w-full max-w-6xl h-[90vh] flex flex-col">
        {/* Header */}
        <div className="border-b p-4 flex justify-between items-center">
          <div className="flex-1">
            <h2 className="text-xl font-bold text-gray-900">{sourceName}</h2>
            <p className="text-sm text-gray-600 dark:text-gray-400">
              {content?.format} • {sourceId}
            </p>
            {/* Tags section (v0.6.0) */}
            <div className="flex flex-wrap items-center gap-1 mt-2">
              {tags.map((tag) => (
                <span
                  key={tag}
                  className="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300 rounded group"
                >
                  {tag}
                  <button
                    onClick={() => handleRemoveTag(tag)}
                    className="text-blue-500 hover:text-red-500 opacity-50 group-hover:opacity-100"
                    title="Remove tag"
                  >
                    ×
                  </button>
                </span>
              ))}
              {showTagInput ? (
                <div className="inline-flex items-center gap-1">
                  <input
                    type="text"
                    value={newTag}
                    onChange={(e) => setNewTag(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') handleAddTag();
                      if (e.key === 'Escape') {
                        setShowTagInput(false);
                        setNewTag('');
                      }
                    }}
                    placeholder="Add tag..."
                    className="px-2 py-0.5 text-xs border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-blue-500 w-24"
                    autoFocus
                  />
                  <button
                    onClick={handleAddTag}
                    className="text-xs text-green-600 hover:text-green-800"
                  >
                    ✓
                  </button>
                  <button
                    onClick={() => {
                      setShowTagInput(false);
                      setNewTag('');
                    }}
                    className="text-xs text-gray-400 hover:text-gray-600"
                  >
                    ×
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => setShowTagInput(true)}
                  className="px-2 py-0.5 text-xs text-gray-500 hover:text-blue-600 hover:bg-blue-50 rounded border border-dashed border-gray-300 hover:border-blue-400"
                >
                  + Tag
                </button>
              )}
            </div>
          </div>
          <div className="flex items-center gap-4">
            {saving && <span className="text-sm text-gray-500 dark:text-gray-400">Saving...</span>}
            {lastSaved && !saving && (
              <span className="text-sm text-gray-500 dark:text-gray-400">
                Saved {lastSaved.toLocaleTimeString()}
              </span>
            )}
            <button
              onClick={onClose}
              className="text-gray-400 hover:text-gray-600 text-3xl leading-none"
            >
              ×
            </button>
          </div>
        </div>

        {error && (
          <div className="p-4">
            <ErrorMessage message={error} onDismiss={() => setError(null)} />
          </div>
        )}

        {loading ? (
          <div className="flex-1 flex items-center justify-center">
            <LoadingSpinner />
          </div>
        ) : (
          <div className="flex-1 flex flex-col overflow-hidden">
            {/* Source Content Section */}
            <div className="flex-1 border-b overflow-hidden flex flex-col">
              <div className="p-4 border-b bg-gray-50">
                <div className="flex items-center gap-2">
                  <h3 className="text-sm font-semibold text-gray-700 dark:text-gray-300">Source Content</h3>
                  <input
                    type="text"
                    placeholder="Search in document..."
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                    className="flex-1 px-3 py-1 text-sm border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500"
                  />
                  {totalMatches > 0 && (
                    <>
                      <span className="text-xs text-gray-600 whitespace-nowrap">
                        {currentMatchIndex + 1} of {totalMatches}
                      </span>
                      <div className="flex gap-1">
                        <button
                          onClick={() => navigateMatch('prev')}
                          className="px-2 py-1 text-xs bg-gray-200 hover:bg-gray-300 rounded transition-colors"
                          title="Previous match"
                        >
                          ↑
                        </button>
                        <button
                          onClick={() => navigateMatch('next')}
                          className="px-2 py-1 text-xs bg-gray-200 hover:bg-gray-300 rounded transition-colors"
                          title="Next match"
                        >
                          ↓
                        </button>
                      </div>
                    </>
                  )}
                </div>
              </div>
              <div className="flex-1 overflow-y-auto p-4 bg-white">
                <div
                  ref={contentRef}
                  className="prose max-w-none text-sm leading-relaxed whitespace-pre-wrap font-mono select-text"
                  onMouseUp={handleTextSelection}
                  dangerouslySetInnerHTML={{
                    __html: content ? renderContentWithHighlights(content.content) : '',
                  }}
                />
                <div className="mt-4 text-xs text-gray-500">
                  💡 Tip: Select any text to highlight it
                </div>
              </div>
            </div>

            {/* Notes Section */}
            <div className="h-1/3 flex flex-col overflow-hidden">
              <div className="p-4 border-b bg-gray-50">
                <h3 className="text-sm font-semibold text-gray-700">📝 Notes</h3>
                <p className="text-xs text-gray-500 mt-1">
                  Your notes are automatically saved as you type
                </p>
              </div>
              <textarea
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Take notes about this source... (Markdown supported)"
                className="flex-1 p-4 text-sm resize-none border-none focus:outline-none focus:ring-2 focus:ring-blue-500"
              />
            </div>
          </div>
        )}

        {/* Footer */}
        <div className="border-t p-4 bg-gray-50 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors"
          >
            Close
          </button>
        </div>
      </div>

      {/* Annotation Modal */}
      {showAnnotationModal && pendingHighlight && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-[60]">
          <div className="bg-white rounded-lg shadow-xl p-6 max-w-md w-full mx-4">
            <h3 className="text-lg font-semibold mb-4">Add Highlight</h3>

            <div className="mb-4">
              <p className="text-sm text-gray-700 mb-2">Selected text:</p>
              <div className="p-3 bg-gray-100 rounded text-sm italic border-l-4 border-gray-400">
                "{pendingHighlight.text.substring(0, 150)}
                {pendingHighlight.text.length > 150 ? '...' : ''}"
              </div>
            </div>

            <div className="mb-4">
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Highlight Color
              </label>
              <div className="flex gap-2">
                {['yellow', 'green', 'blue', 'pink'].map((color) => (
                  <button
                    key={color}
                    onClick={() => setSelectedColor(color)}
                    className={`w-10 h-10 rounded border-2 transition-all ${
                      selectedColor === color ? 'border-gray-800 scale-110' : 'border-gray-300'
                    } ${
                      color === 'yellow' ? 'bg-yellow-200' :
                      color === 'green' ? 'bg-green-200' :
                      color === 'blue' ? 'bg-blue-200' :
                      'bg-pink-200'
                    }`}
                    title={color}
                  />
                ))}
              </div>
            </div>

            <div className="mb-4">
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Annotation (optional)
              </label>
              <textarea
                value={annotationText}
                onChange={(e) => setAnnotationText(e.target.value)}
                placeholder="Add a note about this highlight..."
                className="w-full px-3 py-2 border border-gray-300 rounded focus:outline-none focus:ring-2 focus:ring-blue-500 text-sm"
                rows={3}
              />
            </div>

            <div className="flex justify-end gap-2">
              <button
                onClick={() => {
                  setShowAnnotationModal(false);
                  setPendingHighlight(null);
                }}
                className="px-4 py-2 text-gray-700 bg-gray-200 rounded hover:bg-gray-300 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleSaveHighlight}
                className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 transition-colors"
              >
                Save Highlight
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
