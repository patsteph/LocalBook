// Scan Session persistence — Sprint 8
//
// Session state lives in React, but we mirror it to localStorage so that an
// accidental reload or app restart doesn't destroy a user's in-progress
// scanning session (captures accumulated but not yet transcribed).
//
// Schema is versioned so future breaking changes can migrate or invalidate
// old payloads cleanly.

const STORAGE_KEY = 'localbook.scanSession.v1';

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

export function loadSession(): ScanSessionState | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as ScanSessionState;
    // Minimal shape validation — drop anything malformed rather than crash
    if (!parsed || !Array.isArray(parsed.pages) || !parsed.sessionId) {
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
