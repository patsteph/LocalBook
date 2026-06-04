/**
 * ScanQRBadge — Persistent inline QR code that replaces the Scan button.
 *
 * Shows an 80px QR code that's always scannable from the note header.
 * Clicking it opens the full QRCaptureDropdown with page status.
 * A small "⋮" menu provides file-based scan options.
 *
 * The capture session is created lazily on mount, so the QR is immediately
 * ready when the user opens a note — zero clicks to start scanning.
 */
import { useEffect, useRef, useState } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import { AlertCircle, Loader2, X } from 'lucide-react';
import { captureService, CaptureSession, CapturePageEvent } from '../services/captureService';

interface ScanQRBadgeProps {
  /** Async callback to insert OCR'd markdown into the active note. */
  onCaptureReceived: (markdown: string) => Promise<void>;
  /** Callback to trigger file-based scan. */
  onFileScan: (mode: 'document' | 'photo') => void;
  /** True if the editor is in compact mode. */
  compact?: boolean;
}

interface PageStatus {
  index: number;
  status: 'received' | 'processing' | 'complete' | 'error';
  error?: string;
}

export function ScanQRBadge({ onCaptureReceived, onFileScan, compact }: ScanQRBadgeProps) {
  const [session, setSession] = useState<CaptureSession | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [pages, setPages] = useState<PageStatus[]>([]);
  const [totalChars, setTotalChars] = useState(0);
  // Latest backend error — typed so the banner can render category-
  // specific guidance ("Vision model X failed" vs. a generic message)
  // instead of leaving the user staring at a silent "processing" state.
  const [lastError, setLastError] = useState<{
    message: string;
    type: CapturePageEvent['error_type'];
    model: string;
  } | null>(null);
  const wsCleanupRef = useRef<(() => void) | null>(null);
  const badgeRef = useRef<HTMLDivElement>(null);

  // Stable ref for the parent-supplied insert callback. We deliberately do
  // NOT depend on `onCaptureReceived` in the WebSocket effect: the parent
  // typically passes a fresh closure each render, which would tear down
  // and reopen the WS on every keystroke / autosave. The backend replays
  // every prior `page_complete` event on reconnect (for legitimate recovery),
  // so a flapping WS turns into a runaway insertion loop where the same
  // OCR text is appended ~2x/sec until the editor is full of duplicates.
  // Holding the latest callback in a ref keeps the WS effect deps tight
  // while still calling whatever the parent passed on the most recent render.
  const onCaptureReceivedRef = useRef(onCaptureReceived);
  useEffect(() => {
    onCaptureReceivedRef.current = onCaptureReceived;
  }, [onCaptureReceived]);

  // Per-session set of page_indices we have already inserted into the note.
  // Backend replays results on reconnect; without this dedup, every replay
  // would trigger another insert. Cleared when the session changes.
  const deliveredPagesRef = useRef<Set<number>>(new Set());

  // ── Create session on mount ────────────────────────────────────────────────
  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const sess = await captureService.createSession();
        if (cancelled) {
          captureService.closeSession(sess.session_id);
          return;
        }
        setSession(sess);
      } catch (err) {
        console.warn('[ScanQRBadge] Failed to create session:', err);
        if (!cancelled) {
          setPages([{ index: -1, status: 'error' }]); // Use error state to stop spinner
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  // ── Cleanup session on unmount ─────────────────────────────────────────────
  useEffect(() => {
    return () => {
      if (wsCleanupRef.current) wsCleanupRef.current();
      // We can't use session state in cleanup because it's captured at
      // the time the effect ran. Use a ref-based approach.
    };
  }, []);

  const sessionRef = useRef<CaptureSession | null>(null);
  useEffect(() => {
    sessionRef.current = session;
    return () => {
      // On unmount or session change, close the old session
      if (sessionRef.current) {
        captureService.closeSession(sessionRef.current.session_id);
      }
    };
  }, [session]);

  // ── Connect WebSocket when session is ready ────────────────────────────────
  useEffect(() => {
    if (!session) return;

    const handleEvent = (event: CapturePageEvent) => {
      switch (event.type) {
        case 'page_received':
          setPages(prev => {
            // Avoid duplicates from reconnection replays
            if (prev.some(p => p.index === event.page_index)) return prev;
            return [...prev, { index: event.page_index, status: 'received' }];
          });
          break;

        case 'page_complete':
          setPages(prev =>
            prev.map(p =>
              p.index === event.page_index ? { ...p, status: 'complete' } : p
            )
          );
          // Insert OCR text exactly once per page_index. Backend replays
          // completed results to any reconnecting WebSocket so the receiver
          // can recover state — but we only want the side-effect (insert
          // into the note) the first time we see each index.
          if (event.ocr_text && !deliveredPagesRef.current.has(event.page_index)) {
            deliveredPagesRef.current.add(event.page_index);
            setTotalChars(prev => prev + event.ocr_text!.length);
            onCaptureReceivedRef.current(event.ocr_text);
          }
          break;

        case 'page_error': {
          const errMsg = event.error || 'Unknown error';
          const errType = event.error_type || 'generic';
          const errModel = event.error_model || '';
          setPages(prev =>
            prev.map(p =>
              p.index === event.page_index
                ? { ...p, status: 'error', error: errMsg }
                : p
            )
          );
          setLastError({ message: errMsg, type: errType, model: errModel });
          // Pop the dropdown so the user notices. They can dismiss it.
          setExpanded(true);
          console.warn('[ScanQRBadge] page_error:', { errType, errModel, errMsg });
          break;
        }
      }
    };

    // Reset the delivered-pages dedup whenever the session changes, otherwise
    // a fresh session would refuse to insert page 0 because we saw a page 0
    // for the previous session.
    deliveredPagesRef.current = new Set();

    const cleanup = captureService.connectWebSocket(session, handleEvent);
    wsCleanupRef.current = cleanup;

    return () => {
      cleanup();
      wsCleanupRef.current = null;
    };
    // IMPORTANT: do NOT add `onCaptureReceived` here. The callback is read
    // through `onCaptureReceivedRef` so changes to the parent's render
    // identity don't reopen the WebSocket. See the ref declaration above.
  }, [session]);

  // ── Close expanded view on outside click ───────────────────────────────────
  useEffect(() => {
    if (!expanded) return;
    const handler = (e: MouseEvent) => {
      if (badgeRef.current && !badgeRef.current.contains(e.target as Node)) {
        setExpanded(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [expanded]);

  const completedCount = pages.filter(p => p.status === 'complete').length;
  const processingCount = pages.filter(p => p.status === 'received' || p.status === 'processing').length;
  const errorCount = pages.filter(p => p.status === 'error').length;

  return (
    <div className="flex items-start gap-2" ref={badgeRef}>
      {/* ── Persistent mini QR badge ── */}
      <div className="relative">
        <button
          onClick={() => setExpanded(!expanded)}
          className="group flex flex-col items-center gap-1 cursor-pointer"
          title="Click to expand · Point phone camera to scan"
        >
          {/* QR code container */}
          <div className={`relative bg-white rounded-lg overflow-hidden shadow-sm border transition-all ${
            expanded
              ? 'border-indigo-400 ring-2 ring-indigo-200 dark:ring-indigo-800'
              : 'border-gray-200 dark:border-gray-600 hover:border-indigo-300 hover:shadow-md'
          }`}>
            {session ? (
              <div className="p-1">
                <QRCodeSVG
                  value={session.short_url}
                  size={compact ? 56 : 72}
                  level="L"
                  includeMargin={false}
                />
              </div>
            ) : pages.some(p => p.status === 'error') ? (
              <div className={`flex items-center justify-center bg-red-50 dark:bg-red-900/20 ${compact ? 'w-[64px] h-[64px]' : 'w-[80px] h-[80px]'}`}>
                <span className="text-[10px] text-red-500 font-bold px-2 text-center leading-tight">Backend Error</span>
              </div>
            ) : (
              <div className={`flex items-center justify-center ${compact ? 'w-[64px] h-[64px]' : 'w-[80px] h-[80px]'}`}>
                <Loader2 className="w-4 h-4 text-gray-400 animate-spin" />
              </div>
            )}

            {/* Live page counter badge */}
            {pages.length > 0 && (
              <div className={`absolute -top-1.5 -right-1.5 min-w-[20px] h-5 rounded-full flex items-center justify-center text-[10px] font-bold px-1 ${
                processingCount > 0
                  ? 'bg-indigo-500 text-white animate-pulse'
                  : errorCount > 0 && completedCount === 0
                  ? 'bg-red-500 text-white'
                  : 'bg-green-500 text-white'
              }`}>
                {completedCount > 0 ? `✓${completedCount}` : pages.length}
              </div>
            )}
          </div>

          {/* Label */}
          <span className="text-[10px] font-medium text-gray-500 dark:text-gray-400 group-hover:text-indigo-500 transition-colors">
            Scan
          </span>
        </button>

        {/* ── Expanded dropdown ── */}
        {expanded && (
          <div className="absolute right-0 mt-2 w-80 bg-white dark:bg-gray-800 rounded-xl shadow-2xl border border-gray-200 dark:border-gray-700 z-50 overflow-hidden">
            {/* Header */}
            <div className="flex items-center justify-between px-4 py-2 border-b border-gray-100 dark:border-gray-700">
              <span className="text-xs font-semibold text-gray-600 dark:text-gray-300 uppercase tracking-wide">
                Phone Capture
              </span>
              <button
                onClick={() => setExpanded(false)}
                className="p-1 rounded-full hover:bg-gray-100 dark:hover:bg-gray-700"
              >
                <X className="w-3.5 h-3.5 text-gray-400" />
              </button>
            </div>

            {/* Large QR */}
            <div className="p-4 flex flex-col items-center gap-2">
              {session && (
                <>
                  <div className="bg-white p-3 rounded-lg shadow-inner">
                    <QRCodeSVG
                      value={session.short_url}
                      size={200}
                      level="L"
                      includeMargin={false}
                    />
                  </div>
                  <p className="text-xs text-gray-500 dark:text-gray-400 text-center">
                    Point your iPhone camera at this code
                  </p>
                </>
              )}
            </div>

            {/* Page status */}
            {pages.length > 0 && (
              <div className="px-4 pb-3 border-t border-gray-100 dark:border-gray-700 pt-3">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-[10px] font-semibold uppercase tracking-wide text-gray-400">
                    Pages
                  </span>
                  <span className="text-[10px] text-gray-500">
                    {completedCount} done
                    {processingCount > 0 && ` · ${processingCount} processing`}
                    {errorCount > 0 && ` · ${errorCount} failed`}
                  </span>
                </div>
                <div className="flex flex-wrap gap-1">
                  {pages.map((page) => (
                    <div
                      key={page.index}
                      className={`w-6 h-6 rounded flex items-center justify-center text-[10px] font-semibold ${
                        page.status === 'complete'
                          ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                          : page.status === 'error'
                          ? 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
                          : 'bg-indigo-100 text-indigo-600 dark:bg-indigo-900/30 dark:text-indigo-400 animate-pulse'
                      }`}
                    >
                      {page.status === 'complete' ? '✓' : page.status === 'error' ? '!' : page.index + 1}
                    </div>
                  ))}
                </div>
                {totalChars > 0 && (
                  <p className="text-[10px] text-gray-400 mt-2">
                    {totalChars.toLocaleString()} characters extracted
                  </p>
                )}
              </div>
            )}
            {/* Error banner — only when the most recent capture failed.
                Renders category-specific guidance: vision-model failures
                point at the configured model + Settings, cleanup-model
                failures explain partial success, timeouts suggest retry,
                generic falls through to the raw backend message. */}
            {lastError && (() => {
              const isVision = lastError.type === 'vision_model';
              const isCleanup = lastError.type === 'cleanup_model';
              const isTimeout = lastError.type === 'timeout';
              const headline =
                isVision  ? 'Vision model failed'
                : isCleanup ? 'Cleanup model failed'
                : isTimeout ? 'Capture timed out'
                : 'Capture failed';
              return (
                <div className="px-4 py-2 bg-red-50 dark:bg-red-900/20 border-t border-red-200 dark:border-red-800/40">
                  <div className="flex items-start gap-2">
                    <AlertCircle className="w-3.5 h-3.5 text-red-500 mt-0.5 flex-shrink-0" />
                    <div className="flex-1 min-w-0">
                      <p className="text-[10px] font-semibold text-red-700 dark:text-red-300 uppercase tracking-wide">
                        {headline}
                        {lastError.model && (
                          <span className="ml-1 font-mono normal-case text-red-600 dark:text-red-400">
                            · {lastError.model}
                          </span>
                        )}
                      </p>
                      <p className="text-[11px] text-red-600 dark:text-red-400 mt-0.5 break-words">
                        {lastError.message}
                      </p>
                      {isVision && (
                        <p className="text-[10px] text-red-500 dark:text-red-400 mt-1">
                          Try a different vision model in <span className="font-semibold">Settings → Models</span>. Common working choices: <code className="font-mono">granite3.2-vision:2b</code>, <code className="font-mono">llava:7b</code>, <code className="font-mono">moondream:1.8b</code>.
                        </p>
                      )}
                      {isCleanup && (
                        <p className="text-[10px] text-red-500 dark:text-red-400 mt-1 italic">
                          Vision OCR succeeded but the text-cleanup pass failed. The page may still be partly recoverable from the backend logs.
                        </p>
                      )}
                      {isTimeout && (
                        <p className="text-[10px] text-red-500 dark:text-red-400 mt-1 italic">
                          The model took longer than 3 minutes. Try recapturing or use a faster vision model.
                        </p>
                      )}
                    </div>
                    <button
                      onClick={() => setLastError(null)}
                      className="p-0.5 rounded hover:bg-red-100 dark:hover:bg-red-900/40 flex-shrink-0"
                      aria-label="Dismiss error"
                    >
                      <X className="w-3 h-3 text-red-500" />
                    </button>
                  </div>
                </div>
              );
            })()}

            {/* File Scan Options integrated into panel */}
            <div className="bg-gray-50 dark:bg-gray-800/80 px-4 py-2 border-t border-gray-100 dark:border-gray-700 flex justify-between items-center">
              <span className="text-[10px] font-medium text-gray-500">Scan from file:</span>
              <div className="flex gap-2">
                <button
                  onClick={() => {
                    setExpanded(false);
                    onFileScan('document');
                  }}
                  className="px-2 py-1 text-[10px] font-semibold bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded hover:bg-gray-50 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 transition-colors"
                >
                  Document
                </button>
                <button
                  onClick={() => {
                    setExpanded(false);
                    onFileScan('photo');
                  }}
                  className="px-2 py-1 text-[10px] font-semibold bg-white dark:bg-gray-700 border border-gray-200 dark:border-gray-600 rounded hover:bg-gray-50 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 transition-colors"
                >
                  Photo
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
