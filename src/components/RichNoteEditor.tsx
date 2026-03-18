import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { BlockNoteSchema, defaultBlockSpecs, defaultInlineContentSpecs, defaultStyleSpecs } from '@blocknote/core';
import '@blocknote/core/fonts/inter.css';
import { BlockNoteView } from '@blocknote/mantine';
import '@blocknote/mantine/style.css';
import { useCreateBlockNote } from '@blocknote/react';
import { Save, Mic, Loader2 } from 'lucide-react';
import { WritingAssistBar } from './WritingAssistBar';
import { useCanvas } from './canvas/CanvasContext';
import { CanvasItem } from './canvas/types';
import { sourceService } from '../services/sources';
import { voiceService } from '../services/voice';
import { settingsService } from '../services/settings';

// ─── Types ──────────────────────────────────────────────────────────────────
interface RichNoteEditorProps {
  item: CanvasItem;
  compact?: boolean;
}

// ─── Custom Schema ──────────────────────────────────────────────────────────
const schema = BlockNoteSchema.create({
  blockSpecs: {
    ...defaultBlockSpecs,
  },
  inlineContentSpecs: {
    ...defaultInlineContentSpecs,
  },
  styleSpecs: {
    ...defaultStyleSpecs,
  },
});

// ─── Helpers: BlockNote JSON ↔ metadata storage ─────────────────────────────
function getStoredBlocks(item: CanvasItem): any[] | null {
  try {
    const raw = item.metadata?.blocknoteJson;
    if (raw && typeof raw === 'string') {
      return JSON.parse(raw);
    }
    if (raw && Array.isArray(raw)) {
      return raw;
    }
  } catch { /* ignore parse errors */ }
  return null;
}

// ─── Component ──────────────────────────────────────────────────────────────
export const RichNoteEditor: React.FC<RichNoteEditorProps> = ({ item, compact = false }) => {
  const ctx = useCanvas();
  const [saving, setSaving] = useState(false);
  const [isRecording, setIsRecording] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [wordCount, setWordCount] = useState(0);
  const [charCount, setCharCount] = useState(0);
  const [selectedText, setSelectedText] = useState('');
  const [fullText, setFullText] = useState('');
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const initializedRef = useRef(false);
  const editorRef = useRef<any>(null);

  // Dark mode from app shell
  const darkMode = ctx.darkMode;

  // Resolve initial content: prefer stored BlockNote JSON, fall back to markdown parsing
  const initialContent = useMemo(() => {
    const stored = getStoredBlocks(item);
    if (stored && stored.length > 0) return stored;
    // If there's existing plain text content, we'll parse it after editor mounts
    return undefined;
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Create the BlockNote editor
  const editor = useCreateBlockNote({
    schema,
    initialContent: initialContent as any,
    domAttributes: {
      editor: {
        class: 'rich-note-editor-content',
      },
    },
  });

  // Store ref for external access
  editorRef.current = editor;

  // On mount: if we have plain text content but no stored blocks, parse markdown into blocks
  useEffect(() => {
    if (initializedRef.current) return;
    initializedRef.current = true;

    const stored = getStoredBlocks(item);
    if (!stored && item.content && item.content.trim()) {
      // Parse existing markdown content into blocks
      (async () => {
        try {
          const blocks = await editor.tryParseMarkdownToBlocks(item.content);
          if (blocks && blocks.length > 0) {
            editor.replaceBlocks(editor.document, blocks);
          }
        } catch (e) {
          console.warn('[RichNoteEditor] Markdown parse failed, using as paragraph:', e);
        }
      })();
    }

    // Set default title for new notes
    if (!item.content && !item.title) {
      const dateStr = new Date().toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
      settingsService.getUserProfile()
        .then(profile => {
          const name = profile.name?.trim();
          ctx.updateCanvasItem(item.id, { title: name ? `${name}'s Note — ${dateStr}` : `Note — ${dateStr}` });
        })
        .catch(() => ctx.updateCanvasItem(item.id, { title: `Note — ${dateStr}` }));
    }
  }, [editor]); // eslint-disable-line react-hooks/exhaustive-deps

  // Auto-save: convert blocks → markdown for RAG + store JSON for rich editing
  const handleEditorChange = useCallback(async () => {
    if (!editor) return;

    // Debounce save
    if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(async () => {
      try {
        const markdown = await editor.blocksToMarkdownLossy(editor.document);
        const blocksJson = JSON.stringify(editor.document);

        // Update word/char counts
        const text = markdown.trim();
        setWordCount(text ? text.split(/\s+/).filter(Boolean).length : 0);
        setCharCount(text.length);

        // Persist both formats
        ctx.updateCanvasItem(item.id, {
          content: markdown,
          metadata: {
            ...item.metadata,
            blocknoteJson: blocksJson,
          },
        });
      } catch (e) {
        console.error('[RichNoteEditor] Auto-save failed:', e);
      }
    }, 500);
  }, [editor, item.id, item.metadata, ctx]);

  // Cleanup timers and media on unmount
  useEffect(() => {
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        mediaRecorderRef.current.stream.getTracks().forEach(track => track.stop());
        mediaRecorderRef.current.stop();
      }
    };
  }, []);

  // Title change handler
  const handleTitleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    ctx.updateCanvasItem(item.id, { title: e.target.value });
  };

  // Save as notebook source
  const handleSaveAsSource = async () => {
    if (!ctx.selectedNotebookId) return;
    const markdown = await editor.blocksToMarkdownLossy(editor.document);
    if (!markdown.trim()) return;

    setSaving(true);
    try {
      await sourceService.createNote(
        ctx.selectedNotebookId,
        item.title.trim() || 'Untitled Note',
        markdown.trim()
      );
      ctx.addToast({ type: 'success', title: 'Note saved as source', message: item.title || 'Untitled Note' });
      ctx.triggerSourcesRefresh();
    } catch (err: any) {
      console.error('Save note failed:', err);
      ctx.addToast({ type: 'error', title: 'Failed to save note', message: err.message || 'Unknown error' });
    }
    setSaving(false);
  };

  // Dictation — record audio and transcribe
  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mediaRecorder = new MediaRecorder(stream);
      mediaRecorderRef.current = mediaRecorder;
      audioChunksRef.current = [];

      mediaRecorder.ondataavailable = (event) => {
        if (event.data.size > 0) audioChunksRef.current.push(event.data);
      };

      mediaRecorder.onstop = async () => {
        const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/webm' });
        stream.getTracks().forEach(track => track.stop());
        setIsTranscribing(true);
        try {
          const result = await voiceService.transcribe(
            new File([audioBlob], 'recording.webm', { type: 'audio/webm' }),
            ctx.selectedNotebookId || '',
            undefined,
            false
          );
          if (result.text) {
            // Insert transcribed text at cursor
            editor.insertBlocks(
              [{ type: 'paragraph', content: result.text }],
              editor.document[editor.document.length - 1],
              'after'
            );
            // Trigger save
            handleEditorChange();
          }
        } catch (err) {
          console.error('Transcription failed:', err);
          ctx.addToast({ type: 'error', title: 'Transcription failed', message: 'Is Whisper running?' });
        } finally {
          setIsTranscribing(false);
        }
      };

      mediaRecorder.start();
      setIsRecording(true);
    } catch (err) {
      console.error('Mic access denied:', err);
      ctx.addToast({ type: 'error', title: 'Microphone access denied' });
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
    }
  };

  // Track selection for WritingAssistBar
  const handleSelectionChange = useCallback(async () => {
    if (!editor) return;
    const sel = editor.getSelectedText();
    setSelectedText(sel || '');
    // Also keep full text up-to-date for WritingAssistBar
    try {
      const md = await editor.blocksToMarkdownLossy(editor.document);
      setFullText(md);
    } catch { /* ignore */ }
  }, [editor]);

  // WritingAssistBar: replace text (full doc or selection)
  const handleWritingReplace = useCallback(async (newText: string, replaceSelection: boolean) => {
    if (!editor) return;
    if (replaceSelection && selectedText) {
      // Replace the selected blocks with new content
      const selection = editor.getSelection();
      if (selection && selection.blocks.length > 0) {
        try {
          const newBlocks = await editor.tryParseMarkdownToBlocks(newText);
          editor.replaceBlocks(selection.blocks, newBlocks.length > 0 ? newBlocks : [{ type: 'paragraph' as const, content: newText }]);
        } catch {
          editor.replaceBlocks(selection.blocks, [{ type: 'paragraph' as const, content: newText }]);
        }
      }
    } else {
      // Replace entire document
      try {
        const newBlocks = await editor.tryParseMarkdownToBlocks(newText);
        if (newBlocks.length > 0) {
          editor.replaceBlocks(editor.document, newBlocks);
        }
      } catch {
        editor.replaceBlocks(editor.document, [{ type: 'paragraph' as const, content: newText }]);
      }
    }
    handleEditorChange();
  }, [editor, selectedText, handleEditorChange]);

  // WritingAssistBar: continue writing from end
  const handleWritingContinue = useCallback(async (continuation: string) => {
    if (!editor) return;
    try {
      const newBlocks = await editor.tryParseMarkdownToBlocks(continuation);
      const lastBlock = editor.document[editor.document.length - 1];
      if (newBlocks.length > 0) {
        editor.insertBlocks(newBlocks, lastBlock, 'after');
      } else {
        editor.insertBlocks([{ type: 'paragraph' as const, content: continuation }], lastBlock, 'after');
      }
    } catch {
      const lastBlock = editor.document[editor.document.length - 1];
      editor.insertBlocks([{ type: 'paragraph' as const, content: continuation }], lastBlock, 'after');
    }
    handleEditorChange();
  }, [editor, handleEditorChange]);

  return (
    <div className={`rich-note-editor flex flex-col ${compact ? 'px-3 py-2' : 'flex-1 min-h-0 px-5 py-4'}`}>
      {/* Title */}
      <input
        type="text"
        value={item.title}
        onChange={handleTitleChange}
        placeholder="Note title..."
        className={`w-full bg-transparent border-none outline-none text-gray-900 dark:text-white placeholder-gray-400 dark:placeholder-gray-500 mb-3 ${
          compact ? 'text-sm font-semibold' : 'text-xl font-bold'
        }`}
      />

      {/* BlockNote Editor */}
      <div
        className={`rich-note-editor-wrapper rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-900/60 overflow-hidden ${
          compact ? 'min-h-[200px]' : 'flex-1 min-h-0 flex flex-col'
        }`}
        onMouseUp={handleSelectionChange}
        onKeyUp={handleSelectionChange}
      >
        <BlockNoteView
          editor={editor}
          onChange={handleEditorChange}
          theme={darkMode ? 'dark' : 'light'}
          data-theming-css-variables-demo
        />
      </div>

      {/* AI Writing Assist */}
      <WritingAssistBar
        text={fullText}
        selectedText={selectedText}
        onReplace={handleWritingReplace}
        onContinue={handleWritingContinue}
        compact={compact}
        className="mt-2"
      />

      {/* Bottom toolbar */}
      <div className={`flex items-center justify-between mt-3 ${compact ? 'gap-2' : 'gap-3'}`}>
        <div className="flex items-center gap-2">
          {/* Dictation button */}
          <button
            onClick={isRecording ? stopRecording : startRecording}
            disabled={isTranscribing}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
              isRecording
                ? 'bg-red-100 dark:bg-red-900/30 text-red-600 dark:text-red-400 animate-pulse'
                : isTranscribing
                  ? 'bg-yellow-100 dark:bg-yellow-900/30 text-yellow-600 dark:text-yellow-400 animate-pulse'
                  : 'bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 hover:bg-gray-200 dark:hover:bg-gray-600'
            }`}
          >
            {isRecording ? (
              <><svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="2" /></svg> Stop</>
            ) : isTranscribing ? (
              <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Transcribing...</>
            ) : (
              <><Mic className="w-3.5 h-3.5" /> Dictate</>
            )}
          </button>

          {/* Word count */}
          <span className="text-xs text-gray-400 dark:text-gray-500">
            {wordCount.toLocaleString()} words · {charCount.toLocaleString()} chars
          </span>
        </div>

        {/* Save as Source */}
        <button
          onClick={handleSaveAsSource}
          disabled={saving || !ctx.selectedNotebookId}
          className="flex items-center gap-1.5 px-4 py-1.5 text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {saving ? (
            <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Saving...</>
          ) : (
            <><Save className="w-3.5 h-3.5" /> Save as Source</>
          )}
        </button>
      </div>
    </div>
  );
};

export default RichNoteEditor;
