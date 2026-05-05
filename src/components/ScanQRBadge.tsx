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
import React, { useEffect, useRef, useState, useCallback } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import { Camera, ChevronDown, Loader2, X } from 'lucide-react';
import { captureService, CaptureSession, CapturePageEvent } from '../services/captureService';
import { API_BASE_URL } from '../services/api';

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
}

export function ScanQRBadge({ onCaptureReceived, onFileScan, compact }: ScanQRBadgeProps) {
  const [session, setSession] = useState<CaptureSession | null>(null);
  const [expanded, setExpanded] = useState(false);
  const [pages, setPages] = useState<PageStatus[]>([]);
  const [showFileMenu, setShowFileMenu] = useState(false);
  const [totalChars, setTotalChars] = useState(0);
  const wsCleanupRef = useRef<(() => void) | null>(null);
  const badgeRef = useRef<HTMLDivElement>(null);
  const fileMenuRef = useRef<HTMLDivElement>(null);

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
          if (event.ocr_text) {
            setTotalChars(prev => prev + event.ocr_text!.length);
            onCaptureReceived(event.ocr_text);
          }
          break;

        case 'page_error':
          setPages(prev =>
            prev.map(p =>
              p.index === event.page_index ? { ...p, status: 'error' } : p
            )
          );
          break;
      }
    };

    const cleanup = captureService.connectWebSocket(session, handleEvent);
    wsCleanupRef.current = cleanup;

    return () => {
      cleanup();
      wsCleanupRef.current = null;
    };
  }, [session, onCaptureReceived]);

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

  // ── Close file menu on outside click ───────────────────────────────────────
  useEffect(() => {
    if (!showFileMenu) return;
    const handler = (e: MouseEvent) => {
      if (fileMenuRef.current && !fileMenuRef.current.contains(e.target as Node)) {
        setShowFileMenu(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [showFileMenu]);

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
          </div>
        )}
      </div>

      {/* ── File scan menu (separate small button) ── */}
      <div className="relative" ref={fileMenuRef}>
        <button
          onClick={() => {
            setShowFileMenu(!showFileMenu);
            if (!showFileMenu) {
              // Pre-warm vision model
              fetch(`${API_BASE_URL}/scan/warmup`, { method: 'POST' })
                .catch(() => {});
            }
          }}
          className="flex items-center gap-1 px-2 py-1.5 text-xs font-medium text-gray-500 bg-gray-50 border border-gray-200 rounded-lg hover:bg-gray-100 dark:bg-gray-800 dark:text-gray-400 dark:border-gray-600 dark:hover:bg-gray-700 transition-colors"
          title="Scan from file"
        >
          <Camera className="w-3.5 h-3.5" />
          <ChevronDown className="w-3 h-3" />
        </button>

        {showFileMenu && (
          <div className="absolute right-0 mt-1 w-52 bg-white dark:bg-gray-800 rounded-lg shadow-lg border border-gray-200 dark:border-gray-700 py-1 z-50">
            <div className="px-3 pt-1.5 pb-1 text-[10px] font-semibold uppercase tracking-wide text-gray-400 dark:text-gray-500">
              From File
            </div>
            <button
              onClick={() => { setShowFileMenu(false); onFileScan('document'); }}
              className="w-full text-left px-3 py-1.5 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
            >
              Scan Document (OCR)
            </button>
            <button
              onClick={() => { setShowFileMenu(false); onFileScan('photo'); }}
              className="w-full text-left px-3 py-1.5 text-sm text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700"
            >
              Scan Photo (Scene)
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
