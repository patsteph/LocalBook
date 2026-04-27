// Scan Session persistence — Sprint 8
//
// Session state lives in React, but we mirror it to localStorage so that an
// accidental reload or app restart doesn't destroy a user's in-progress
// scanning session (captures accumulated but not yet transcribed).
//
// Schema is versioned so future breaking changes can migrate or invalidate
// old payloads cleanly.

// Bumped to v2: schema now carries `noteId` so a lingering session on
// note A can't bleed into a freshly-created note B. v1 payloads are
// silently dropped because they have no way to identify their origin.
const STORAGE_KEY = 'localbook.scanSession.v2';
const LEGACY_KEYS = ['localbook.scanSession.v1'];

export interface ScanSessionPage {
  /** Absolute filesystem path returned by the scan source (Continuity or
   *  file picker). Backend reads from these paths directly. */
  path: string;
  /** Display name shown under the thumbnail. */
  label: string;
  /** Wall-clock timestamp of capture, ISO8601. */
  addedAt: string;
  /** Where this page came from — useful for analytics and troubleshooting. */
  source: 'continuity' | 'file' | 'unknown';
}

export interface ScanSessionState {
  sessionId: string;
  /** Note this session belongs to. Used to scope persistence so opening
   *  any other note doesn't accidentally inherit pages from this one. */
  noteId: string;
  notebookId: string | null;
  mode: 'document' | 'photo';
  pages: ScanSessionPage[];
  createdAt: string;
}

export function newSessionId(): string {
  // RFC4122-ish random ID; we don't need cryptographic strength here.
  return (
    'scan-' +
    Math.random().toString(36).slice(2, 10) +
    '-' +
    Date.now().toString(36)
  );
}

/**
 * Load any persisted session, optionally filtering to a specific note id.
 * If `noteId` is provided, returns null when the stored session belongs
 * to a different note (this is the common case — prevents a stale
 * session bleeding into newly opened notes).
 */
export function loadSession(noteId?: string): ScanSessionState | null {
  // Drop any legacy v1 payloads on first read so they can't haunt us.
  for (const k of LEGACY_KEYS) {
    try { localStorage.removeItem(k); } catch { /* ignore */ }
  }
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as ScanSessionState;
    // Minimal shape validation — drop anything malformed rather than crash
    if (!parsed || !Array.isArray(parsed.pages) || !parsed.sessionId || !parsed.noteId) {
      return null;
    }
    if (noteId !== undefined && parsed.noteId !== noteId) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export function saveSession(state: ScanSessionState): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // localStorage may be unavailable (private mode, quota) — non-fatal
  }
}

export function clearSession(): void {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}
