import api from './api';

export interface SearchResult {
  id: string;
  title: string;
  type: 'note' | 'source';
}

export interface CanvasNote {
  id: string;
  notebook_id: string | null;
  title: string;
  content_markdown: string;
  content_blocknote_json: string;
  source_type: 'typed' | 'scanned' | 'dictated' | 'mixed';
  note_type: string;
  tags: string[];
  voice_weight: number;
  original_image_paths: string[];
  scan_confidence: number | null;
  wikilinks_out: string[];
  created_at: string;
  updated_at: string;
  saved_as_source_id: string | null;
}

export interface NoteCreatePayload {
  /** Client canvas item ID — allows idempotent upsert on reconnect */
  note_id?: string;
  notebook_id?: string | null;
  title?: string;
  content_markdown?: string;
  content_blocknote_json?: string;
  source_type?: 'typed' | 'scanned' | 'dictated' | 'mixed';
  note_type?: string;
  tags?: string[];
  voice_weight?: number;
}

export interface NoteUpdatePayload {
  title?: string;
  content_markdown?: string;
  content_blocknote_json?: string;
  notebook_id?: string | null;
  source_type?: string;
  note_type?: string;
  tags?: string[];
  voice_weight?: number;
  saved_as_source_id?: string | null;
  wikilinks_out?: string[];
}

export const noteService = {
  /**
   * Create a new persisted canvas note.
   * Called by RichNoteEditor when a note first produces content.
   * Pass the canvas item's existing ID as note_id for stable identity.
   */
  async create(payload: NoteCreatePayload): Promise<CanvasNote> {
    const response = await api.post('/canvas-notes', payload);
    return response.data;
  },

  /**
   * Get a single note by ID.
   */
  async get(noteId: string): Promise<CanvasNote | null> {
    try {
      const response = await api.get(`/canvas-notes/${noteId}`);
      return response.data;
    } catch (err: any) {
      if (err?.response?.status === 404) return null;
      throw err;
    }
  },

  /**
   * List notes for a notebook (used on app load to restore canvas state).
   * If notebookId is omitted, returns all notes across all notebooks.
   */
  async list(notebookId?: string | null): Promise<CanvasNote[]> {
    const params = notebookId ? { notebook_id: notebookId } : {};
    const response = await api.get('/canvas-notes', { params });
    return response.data || [];
  },

  /**
   * Partial update — called by the 500ms auto-save debounce.
   * Only the fields present in the payload are changed.
   */
  async update(noteId: string, payload: NoteUpdatePayload): Promise<CanvasNote> {
    const response = await api.patch(`/canvas-notes/${noteId}`, payload);
    return response.data;
  },

  /**
   * Delete a canvas note (on canvas item removal / explicit discard).
   */
  async delete(noteId: string): Promise<void> {
    await api.delete(`/canvas-notes/${noteId}`);
  },

  /**
   * Delete all canvas notes for a notebook (on notebook deletion).
   */
  async deleteAllForNotebook(notebookId: string): Promise<void> {
    await api.delete('/canvas-notes', { params: { notebook_id: notebookId } });
  },

  /**
   * Search notes and sources for wikilink autocomplete.
   */
  async searchEntities(query: string, notebookId?: string | null): Promise<SearchResult[]> {
    const params: any = { q: query };
    if (notebookId) params.notebook_id = notebookId;
    const response = await api.get('/canvas-notes', { params });
    return response.data || [];
  },

  /**
   * Get all notes that link to this note via wikilinks_out.
   */
  async getBacklinks(noteId: string): Promise<SearchResult[]> {
    const response = await api.get(`/canvas-notes/${noteId}/backlinks`);
    return response.data || [];
  },
};
