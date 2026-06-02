/**
 * CommandPalette — ⌘K-style fuzzy command picker.
 *
 * 2026-06-02 (MVP): main-view jump only. Future entries (open notebook,
 * save as note, generate X) can append to the `commands` list without
 * any structural change.
 *
 * Design: centered modal at the top third of the screen, like Raycast /
 * Linear / Notion. Type to filter. Arrow keys to navigate. Enter to fire.
 * ESC to close. Mouse works too.
 */
import React, { useState, useEffect, useRef, useMemo } from 'react';
import { Search, X } from 'lucide-react';
import { PanelView, VIEW_LABELS } from './canvas/types';

interface Command {
  id: string;
  label: string;
  hint?: string;        // optional secondary line ("View")
  shortcut?: string;    // optional keyboard shortcut display
  run: () => void;
}

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
  onSwitchView: (view: PanelView) => void;
}

const VIEW_SHORTCUT: Record<PanelView, string | null> = {
  'chat': '⌘1',
  'library': '⌘2',
  'constellation': '⌘3',
  'timeline': '⌘4',
  'curator': '⌘5',
  'settings': null,
  'llm-selector': null,
  'embedding-selector': null,
  'content-viewer': null,
  'quiz-viewer': null,
  'visual-viewer': null,
};

export const CommandPalette: React.FC<CommandPaletteProps> = ({ open, onClose, onSwitchView }) => {
  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  // Build the command list. Right now: just the five main views.
  // Future: append "open notebook X", "save as note", etc.
  const commands: Command[] = useMemo(() => {
    const mainViews: PanelView[] = ['chat', 'library', 'constellation', 'timeline', 'curator'];
    return mainViews.map(v => ({
      id: `view:${v}`,
      label: VIEW_LABELS[v],
      hint: 'View',
      shortcut: VIEW_SHORTCUT[v] || undefined,
      run: () => { onSwitchView(v); onClose(); },
    }));
  }, [onSwitchView, onClose]);

  // Fuzzy filter: substring match on label (case-insensitive). Good enough
  // for 5-20 commands; swap for a real fuzzy match if the list grows.
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return commands;
    return commands.filter(c => c.label.toLowerCase().includes(q) || (c.hint || '').toLowerCase().includes(q));
  }, [commands, query]);

  // Reset state on open; focus the input.
  useEffect(() => {
    if (open) {
      setQuery('');
      setSelectedIndex(0);
      // Allow the modal to mount before focusing.
      const t = setTimeout(() => inputRef.current?.focus(), 0);
      return () => clearTimeout(t);
    }
  }, [open]);

  // Clamp the selected index whenever the filtered list shrinks.
  useEffect(() => {
    if (selectedIndex >= filtered.length) {
      setSelectedIndex(Math.max(0, filtered.length - 1));
    }
  }, [filtered.length, selectedIndex]);

  if (!open) return null;

  const handleKey = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
      return;
    }
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelectedIndex(i => Math.min(i + 1, filtered.length - 1));
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelectedIndex(i => Math.max(i - 1, 0));
      return;
    }
    if (e.key === 'Enter') {
      e.preventDefault();
      const cmd = filtered[selectedIndex];
      if (cmd) cmd.run();
    }
  };

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-50 bg-black/30 dark:bg-black/50 transition-opacity"
        onClick={onClose}
      />
      {/* Palette */}
      <div className="fixed inset-x-0 top-[15vh] z-50 flex items-start justify-center pointer-events-none">
        <div
          className="w-full max-w-md mx-4 bg-white dark:bg-gray-900 border border-gray-200 dark:border-gray-700 rounded-lg shadow-2xl pointer-events-auto overflow-hidden"
          onKeyDown={handleKey}
        >
          {/* Input */}
          <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-200 dark:border-gray-700">
            <Search className="w-4 h-4 text-gray-400" />
            <input
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => { setQuery(e.target.value); setSelectedIndex(0); }}
              placeholder="Jump to a view or command…"
              className="flex-1 bg-transparent text-sm text-gray-900 dark:text-gray-100 placeholder-gray-400 focus:outline-none"
            />
            <button
              onClick={onClose}
              className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300"
              title="Close (Esc)"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
          {/* Results */}
          <div className="max-h-[40vh] overflow-y-auto py-1">
            {filtered.length === 0 && (
              <div className="px-3 py-4 text-center text-xs text-gray-400 dark:text-gray-500">
                No matches.
              </div>
            )}
            {filtered.map((cmd, i) => (
              <button
                key={cmd.id}
                onClick={() => cmd.run()}
                onMouseEnter={() => setSelectedIndex(i)}
                className={`w-full flex items-center justify-between px-3 py-2 text-left transition-colors ${
                  i === selectedIndex
                    ? 'bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300'
                    : 'text-gray-700 dark:text-gray-200 hover:bg-gray-50 dark:hover:bg-gray-800'
                }`}
              >
                <div className="flex flex-col">
                  <span className="text-sm">{cmd.label}</span>
                  {cmd.hint && (
                    <span className="text-[10px] text-gray-400 dark:text-gray-500">{cmd.hint}</span>
                  )}
                </div>
                {cmd.shortcut && (
                  <span className="text-[10px] text-gray-400 dark:text-gray-500 font-mono">{cmd.shortcut}</span>
                )}
              </button>
            ))}
          </div>
          {/* Footer hint */}
          <div className="flex items-center justify-end gap-3 px-3 py-1.5 border-t border-gray-200 dark:border-gray-700 text-[10px] text-gray-400 dark:text-gray-500">
            <span>↑↓ navigate</span>
            <span>↵ select</span>
            <span>esc close</span>
          </div>
        </div>
      </div>
    </>
  );
};
