import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { BlockNoteSchema, defaultBlockSpecs, defaultInlineContentSpecs, defaultStyleSpecs } from '@blocknote/core';
import '@blocknote/core/fonts/inter.css';
import { BlockNoteView } from '@blocknote/mantine';
import '@blocknote/mantine/style.css';
import { useCreateBlockNote, SuggestionMenuController, createReactInlineContentSpec } from '@blocknote/react';
import { Save, Mic, Loader2, Camera } from 'lucide-react';
import { open } from '@tauri-apps/plugin-dialog';
import { WritingAssistBar } from './WritingAssistBar';
import { useCanvas } from './canvas/CanvasContext';
import { CanvasItem } from './canvas/types';
import { sourceService } from '../services/sources';
import { voiceService } from '../services/voice';
import { settingsService } from '../services/settings';
import { noteService } from '../services/noteService';
import { API_BASE_URL } from '../services/api';
import { scanService, ScanProgressEvent } from '../services/scanService';
import { ScanQRBadge } from './ScanQRBadge';
import { sanitizeOcrMarkdown } from '../lib/sanitizeOcrMarkdown';


// ─── Types ──────────────────────────────────────────────────────────────────
interface RichNoteEditorProps {
  item: CanvasItem;
  compact?: boolean;
}

// ─── Custom Schema ──────────────────────────────────────────────────────────
const WikiLink = createReactInlineContentSpec(
  {
    type: "wikilink",
    propSchema: {
      target: { default: "Unknown" },
      id: { default: "" },
    },
    content: "none",
  },
  {
    render: (props) => (
      <span 
        className="wikilink px-1 rounded bg-blue-100 text-blue-800 dark:bg-blue-900/50 dark:text-blue-300 cursor-pointer hover:underline mx-0.5"
        onClick={() => {
          console.log("Wikilink clicked:", props.inlineContent.props.target);
        }}
      >
        [[{props.inlineContent.props.target}]]
      </span>
    ),
  }
);

const schema = BlockNoteSchema.create({
  blockSpecs: {
    ...defaultBlockSpecs,
  },
  inlineContentSpecs: {
    ...defaultInlineContentSpecs,
    wikilink: WikiLink,
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
  const [backlinks, setBacklinks] = useState<any[]>([]);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const initializedRef = useRef(false);
  const editorRef = useRef<any>(null);
  /** true once we've POSTed a create to the backend for this canvas item */
  const persistedRef = useRef(false);

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

  // Fetch backlinks
  useEffect(() => {
    const idToUse = item.metadata?.persistedNoteId || item.id;
    if (idToUse) {
      noteService.getBacklinks(idToUse)
        .then(setBacklinks)
        .catch(() => {});
    }
  }, [item.id, item.metadata?.persistedNoteId]);

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

        // Extract wikilinks directly from block structure
        const wikilinksOut: string[] = [];
        const extractLinks = (blocks: any[]) => {
          for (const b of blocks) {
            if (b.content && Array.isArray(b.content)) {
              for (const c of b.content) {
                if (c.type === 'wikilink') {
                  wikilinksOut.push(c.props.id || c.props.target);
                }
              }
            }
            if (b.children) extractLinks(b.children);
          }
        };
        extractLinks(editor.document);

        // Update word/char counts
        const text = markdown.trim();
        setWordCount(text ? text.split(/\s+/).filter(Boolean).length : 0);
        setCharCount(text.length);

        // Persist both formats to React state (in-memory, instant)
        ctx.updateCanvasItem(item.id, {
          content: markdown,
          metadata: {
            ...item.metadata,
            blocknoteJson: blocksJson,
            persistedNoteId: item.id,
          },
        });

        // Persist to backend SQLite (survive session close)
        if (text.length > 0) {
          const payload = {
            title: item.title || '',
            content_markdown: markdown,
            content_blocknote_json: blocksJson,
            notebook_id: ctx.selectedNotebookId,
            source_type: (item.metadata?.sourceType as any) || 'typed',
            wikilinks_out: wikilinksOut,
          };
          if (!persistedRef.current) {
            // First write — create the backend row using the canvas item ID
            await noteService.create({ note_id: item.id, ...payload });
            persistedRef.current = true;
          } else {
            // Subsequent writes — partial update
            await noteService.update(item.id, payload);
          }
        }
      } catch (e) {
        console.error('[RichNoteEditor] Auto-save failed:', e);
      }
    }, 500);
  }, [editor, item.id, item.title, item.metadata, ctx]);

  // Cleanup timers and media on unmount
  useEffect(() => {
    // On mount: check if this note already has a backend row (e.g., restored on app load)
    if (item.metadata?.persistedNoteId) {
      persistedRef.current = true;
    }
    return () => {
      if (saveTimerRef.current) clearTimeout(saveTimerRef.current);
      if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
        mediaRecorderRef.current.stream.getTracks().forEach(track => track.stop());
        mediaRecorderRef.current.stop();
      }
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

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

  // Sprint 9: insert OCR'd markdown from a scan into the open editor at the
  // current cursor position. Parses the markdown into BlockNote blocks (so
  // headings, lists, tables etc. survive the round-trip) and inserts AFTER
  // the cursor's block — this matches Notion-style "slash command" UX where
  // new content lands right where the user was working, not at the bottom
  // of the doc.
  //
  // Scans are additive: existing content is never replaced. Falls back to a
  // single paragraph block if markdown parsing fails (rare, but possible
  // for very malformed OCR output).
  const insertScannedMarkdown = useCallback(async (markdown: string) => {
    if (!editor || !markdown || !markdown.trim()) return;
    const cleaned = sanitizeOcrMarkdown(markdown);
    if (!cleaned) return;

    // Anchor at the cursor's current block; fall back to the last block if
    // the cursor isn't placed (e.g. editor was never focused after open).
    let anchor: any;
    try {
      anchor = editor.getTextCursorPosition()?.block;
    } catch { /* ignore */ }
    if (!anchor) {
      anchor = editor.document[editor.document.length - 1];
    }

    let blocksToInsert: any[];
    try {
      const parsed = await editor.tryParseMarkdownToBlocks(cleaned);
      blocksToInsert = (parsed && parsed.length > 0)
        ? parsed
        : [{ type: 'paragraph' as const, content: cleaned }];
    } catch (e) {
      console.warn('[scan-insert] markdown parse failed, inserting as paragraph:', e);
      blocksToInsert = [{ type: 'paragraph' as const, content: cleaned }];
    }

    // Soft visual divider before scanned content so successive captures
    // don't blur into each other or into existing prose. Skipped when the
    // anchor block is the very first block of an empty doc (avoids a
    // leading "---" on a brand-new note).
    const anchorIsEmpty =
      !!anchor &&
      Array.isArray(editor.document) &&
      editor.document.length === 1 &&
      (Array.isArray(anchor.content) ? anchor.content.length === 0 : !anchor.content);
    if (!anchorIsEmpty) {
      blocksToInsert = [
        { type: 'paragraph' as const, content: '' },
        ...blocksToInsert,
      ];
    }

    editor.insertBlocks(blocksToInsert, anchor, 'after');
    handleEditorChange();
  }, [editor, handleEditorChange]);

  // handleScan is used by ScanQRBadge's file-scan menu.
  // showScanMenu/showQRCapture state is now inside ScanQRBadge.

  const handleScan = async (mode: 'document' | 'photo') => {
    try {
      const selected = await open({
        multiple: false,
        filters: [{
          name: 'Image',
          extensions: ['png', 'jpeg', 'jpg', 'webp']
        }]
      });

      if (selected && typeof selected === 'string') {
        await runInlineOcr([selected], mode);
      }
    } catch (err) {
      console.error("Scan error", err);
      ctx.addToast({ type: 'error', title: 'Scan Error', message: String(err), duration: 4000 });
    }
  };

  // Single source of truth for "OCR these images and insert the result into
  // the open note." Used by:
  //   • file picker (handleScan)
  //   • QR phone capture (QRCaptureDropdown via onCaptureReceived)
  //
  // Document mode and photo mode both flow through here — the only
  // difference is which Ollama prompt the backend uses (set via `mode`).
  // The note is NEVER closed by this function: insertScannedMarkdown
  // appends the parsed blocks and flags the autosave timer; the user
  // remains in the editor with the new content visible and editable.
  //
  // Progress UI: callers that own a progress panel (the session panel)
  // pass their own onProgress handler. Otherwise we fall back to throttled
  // toast notifications so single-capture flows still get feedback.
  const runInlineOcr = useCallback(async (
    filePaths: string[],
    mode: 'document' | 'photo',
    opts?: { onProgress?: (evt: ScanProgressEvent) => void },
  ): Promise<{ totalPages: number; chars: number } | null> => {
    if (filePaths.length === 0) return null;

    // Only show the "OCR running…" toast when no caller-supplied progress
    // handler is in play — otherwise the panel's progress bar is the
    // source of truth and toasts would be redundant noise.
    if (!opts?.onProgress) {
      ctx.addToast({
        type: 'info',
        title: mode === 'photo' ? 'Deconstructing scene…' : 'Analyzing document…',
        message: 'Vision OCR running — this can take 20-60s on first run.',
        duration: 6000,
      });
    }

    // Throttled stage-change toasts for the single-capture path.
    let lastStage = '';
    const toastProgress = (evt: ScanProgressEvent) => {
      if (evt.stage === lastStage) return;
      lastStage = evt.stage;
      ctx.addToast({
        type: 'info',
        title: 'Scanning…',
        message: `${evt.message} (${evt.percent}%)`,
        duration: 2500,
      });
    };
    const onProgress = opts?.onProgress ?? toastProgress;

    let result: Awaited<ReturnType<typeof scanService.ocrBatchWithProgress>>;
    try {
      result = await scanService.ocrBatchWithProgress(filePaths, { mode, onProgress });
    } catch (err: any) {
      ctx.addToast({
        type: 'error',
        title: 'Scan Error',
        message: err?.message || String(err),
        duration: 5000,
      });
      throw err;
    }

    if (!result.merged_text || !result.merged_text.trim()) {
      ctx.addToast({
        type: 'error',
        title: 'No text found',
        message: 'OCR returned no usable text from the captured image(s).',
        duration: 4000,
      });
      return null;
    }

    await insertScannedMarkdown(result.merged_text);
    const totalPages = result.total_pages ?? filePaths.length;
    const chars = result.chars ?? result.merged_text.length;
    ctx.addToast({
      type: 'success',
      title: 'Inserted into note',
      message: `Added ${chars} characters from ${totalPages} page${totalPages !== 1 ? 's' : ''}.`,
      duration: 4000,
    });
    return { totalPages, chars };
  }, [ctx, insertScannedMarkdown]);



  return (
    <div className={`rich-note-editor flex flex-col ${compact ? 'px-3 py-2' : 'flex-1 min-h-0 px-5 py-4'}`}>
      {/* Header. pr-10 reserves space for the absolute-positioned close X
          rendered by the parent (ChatInterface / CanvasItemCard) at top-3 right-3
          so the Scan button doesn't end up partially hidden under it. */}
      <div className="flex justify-between items-center mb-3 gap-3 pr-10">
        <input
          type="text"
          value={item.title}
          onChange={handleTitleChange}
          placeholder="Note title..."
          className={`flex-1 min-w-0 bg-transparent border-none outline-none text-gray-900 dark:text-white placeholder-gray-400 dark:placeholder-gray-500 ${
            compact ? 'text-sm font-semibold' : 'text-xl font-bold'
          }`}
        />
        <ScanQRBadge
          onCaptureReceived={insertScannedMarkdown}
          onFileScan={handleScan}
          compact={compact}
        />
      </div>

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
        >
          <SuggestionMenuController
            triggerCharacter={"["}
            getItems={async (query) => {
              if (!query.startsWith("[")) return [];
              const actualQuery = query.slice(1);
              const results = await noteService.searchEntities(actualQuery, ctx.selectedNotebookId);
              return results.map(r => ({
                title: r.title,
                subtext: r.type === 'note' ? '📝 Note' : '📄 Source',
                onItemClick: () => {
                  editor.insertInlineContent([
                    {
                      type: "wikilink",
                      props: { target: r.title, id: r.id },
                    },
                    " "
                  ]);
                }
              }));
            }}
          />
        </BlockNoteView>
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

      {/* Backlinks Panel */}
      {backlinks.length > 0 && !compact && (
        <div className="mt-4 pt-4 border-t border-gray-200 dark:border-gray-700">
          <h4 className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">Backlinks</h4>
          <ul className="flex flex-wrap gap-2">
            {backlinks.map(link => (
              <li key={link.id} className="text-xs px-2 py-1 bg-gray-100 dark:bg-gray-800 rounded text-blue-600 dark:text-blue-400 cursor-pointer hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors">
                {link.title}
              </li>
            ))}
          </ul>
        </div>
      )}

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
