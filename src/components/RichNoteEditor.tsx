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
import { scanService } from '../services/scanService';
import { ScanSessionPanel } from './ScanSessionPanel';
import {
  ScanSessionState,
  ScanSessionPage,
  newSessionId,
  loadSession,
  saveSession,
  clearSession,
} from '../services/scanSession';
import {
  CameraPickerModal,
  ContinuityCameraInfo,
  loadPreferredCameraId,
  savePreferredCameraId,
} from './CameraPickerModal';

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

  const [showScanMenu, setShowScanMenu] = useState(false);
  const scanMenuRef = useRef<HTMLDivElement>(null);

  // ── Continuity Camera picker state ──────────────────────────────────────
  // When more than one camera is available we show a modal so the user can
  // choose. The chosen camera id (and the scan mode that triggered the
  // picker) is held here so handleContinuityScan can resume after selection.
  const [pickerOpen, setPickerOpen]       = useState(false);
  const [pickerCameras, setPickerCameras] = useState<ContinuityCameraInfo[]>([]);
  const [pickerMode, setPickerMode]       = useState<'document' | 'photo'>('document');

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
    // Backend has already created the merged note + broadcast canvas_item_created
    // via WebSocket. Just clear the session locally.
    setSession(null);
    ctx.addToast({
      type: 'success',
      title: 'Scan Complete',
      message: 'Pages merged into a new note.',
      duration: 3000,
    });
  }, [ctx]);

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
        ctx.addToast({ type: 'info', title: 'Processing Scan', message: mode === 'photo' ? 'Deconstructing scene...' : 'Analyzing document...', duration: 3000 });
        const res = await fetch(`${API_BASE_URL}/scan/process`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file_path: selected, notebook_id: ctx.selectedNotebookId, mode })
        });
        
        if (!res.ok) {
          throw new Error('Failed to start scan processing');
        }
      }
    } catch (err) {
      console.error("Scan error", err);
      ctx.addToast({ type: 'error', title: 'Scan Error', message: String(err), duration: 4000 });
    }
  };

  // Sprint 7+ : Continuity Camera (macOS only). Flow:
  //   1. Enumerate cameras via `list_continuity_cameras` (sidecar --list).
  //   2. If no preferred camera is remembered AND there's >1 camera, open
  //      the picker. Otherwise skip straight to capture.
  //   3. Capture invokes `trigger_continuity_camera` with the chosen id;
  //      the sidecar uses that exact device or, if missing, falls back to
  //      first .continuityCamera.
  //   4. Resulting image paths are either added to the active scan session
  //      (multi-page accumulation) or POSTed to /scan/process directly.
  //
  // Picker preference is persisted to localStorage (CAMERA_PREF_STORAGE_KEY)
  // so subsequent scans skip the modal until the user clears it.
  const runCapture = async (mode: 'document' | 'photo', cameraId: string | null) => {
    ctx.addToast({
      type: 'info',
      title: 'Waiting for iPhone',
      message: 'Tap Take Photo or Scan Documents on your iPhone to capture.',
      duration: 5000,
    });

    // include_non_continuity = true when the user has explicitly picked a
    // non-iPhone camera, so the sidecar is allowed to use it. When no
    // preferred id is set we keep the strict iPhone-only behaviour.
    const result = await invoke<{ status: string; paths: string[]; message?: string }>(
      'trigger_continuity_camera',
      { cameraId, includeNonContinuity: !!cameraId }
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

    // Use the streaming /scan/process-batch endpoint so the user gets live
    // per-stage feedback ("Processing page 1 of 1…", "Cleanup pass…", etc.)
    // instead of staring at an empty editor for 30-60s with nothing
    // happening. Single-image capture is just a 1-element batch.
    ctx.addToast({
      type: 'info',
      title: mode === 'photo' ? 'Deconstructing scene…' : 'Analyzing document…',
      message: 'Vision OCR running — this can take 20-60s on first run.',
      duration: 6000,
    });

    try {
      const finalResult = await scanService.processBatchWithProgress(
        result.paths,
        {
          notebookId: ctx.selectedNotebookId,
          mode,
          // Progress events are surfaced as transient toasts so the user
          // can confirm something is in fact happening. We throttle by
          // only toasting on stage changes, not every percent tick.
          onProgress: (() => {
            let lastStage = '';
            return (evt) => {
              if (evt.stage === lastStage) return;
              lastStage = evt.stage;
              ctx.addToast({
                type: 'info',
                title: 'Scanning…',
                message: `${evt.message} (${evt.percent}%)`,
                duration: 2500,
              });
            };
          })(),
        },
      );
      ctx.addToast({
        type: 'success',
        title: 'Scan Complete',
        message: finalResult.title
          ? `Created note: ${finalResult.title}`
          : 'New scan note added to canvas.',
        duration: 4000,
      });
    } catch (err) {
      // Re-throw so the outer handler in handleContinuityScan reports it.
      throw err;
    }
  };

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
      // Quick non-blocking enumerate. Returns [] if perms are denied or no
      // camera is attached — we fall through to the legacy "capture and let
      // the sidecar produce the friendly error" path in that case.
      const cameras = await invoke<ContinuityCameraInfo[]>('list_continuity_cameras');
      const remembered = loadPreferredCameraId();
      const rememberedExists = remembered && cameras.some(c => c.id === remembered);

      // Decide whether to show the picker.
      //   • 0 or 1 camera → no picker (sidecar handles missing-iphone case)
      //   • multiple AND no remembered choice → show picker
      //   • multiple AND remembered choice still present → use it silently
      if (cameras.length > 1 && !rememberedExists) {
        setPickerCameras(cameras);
        setPickerMode(mode);
        setPickerOpen(true);
        return;
      }

      const cameraId = rememberedExists ? remembered : null;
      await runCapture(mode, cameraId);
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

  const handlePickerSelect = async (cam: ContinuityCameraInfo, remember: boolean) => {
    setPickerOpen(false);
    if (remember) savePreferredCameraId(cam.id);
    try {
      await runCapture(pickerMode, cam.id);
    } catch (err) {
      console.error('Continuity scan (post-picker) error', err);
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

      {/* Sprint 8: Scan Session Panel (thumbnail grid + finish button) */}
      {session && (
        <ScanSessionPanel
          session={session}
          onReorder={reorderSessionPage}
          onDelete={deleteSessionPage}
          onFinish={finishSession}
          onCancel={cancelSession}
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

      {/* Camera picker — only shown when multiple cameras are available and no
          remembered choice. handlePickerSelect resumes the capture flow. */}
      <CameraPickerModal
        isOpen={pickerOpen}
        cameras={pickerCameras}
        onSelect={handlePickerSelect}
        onCancel={() => setPickerOpen(false)}
      />
    </div>
  );
};

export default RichNoteEditor;
