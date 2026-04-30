import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { BlockNoteSchema, defaultBlockSpecs, defaultInlineContentSpecs, defaultStyleSpecs } from '@blocknote/core';
import '@blocknote/core/fonts/inter.css';
import { BlockNoteView } from '@blocknote/mantine';
import '@blocknote/mantine/style.css';
import { useCreateBlockNote, SuggestionMenuController, createReactInlineContentSpec } from '@blocknote/react';
import { Save, Mic, Loader2, Camera } from 'lucide-react';
import { open } from '@tauri-apps/plugin-dialog';
import { invoke } from '@tauri-apps/api/core';
import { WritingAssistBar } from './WritingAssistBar';
import { useCanvas } from './canvas/CanvasContext';
import { CanvasItem } from './canvas/types';
import { sourceService } from '../services/sources';
import { voiceService } from '../services/voice';
import { settingsService } from '../services/settings';
import { noteService } from '../services/noteService';
import { API_BASE_URL } from '../services/api';
import { scanService, ScanProgressEvent } from '../services/scanService';
import { ScanSessionPanel } from './ScanSessionPanel';
import {
  ScanSessionState,
  ScanSessionPage,
  newSessionId,
  loadSession,
  saveSession,
  clearSession,
} from '../services/scanSession';

// macOS detection for Continuity Camera button (Sprint 7).
// Uses userAgent instead of Tauri's async platform() so we can render
// conditionally on first paint without a loading flicker.
const IS_MACOS = typeof navigator !== 'undefined'
  && /mac/i.test(navigator.userAgent)
  && !/iphone|ipad|ipod/i.test(navigator.userAgent);

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
    const trimmed = markdown.trim();

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
      const parsed = await editor.tryParseMarkdownToBlocks(trimmed);
      blocksToInsert = (parsed && parsed.length > 0)
        ? parsed
        : [{ type: 'paragraph' as const, content: trimmed }];
    } catch (e) {
      console.warn('[scan-insert] markdown parse failed, inserting as paragraph:', e);
      blocksToInsert = [{ type: 'paragraph' as const, content: trimmed }];
    }

    editor.insertBlocks(blocksToInsert, anchor, 'after');
    handleEditorChange();
  }, [editor, handleEditorChange]);

  const [showScanMenu, setShowScanMenu] = useState(false);
  const scanMenuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (scanMenuRef.current && !scanMenuRef.current.contains(e.target as Node)) {
        setShowScanMenu(false);
      }
    };
    if (showScanMenu) document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [showScanMenu]);

  // ── Sprint 8: Scan Session state ──────────────────────────────────────────
  // sessionMode is the user's toggle; session is the active batch being
  // accumulated. Both persist: sessionMode just follows `session` existence,
  // `session` mirrors to localStorage on every mutation so reloads recover.
  //
  // CRITICAL: only restore a persisted session if it belongs to THIS note.
  // Without the noteId scope filter, every freshly-created note would
  // resurrect a lingering session from some prior abandoned note.
  const [session, setSession] = useState<ScanSessionState | null>(
    () => loadSession(item.id)
  );
  const sessionMode = session !== null;

  // Persist any session change so an accidental reload or app crash doesn't
  // lose the user's accumulated captures.
  useEffect(() => {
    if (session) saveSession(session);
    else clearSession();
  }, [session]);

  const startSession = useCallback((mode: 'document' | 'photo') => {
    setSession({
      sessionId: newSessionId(),
      noteId: item.id,
      notebookId: ctx.selectedNotebookId || null,
      mode,
      pages: [],
      createdAt: new Date().toISOString(),
    });
    setShowScanMenu(false);
  }, [ctx.selectedNotebookId, item.id]);

  const cancelSession = useCallback(() => {
    setSession(null);
  }, []);

  const addPageToSession = useCallback(
    (path: string, source: ScanSessionPage['source']) => {
      setSession(prev => {
        if (!prev) return prev;
        const label = path.split('/').pop() || `Page ${prev.pages.length + 1}`;
        return {
          ...prev,
          pages: [
            ...prev.pages,
            { path, label, addedAt: new Date().toISOString(), source },
          ],
        };
      });
    },
    [],
  );

  const reorderSessionPage = useCallback((from: number, to: number) => {
    setSession(prev => {
      if (!prev) return prev;
      if (to < 0 || to >= prev.pages.length || from === to) return prev;
      const next = prev.pages.slice();
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return { ...prev, pages: next };
    });
  }, []);

  const deleteSessionPage = useCallback((index: number) => {
    setSession(prev => {
      if (!prev) return prev;
      const next = prev.pages.slice();
      next.splice(index, 1);
      return { ...prev, pages: next };
    });
  }, []);

  const finishSession = useCallback(() => {
    // Sprint 9: the inline processBatch callback (handleSessionInline) has
    // already inserted the merged markdown into the open editor and shown a
    // success toast. We just clear the session here so the panel collapses
    // and the user is back to a single-page editor.
    setSession(null);
  }, []);

  // File-picker scan path. Sprint 9: when a note is open we run inline OCR
  // and append the result to the current note instead of forking a new one.
  // Session mode still accumulates pages and defers OCR to handleSessionInline.
  const handleScan = async (mode: 'document' | 'photo') => {
    setShowScanMenu(false);
    try {
      const selected = await open({
        multiple: false,
        filters: [{
          name: 'Image',
          extensions: ['png', 'jpeg', 'jpg', 'webp']
        }]
      });

      if (selected && typeof selected === 'string') {
        // Session mode: accumulate into the batch instead of processing now.
        if (sessionMode) {
          addPageToSession(selected, 'file');
          return;
        }
        await runInlineOcr([selected], mode);
      }
    } catch (err) {
      console.error("Scan error", err);
      ctx.addToast({ type: 'error', title: 'Scan Error', message: String(err), duration: 4000 });
    }
  };

  // Single source of truth for "OCR these images and insert the result into
  // the open note." Used by every scan trigger:
  //   • file picker (handleScan)
  //   • Continuity Camera single capture (runCapture)
  //   • multi-page session finish (ScanSessionPanel processBatch callback)
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

  // Continuity Camera capture (macOS only). Sprint 9 architecture:
  //   1. Tauri spawns the signed `continuity-camera` sidecar.
  //   2. The sidecar shows a small "Insert from iPhone" launcher window.
  //      The user clicks Capture; AppKit pops up a contextual menu that's
  //      auto-populated with iPhone-side options (Take Photo, Scan
  //      Documents, Add Sketch).
  //   3. The user picks a mode on the iPhone screen and captures — multi-
  //      page "Scan Documents" returns N images in one batch with no Mac
  //      round-trip per page.
  //   4. The sidecar saves the image(s) and returns paths via JSON on
  //      stdout; we either accumulate them into an active scan session or
  //      OCR them inline and insert the result at the cursor.
  //
  // The `mode` parameter only controls how the OCR pipeline interprets the
  // result — it does NOT pre-select an iPhone capture mode. That choice
  // lives entirely on the iPhone now.
  const runCapture = async (mode: 'document' | 'photo') => {
    ctx.addToast({
      type: 'info',
      title: 'Insert from iPhone',
      message: 'Click “Capture from iPhone” on the launcher window, then pick a mode on your iPhone screen.',
      duration: 6000,
    });

    // The `cameraId` and `includeNonContinuity` args are accepted by the
    // Rust command for backward compatibility, but the new import-from-
    // device sidecar ignores them — AppKit and the iPhone handle device
    // selection automatically.
    const result = await invoke<{ status: string; paths: string[]; message?: string }>(
      'trigger_continuity_camera',
      { cameraId: null, includeNonContinuity: false }
    );

    if (result.status !== 'ok' || result.paths.length === 0) {
      ctx.addToast({
        type: 'error',
        title: 'Capture Failed',
        message: result.message || 'No image was captured.',
        duration: 6000,
      });
      return;
    }

    if (sessionMode) {
      for (const path of result.paths) {
        addPageToSession(path, 'continuity');
      }
      ctx.addToast({
        type: 'info',
        title: 'Pages Added',
        message: `${result.paths.length} page${result.paths.length !== 1 ? 's' : ''} added to session.`,
        duration: 2500,
      });
      return;
    }

    // Single-shot capture: OCR and insert at cursor in the open note.
    await runInlineOcr(result.paths, mode);
  };

  // Entry point invoked from the Scan menu's Continuity Camera options.
  // No camera picker, no list_continuity_cameras call: AppKit's import-
  // from-device flow shows the user every paired iPhone in its own popup
  // menu, so duplicating that on the Mac side would just add friction.
  const handleContinuityScan = async (mode: 'document' | 'photo') => {
    setShowScanMenu(false);
    if (!IS_MACOS) {
      ctx.addToast({
        type: 'error',
        title: 'Not Available',
        message: 'Continuity Camera requires macOS 12+ with a paired iPhone or iPad.',
        duration: 4000,
      });
      return;
    }

    try {
      await runCapture(mode);
    } catch (err) {
      console.error('Continuity scan error', err);
      ctx.addToast({
        type: 'error',
        title: 'Scan Error',
        message: String(err),
        duration: 4000,
      });
    }
  };

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
        <div className="relative" ref={scanMenuRef}>
          <button 
            onClick={() => {
              const next = !showScanMenu;
              setShowScanMenu(next);
              // Pre-warm the vision model the moment the menu opens. By the
              // time the user picks a mode and finishes capturing (~5-15s
              // minimum), Granite-Vision is already resident in Ollama, so
              // OCR starts emitting progress within ~1s instead of waiting
              // 5-15s for a cold load. Fire-and-forget — failures are logged
              // server-side and never affect the UI.
              if (next) {
                fetch(`${API_BASE_URL}/scan/warmup`, { method: 'POST' })
                  .catch(err => console.debug('[scan] vision warmup ping failed (non-fatal):', err));
              }
            }}
            className="flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 dark:bg-gray-800 dark:text-gray-200 dark:border-gray-600 dark:hover:bg-gray-700 transition-colors shadow-sm whitespace-nowrap"
          >
            <Camera className="w-4 h-4" />
            Scan
          </button>
          
          {showScanMenu && (
            <div className="absolute right-0 mt-2 w-60 bg-white dark:bg-gray-800 rounded-lg shadow-lg border border-gray-200 dark:border-gray-700 py-1 z-50">
              {IS_MACOS && (
                <>
                  <div className="px-4 pt-2 pb-1 text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
                    From iPhone
                  </div>
                  <button
                    onClick={() => handleContinuityScan('document')}
                    className="w-full text-left px-4 py-2 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
                  >
                    📱 Scan Documents
                  </button>
                  <button
                    onClick={() => handleContinuityScan('photo')}
                    className="w-full text-left px-4 py-2 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
                  >
                    📱 Take Photo
                  </button>
                  <div className="my-1 border-t border-gray-200 dark:border-gray-700" />
                  <div className="px-4 pt-1 pb-1 text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
                    From File
                  </div>
                </>
              )}
              <button
                onClick={() => handleScan('document')}
                className="w-full text-left px-4 py-2 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
              >
                Scan Document (OCR)
              </button>
              <button
                onClick={() => handleScan('photo')}
                className="w-full text-left px-4 py-2 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
              >
                Scan Photo (Scene)
              </button>
              {/* Sprint 8: Multi-page session mode */}
              <div className="my-1 border-t border-gray-200 dark:border-gray-700" />
              <div className="px-4 pt-1 pb-1 text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
                Multi-page Session
              </div>
              {sessionMode ? (
                <button
                  onClick={() => { cancelSession(); setShowScanMenu(false); }}
                  className="w-full text-left px-4 py-2 text-sm text-red-600 dark:text-red-400 hover:bg-gray-100 dark:hover:bg-gray-700"
                >
                  Cancel Session ({session!.pages.length} pages)
                </button>
              ) : (
                <>
                  <button
                    onClick={() => startSession('document')}
                    className="w-full text-left px-4 py-2 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
                  >
                    Start Document Session
                  </button>
                  <button
                    onClick={() => startSession('photo')}
                    className="w-full text-left px-4 py-2 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
                  >
                    Start Photo Session
                  </button>
                </>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Sprint 8: Scan Session Panel (thumbnail grid + finish button).
          Sprint 9: the processBatch callback delegates to runInlineOcr so
          single-capture and multi-page session flows share one code path —
          OCR runs against /scan/ocr-batch and the merged markdown is
          inserted into the open note at the cursor (no fork-a-new-note).
          The panel suppresses runInlineOcr's own progress toasts by
          providing its own onProgress handler that drives the panel's
          progress bar. */}
      {session && (
        <ScanSessionPanel
          session={session}
          onReorder={reorderSessionPage}
          onDelete={deleteSessionPage}
          onFinish={finishSession}
          onCancel={cancelSession}
          processBatch={async (filePaths, mode, onProgress) => {
            const r = await runInlineOcr(filePaths, mode, { onProgress });
            return { totalPages: r?.totalPages };
          }}
        />
      )}

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
