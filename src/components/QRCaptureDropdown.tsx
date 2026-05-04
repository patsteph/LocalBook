/**
 * QRCaptureDropdown — Dropdown panel that displays a QR code for phone-based
 * document capture. Connects via WebSocket to receive OCR results as they
 * process and calls `onCaptureReceived` to insert them into the active note.
 */
import React, { useEffect, useRef, useState, useCallback } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import { X, Smartphone, Loader2, CheckCircle, AlertCircle } from 'lucide-react';
import { captureService, CaptureSession, CapturePageEvent } from '../services/captureService';

interface QRCaptureDropdownProps {
  onCaptureReceived: (markdown: string) => Promise<void>;
  onClose: () => void;
}

interface PageStatus {
  index: number;
  status: 'received' | 'processing' | 'complete' | 'error';
  contentType?: string;
}

export function QRCaptureDropdown({ onCaptureReceived, onClose }: QRCaptureDropdownProps) {
  const [session, setSession] = useState<CaptureSession | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pages, setPages] = useState<PageStatus[]>([]);
  const [sessionComplete, setSessionComplete] = useState(false);
  const [totalChars, setTotalChars] = useState(0);
  const wsCleanupRef = useRef<(() => void) | null>(null);
  const dropdownRef = useRef<HTMLDivElement>(null);

  // Create session on mount
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
      } catch (err: any) {
        if (!cancelled) {
          setError(err?.message || 'Failed to start capture session');
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  // Connect WebSocket when session is ready
  useEffect(() => {
    if (!session) return;

    const handleEvent = (event: CapturePageEvent) => {
      switch (event.type) {
        case 'page_received':
          setPages(prev => [
            ...prev,
            { index: event.page_index, status: 'received' },
          ]);
          break;

        case 'page_complete':
          setPages(prev =>
            prev.map(p =>
              p.index === event.page_index
                ? { ...p, status: 'complete', contentType: event.content_type }
                : p
            )
          );
          // Insert OCR text into the note
          if (event.ocr_text) {
            setTotalChars(prev => prev + event.ocr_text!.length);
            onCaptureReceived(event.ocr_text);
          }
          break;

        case 'page_error':
          setPages(prev =>
            prev.map(p =>
              p.index === event.page_index
                ? { ...p, status: 'error' }
                : p
            )
          );
          break;

        case 'session_complete':
          setSessionComplete(true);
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

  // Close handler
  const handleClose = useCallback(() => {
    if (wsCleanupRef.current) wsCleanupRef.current();
    if (session) captureService.closeSession(session.session_id);
    onClose();
  }, [session, onClose]);

  // Close on click outside
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        handleClose();
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [handleClose]);

  // Auto-close after session complete
  useEffect(() => {
    if (sessionComplete && pages.every(p => p.status === 'complete' || p.status === 'error')) {
      const timer = setTimeout(handleClose, 3000);
      return () => clearTimeout(timer);
    }
  }, [sessionComplete, pages, handleClose]);

  const completedCount = pages.filter(p => p.status === 'complete').length;
  const processingCount = pages.filter(p => p.status === 'received' || p.status === 'processing').length;
  const errorCount = pages.filter(p => p.status === 'error').length;

  return (
    <div
      ref={dropdownRef}
      className="absolute right-0 mt-2 w-80 bg-white dark:bg-gray-800 rounded-xl shadow-2xl border border-gray-200 dark:border-gray-700 z-50 overflow-hidden"
      style={{ maxHeight: '480px' }}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/80">
        <div className="flex items-center gap-2">
          <Smartphone className="w-4 h-4 text-indigo-500" />
          <span className="text-sm font-semibold text-gray-800 dark:text-gray-200">
            Scan with Phone
          </span>
        </div>
        <button
          onClick={handleClose}
          className="p-1 rounded-full hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
        >
          <X className="w-4 h-4 text-gray-500" />
        </button>
      </div>

      {/* Content */}
      <div className="p-4">
        {error ? (
          <div className="text-center py-4">
            <AlertCircle className="w-8 h-8 text-red-500 mx-auto mb-2" />
            <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
          </div>
        ) : !session ? (
          <div className="flex flex-col items-center py-6 gap-3">
            <Loader2 className="w-6 h-6 text-indigo-500 animate-spin" />
            <p className="text-sm text-gray-500">Starting capture session…</p>
          </div>
        ) : sessionComplete ? (
          <div className="text-center py-4">
            <CheckCircle className="w-10 h-10 text-green-500 mx-auto mb-2" />
            <p className="text-sm font-semibold text-gray-800 dark:text-gray-200">
              Session Complete
            </p>
            <p className="text-xs text-gray-500 mt-1">
              {completedCount} page{completedCount !== 1 ? 's' : ''} · {totalChars.toLocaleString()} characters
            </p>
          </div>
        ) : (
          <>
            {/* QR Code */}
            <div className="flex flex-col items-center gap-3">
              <div className="bg-white p-3 rounded-lg shadow-inner">
                <QRCodeSVG
                  value={session.capture_url}
                  size={180}
                  level="M"
                  includeMargin={false}
                />
              </div>
              <p className="text-xs text-gray-500 dark:text-gray-400 text-center max-w-[240px]">
                Scan with your iPhone camera to start capturing pages
              </p>
            </div>

            {/* Live page status */}
            {pages.length > 0 && (
              <div className="mt-4 pt-3 border-t border-gray-200 dark:border-gray-700">
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-semibold uppercase tracking-wide text-gray-400">
                    Pages
                  </span>
                  <span className="text-xs text-gray-500">
                    {completedCount} done
                    {processingCount > 0 && ` · ${processingCount} processing`}
                    {errorCount > 0 && ` · ${errorCount} failed`}
                  </span>
                </div>

                {/* Page indicators */}
                <div className="flex flex-wrap gap-1.5">
                  {pages.map((page) => (
                    <div
                      key={page.index}
                      className={`w-7 h-7 rounded-md flex items-center justify-center text-xs font-medium transition-all ${
                        page.status === 'complete'
                          ? 'bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400'
                          : page.status === 'error'
                          ? 'bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400'
                          : 'bg-indigo-100 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-400 animate-pulse'
                      }`}
                    >
                      {page.status === 'complete' ? '✓' : page.status === 'error' ? '!' : page.index + 1}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
