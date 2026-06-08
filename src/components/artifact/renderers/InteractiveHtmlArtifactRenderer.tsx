/**
 * InteractiveHtmlArtifactRenderer — Phase 11 of v2-information-cortex.
 *
 * The first renderer that allows user-facing JavaScript to execute inside
 * an artifact. Isolated via the browser's native iframe sandbox:
 *
 *   sandbox="allow-scripts"   →  JS runs but origin is `null`; no access
 *                                 to parent's localStorage / cookies / IPC
 *                                 / `window.__TAURI__`.
 *   srcDoc=payload            →  Inline HTML; no external URL load.
 *
 * Parent ↔ iframe channel:
 *   - Inbound `{type: 'lb-resize', height: number}` → parent auto-fits the
 *     iframe height (bounded 200–4000 to absorb pathological values).
 *   - Inbound `{type: 'lb-result', payload: any}` → optional callback via
 *     `artifact.metadata.onResult` (mostly for future scoring use).
 *
 * Source validation: the parent's message listener verifies
 * `event.source === iframe.contentWindow`. Foreign postMessages are
 * silently ignored.
 *
 * Companion to HtmlArtifactRenderer (Phase 2 — strict, Shadow DOM,
 * no scripts) and NewsletterArtifactRenderer (Phase 9 — permissive,
 * Shadow DOM, no scripts, inline styles allowed). This is the only
 * renderer in the registry that runs untrusted JS.
 */

import React, { useCallback, useEffect, useRef, useState } from 'react';
import type { RendererProps } from '../../../types/artifact';

interface InteractiveHtmlMeta {
  onResult?: (payload: unknown) => void;
}

const MIN_HEIGHT = 200;
const MAX_HEIGHT = 4000;

export const InteractiveHtmlArtifactRenderer: React.FC<RendererProps<string>> = ({
  artifact,
  className = '',
}) => {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [height, setHeight] = useState<number>(MIN_HEIGHT);
  const payload = typeof artifact.payload === 'string' ? artifact.payload : '';
  const meta = (artifact.metadata || {}) as InteractiveHtmlMeta;

  const handleMessage = useCallback((event: MessageEvent) => {
    // Strict source check — only respond to messages from our own iframe.
    if (!iframeRef.current || event.source !== iframeRef.current.contentWindow) {
      return;
    }
    const data = event.data as { type?: string; height?: number; payload?: unknown } | null;
    if (!data || typeof data !== 'object' || typeof data.type !== 'string') {
      return;
    }
    if (data.type === 'lb-resize') {
      const requested = Number(data.height);
      if (!Number.isFinite(requested)) return;
      const bounded = Math.max(MIN_HEIGHT, Math.min(MAX_HEIGHT, requested));
      setHeight(bounded);
      return;
    }
    if (data.type === 'lb-result') {
      try {
        meta.onResult?.(data.payload);
      } catch {
        // Don't let a consumer callback crash the parent.
      }
      return;
    }
  }, [meta]);

  useEffect(() => {
    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [handleMessage]);

  return (
    <div className={className}>
      <iframe
        ref={iframeRef}
        title={artifact.title || 'Interactive artifact'}
        sandbox="allow-scripts"
        srcDoc={payload}
        style={{ width: '100%', height: `${height}px`, border: 0, display: 'block' }}
      />
    </div>
  );
};

export default InteractiveHtmlArtifactRenderer;
