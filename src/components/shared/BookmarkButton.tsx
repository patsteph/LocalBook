/**
 * BookmarkButton - Reusable component to save items to Findings
 */

import React, { useState } from 'react';
import { findingsService, Finding } from '../../services/findings';

interface BookmarkButtonProps {
  notebookId: string;
  type: Finding['type'];
  title: string;
  content: Record<string, unknown>;
  tags?: string[];
  className?: string;
  compact?: boolean;
}

export const BookmarkButton: React.FC<BookmarkButtonProps> = ({
  notebookId,
  type,
  title,
  content,
  tags,
  className = '',
  compact = false,
}) => {
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);

  const handleSave = async () => {
    if (saved || saving) return;
    
    setSaving(true);
    try {
      await findingsService.createFinding(notebookId, type, title, content, tags);
      setSaved(true);
      // Dispatch event to refresh Findings panel
      window.dispatchEvent(new CustomEvent('findingsUpdated'));
    } catch (err) {
      console.error('Failed to save to Findings:', err);
    } finally {
      setSaving(false);
    }
  };

  const baseClass = compact
    ? 'p-1.5 rounded transition-colors'
    : 'px-2 py-1 text-xs rounded transition-colors flex items-center';

  // Match chat box background - transparent with high contrast icon
  const colorClass = saved
    ? 'text-amber-500'
    : 'text-gray-600 dark:text-gray-300 hover:text-amber-500 dark:hover:text-amber-400';

  return (
    <button
      onClick={handleSave}
      disabled={saving || saved}
      className={`${baseClass} ${colorClass} ${className}`}
      title={saved ? 'Saved to Findings' : 'Save to Findings'}
    >
      {saving ? (
        <span className="animate-pulse">...</span>
      ) : (
        <>
          <svg 
            className={compact ? 'w-4 h-4' : 'w-3.5 h-3.5 inline mr-1'}
            fill={saved ? 'currentColor' : 'none'} 
            stroke="currentColor" 
            viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 5a2 2 0 012-2h10a2 2 0 012 2v16l-7-3.5L5 21V5z" />
          </svg>
          {!compact && (saved ? 'Saved' : 'Save')}
        </>
      )}
    </button>
  );
};
