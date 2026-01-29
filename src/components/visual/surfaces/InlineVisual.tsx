/**
 * InlineVisual.tsx - Compact visual for Chat/Canvas
 * 
 * Renders a visual inline within chat messages.
 * Includes toolbar for save, edit, and export actions.
 */

import React, { useState, useCallback, useEffect } from 'react';
import { VisualCore, type VisualData } from '../core/VisualCore';
import { VisualToolbar, type ColorPaletteId } from '../core/VisualToolbar';
import { VisualSkeleton } from '../core/VisualSkeleton';
import type { PaletteId } from '../design/DesignSystem';

/**
 * VisualModal - Full-screen popout modal for viewing visuals at PPT slide size
 */
const VisualModal: React.FC<{
  visual: VisualData;
  palette: PaletteId;
  onClose: () => void;
}> = ({ visual, palette, onClose }) => {
  // Close on Escape key
  useEffect(() => {
    const handleEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleEsc);
    return () => window.removeEventListener('keydown', handleEsc);
  }, [onClose]);

  return (
    <div 
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm"
      onClick={onClose}
    >
      <div 
        className="relative bg-gray-900 rounded-xl border border-gray-700 shadow-2xl max-w-[90vw] max-h-[90vh] overflow-auto"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Modal header */}
        <div className="sticky top-0 flex items-center justify-between px-4 py-3 border-b border-gray-700 bg-gray-900/95 z-10">
          <div className="flex items-center gap-2">
            <span className="text-purple-400">
              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
              </svg>
            </span>
            <span className="text-lg font-medium text-gray-200">
              {visual.title || 'Visual'}
            </span>
            {visual.pattern && (
              <span className="text-sm text-gray-500 capitalize">
                ({visual.pattern.replace(/-/g, ' ')})
              </span>
            )}
          </div>
          <button
            onClick={onClose}
            className="p-2 rounded-lg hover:bg-gray-700 text-gray-400 hover:text-white transition-colors"
            title="Close (Esc)"
          >
            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
        
        {/* Visual at full size - PPT slide friendly */}
        <div className="p-6 min-w-[800px]">
          <VisualCore
            visual={visual}
            palette={palette}
            compact={false}
            showTitle={false}
          />
        </div>
        
        {/* Tagline at bottom */}
        {visual.tagline && (
          <div className="px-6 pb-4 text-center">
            <p className="text-sm text-gray-400 italic">{visual.tagline}</p>
          </div>
        )}
      </div>
    </div>
  );
};

export interface InlineVisualProps {
  visual: VisualData | null;
  alternatives?: VisualData[];  // Alternative visual options
  loading?: boolean;
  loadingMessage?: string;
  palette?: PaletteId;
  className?: string;
  onSaveToFindings?: () => void;   // Component already has visual in scope
  onOpenInStudio?: () => void;     // Component already has visual in scope
  onExport?: (format: 'png' | 'svg') => void;
  onRegenerate?: () => void;
  onRegenerateWithGuidance?: (guidance: string) => void;  // Regenerate with user refinement
  onRegenerateWithPalette?: (palette: ColorPaletteId) => void;  // Regenerate with new palette
  onSelectAlternative?: (visual: VisualData) => void;  // Swap primary with alternative
  onTaglineChange?: (tagline: string) => void;  // Update tagline
}

export const InlineVisual: React.FC<InlineVisualProps> = ({
  visual,
  alternatives = [],
  loading = false,
  loadingMessage = 'Creating visual...',
  palette = 'default',
  className = '',
  onSaveToFindings,
  onOpenInStudio,
  onExport,
  onRegenerate,
  onRegenerateWithGuidance,
  onRegenerateWithPalette,
  onSelectAlternative,
  onTaglineChange,
}) => {
  const [saved, setSaved] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const [showModal, setShowModal] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [guidance, setGuidance] = useState('');
  const [isEditingTagline, setIsEditingTagline] = useState(false);
  const [editedTagline, setEditedTagline] = useState(visual?.tagline || '');

  const handleSave = useCallback(() => {
    if (visual && onSaveToFindings) {
      onSaveToFindings();
      setSaved(true);
    }
  }, [visual, onSaveToFindings]);

  const handleOpenStudio = useCallback(() => {
    if (visual && onOpenInStudio) {
      onOpenInStudio();
    }
  }, [visual, onOpenInStudio]);

  const handleExport = useCallback((format: 'png' | 'svg') => {
    if (visual && onExport) {
      onExport(format);
    }
  }, [visual, onExport]);

  const handleEditClick = useCallback(() => {
    setIsEditing(true);
  }, []);

  const handleCancelEdit = useCallback(() => {
    setIsEditing(false);
    setGuidance('');
  }, []);

  const handleSubmitGuidance = useCallback(() => {
    if (guidance.trim() && onRegenerateWithGuidance) {
      onRegenerateWithGuidance(guidance.trim());
      setIsEditing(false);
      setGuidance('');
    }
  }, [guidance, onRegenerateWithGuidance]);

  // Loading state
  if (loading) {
    return (
      <div className={`inline-visual mt-3 ${className}`}>
        <VisualSkeleton compact message={loadingMessage || 'Creating visual...'} />
      </div>
    );
  }

  // No visual
  if (!visual) {
    return null;
  }

  return (
    <div className={`inline-visual mt-3 ${className}`}>
      <div className="relative bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
        {/* Header with title and toolbar */}
        <div className="flex items-center justify-between px-3 py-2 border-b border-gray-700 bg-gray-800/80">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-purple-400">
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
              </svg>
            </span>
            {visual.title && (
              <span className="text-sm font-medium text-gray-200 truncate">
                {visual.title}
              </span>
            )}
            {visual.pattern && (
              <span className="text-xs text-gray-500 capitalize hidden sm:inline">
                • {visual.pattern.replace(/-/g, ' ')}
              </span>
            )}
          </div>
          
          <div className="flex items-center gap-1">
            {/* Expand inline toggle */}
            <button
              onClick={() => setExpanded(!expanded)}
              className="p-1.5 rounded hover:bg-gray-700 text-gray-400 hover:text-white transition-colors"
              title={expanded ? 'Collapse' : 'Expand inline'}
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                {expanded ? (
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20 12H4" />
                ) : (
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 8V4m0 0h4M4 4l5 5m11-1V4m0 0h-4m4 0l-5 5M4 16v4m0 0h4m-4 0l5-5m11 5l-5-5m5 5v-4m0 4h-4" />
                )}
              </svg>
            </button>
            
            {/* Popout to full-size modal */}
            <button
              onClick={() => setShowModal(true)}
              className="p-1.5 rounded hover:bg-gray-700 text-gray-400 hover:text-white transition-colors"
              title="Open full size (PPT slide view)"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
              </svg>
            </button>
            
            {/* Action toolbar */}
            <VisualToolbar
              compact
              saved={saved}
              onSave={onSaveToFindings ? handleSave : undefined}
              onOpenStudio={onOpenInStudio ? handleOpenStudio : undefined}
              onExport={onExport ? handleExport : undefined}
              onRegenerate={onRegenerate}
              onPaletteChange={onRegenerateWithPalette}
            />
          </div>
        </div>
        
        {/* Visual content - scrollable container */}
        <div className={`transition-all duration-300 overflow-auto ${expanded ? 'max-h-[600px]' : 'max-h-72'}`}>
          <VisualCore
            visual={visual}
            palette={palette}
            compact={!expanded}
            showTitle={false}
          />
        </div>
        
        {/* Inline refinement UI */}
        {isEditing && (
          <div className="border-t border-gray-700 px-3 py-3 bg-gray-800/80">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-purple-400">✏️</span>
              <span className="text-sm text-gray-300">What should this visual emphasize?</span>
            </div>
            <textarea
              value={guidance}
              onChange={(e) => setGuidance(e.target.value)}
              placeholder="e.g., Show the gap between current AI capabilities and what's needed for safe deployment..."
              className="w-full px-3 py-2 bg-gray-900 border border-gray-600 rounded-lg text-sm text-gray-200 placeholder-gray-500 focus:border-purple-500 focus:outline-none resize-none"
              rows={2}
              autoFocus
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  handleSubmitGuidance();
                }
                if (e.key === 'Escape') {
                  handleCancelEdit();
                }
              }}
            />
            <div className="flex justify-end gap-2 mt-2">
              <button
                onClick={handleCancelEdit}
                className="px-3 py-1.5 text-xs text-gray-400 hover:text-gray-200 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleSubmitGuidance}
                disabled={!guidance.trim()}
                className="px-3 py-1.5 text-xs bg-purple-600 hover:bg-purple-500 disabled:bg-gray-700 disabled:text-gray-500 text-white rounded transition-colors"
              >
                Regenerate
              </button>
            </div>
          </div>
        )}
        
        {/* Editable tagline/summary below visual */}
        {(visual.tagline || onTaglineChange) && (
          <div className="border-t border-gray-700 px-3 py-2 bg-gray-800/60">
            {isEditingTagline ? (
              <div className="flex items-center gap-2">
                <input
                  type="text"
                  value={editedTagline}
                  onChange={(e) => setEditedTagline(e.target.value)}
                  className="flex-1 px-2 py-1 bg-gray-900 border border-gray-600 rounded text-xs text-gray-200 focus:border-purple-500 focus:outline-none"
                  placeholder="Add a summary or tagline..."
                  autoFocus
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      onTaglineChange?.(editedTagline);
                      setIsEditingTagline(false);
                    }
                    if (e.key === 'Escape') {
                      setEditedTagline(visual.tagline || '');
                      setIsEditingTagline(false);
                    }
                  }}
                />
                <button
                  onClick={() => {
                    onTaglineChange?.(editedTagline);
                    setIsEditingTagline(false);
                  }}
                  className="px-2 py-1 text-xs bg-purple-600 hover:bg-purple-500 text-white rounded transition-colors"
                >
                  Save
                </button>
                <button
                  onClick={() => {
                    setEditedTagline(visual.tagline || '');
                    setIsEditingTagline(false);
                  }}
                  className="px-2 py-1 text-xs text-gray-400 hover:text-gray-200 transition-colors"
                >
                  ✕
                </button>
              </div>
            ) : (
              <button
                onClick={() => {
                  setEditedTagline(visual.tagline || '');
                  setIsEditingTagline(true);
                }}
                className="group flex items-center gap-1.5 text-xs text-gray-400 hover:text-gray-200 transition-colors w-full text-left"
              >
                <span className="opacity-0 group-hover:opacity-100 transition-opacity">✏️</span>
                <span className="italic">{visual.tagline || 'Add a summary tagline...'}</span>
              </button>
            )}
          </div>
        )}
        
        {/* Edit button when not editing - separate from toolbar for visibility */}
        {!isEditing && onRegenerateWithGuidance && (
          <div className="border-t border-gray-700 px-3 py-2 bg-gray-800/50">
            <button
              onClick={handleEditClick}
              className="flex items-center gap-1.5 text-xs text-gray-400 hover:text-purple-400 transition-colors"
            >
              <span>✏️</span>
              <span>Refine this visual...</span>
            </button>
          </div>
        )}
        
        {/* Alternative visuals - clickable thumbnails */}
        {alternatives.length > 0 && onSelectAlternative && (
          <div className="border-t border-gray-700 px-3 py-2 bg-gray-800/50">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-xs text-gray-400">Other options:</span>
            </div>
            <div className="flex gap-2 overflow-x-auto pb-1">
              {alternatives.map((alt) => (
                <button
                  key={alt.id}
                  onClick={() => onSelectAlternative(alt)}
                  className="flex-shrink-0 group relative bg-gray-900 rounded border border-gray-700 hover:border-purple-500 transition-colors overflow-hidden"
                  title={`Switch to: ${alt.title || alt.pattern || 'Alternative'}`}
                >
                  <div className="w-24 h-16 overflow-hidden">
                    <VisualCore
                      visual={alt}
                      palette={palette}
                      compact
                      showTitle={false}
                    />
                  </div>
                  <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-gray-900 to-transparent px-1 py-0.5">
                    <span className="text-[10px] text-gray-300 truncate block capitalize">
                      {alt.pattern?.replace(/-/g, ' ') || 'Alternative'}
                    </span>
                  </div>
                </button>
              ))}
            </div>
          </div>
        )}
      </div>
      
      {/* Full-size popout modal */}
      {showModal && visual && (
        <VisualModal
          visual={visual}
          palette={palette}
          onClose={() => setShowModal(false)}
        />
      )}
    </div>
  );
};

export default InlineVisual;
