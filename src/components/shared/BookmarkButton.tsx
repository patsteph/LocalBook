/**
 * BookmarkButton — "Save as Note" affordance on chat answers and saved items.
 *
 * Renamed from "Save to Findings" (Tier 5, 2026-06-01). Findings used to
 * be a write-once bookmark system; Notes are editable, full-formatting,
 * and live as Sources. Same button, better destination.
 *
 * The 'type' prop survives only for compatibility with the existing call
 * sites — the destination is always a Note now. Content gets converted
 * to markdown so the user keeps formatting + citations.
 */

import React, { useState } from 'react';
import { localFetch, API_BASE_URL } from '../../services/api';

interface BookmarkButtonProps {
  notebookId: string;
  type: 'visual' | 'answer' | 'highlight' | 'source' | 'note';
  title: string;
  content: Record<string, unknown>;
  tags?: string[];
  className?: string;
  compact?: boolean;
}

function contentToMarkdown(
  type: BookmarkButtonProps['type'],
  title: string,
  content: Record<string, unknown>,
): string {
  if (type === 'answer') {
    const question = (content.question as string) || '';
    const answer = (content.answer as string) || '';
    const citations = (content.citations as unknown[]) || [];
    const parts: string[] = [];
    if (question) parts.push(`**Q:** ${question}\n`);
    if (answer) parts.push(answer);
    if (citations.length) {
      parts.push('\n---\n**Citations:**');
      citations.forEach((c, i) => {
        if (c && typeof c === 'object') {
          const cc = c as Record<string, unknown>;
          const src = (cc.source as string) || (cc.filename as string) || 'Unknown';
          const snip = (cc.snippet as string) || (cc.text as string) || '';
          parts.push(`- [${i + 1}] **${src}** — ${snip.slice(0, 200)}`);
        } else {
          parts.push(`- [${i + 1}] ${String(c).slice(0, 200)}`);
        }
      });
    }
    return parts.join('\n').trim();
  }

  if (type === 'visual') {
    const svg = (content.svg as string) || (content.svg_markup as string) || '';
    return svg ? `# ${title}\n\n\`\`\`svg\n${svg.slice(0, 8000)}\n\`\`\`` : `# ${title}`;
  }

  if (type === 'highlight') {
    const text = (content.text as string) || (content.highlighted_text as string) || '';
    const src = (content.source as string) || (content.filename as string) || '';
    return src ? `> ${text}\n\n— ${src}` : `> ${text}`;
  }

  // source / note / unknown
  try {
    return '```json\n' + JSON.stringify(content, null, 2).slice(0, 4000) + '\n```';
  } catch {
    return String(content).slice(0, 4000);
  }
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
      const body = contentToMarkdown(type, title, content);
      const noteTitle = title || `Saved ${type}`;
      const noteTags = [...(tags || []), 'saved-from-chat'];
      const sourceType = type === 'answer' ? 'chat_answer' : 'typed';

      const response = await localFetch(`${API_BASE_URL}/canvas-notes`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          notebook_id: notebookId,
          title: noteTitle,
          content_markdown: body,
          source_type: sourceType,
          note_type: 'note',
          tags: noteTags,
        }),
      });

      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setSaved(true);
      // Notify Notes panel + Sources panel to refresh.
      window.dispatchEvent(new CustomEvent('notesUpdated'));
      window.dispatchEvent(new CustomEvent('sourcesUpdated'));
    } catch (err) {
      console.error('Failed to save as Note:', err);
    } finally {
      setSaving(false);
    }
  };

  const baseClass = compact
    ? 'p-1.5 rounded-lg transition-colors'
    : 'px-2 py-1 text-xs rounded-lg transition-colors flex items-center';

  const colorClass = saved
    ? 'text-amber-500'
    : 'text-gray-600 dark:text-gray-300 hover:text-amber-500 dark:hover:text-amber-400';

  return (
    <button
      onClick={handleSave}
      disabled={saving || saved}
      className={`${baseClass} ${colorClass} ${className}`}
      title={saved ? 'Saved as Note' : 'Save as Note'}
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
          {!compact && (saved ? 'Saved as Note' : 'Save as Note')}
        </>
      )}
    </button>
  );
};
