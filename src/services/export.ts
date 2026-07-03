/**
 * Export service for downloading notebooks
 */

import { save } from '@tauri-apps/plugin-dialog';
import { writeFile } from '@tauri-apps/plugin-fs';
import { API_BASE_URL, localFetch } from './api';
import type { CanvasItem } from '../components/canvas/types';

// ── Phase 5 — unified artifact export ─────────────────────────────────────
export type ArtifactDownloadFormat = 'png' | 'pdf' | 'html';

export interface ArtifactEnvelope {
    id: string;
    type: string;
    payload: unknown;
    title?: string;
    tagline?: string;
    metadata?: Record<string, unknown>;
}

/** Map a CanvasItem into the Artifact envelope the backend renderer expects.
 *  Mirrors the dispatch branches inside CanvasItemCard. Returns null for
 *  item types that aren't Artifact-renderable for export (audio / video /
 *  quiz / flashcards). */
export function canvasItemToArtifact(item: CanvasItem): ArtifactEnvelope | null {
    const base = { id: item.id, title: item.title };
    switch (item.type) {
        case 'document':
        case 'chat-response':
        case 'note':
            return item.content ? { ...base, type: 'markdown', payload: item.content } : null;
        case 'html':
            return item.content ? { ...base, type: 'html', payload: item.content } : null;
        case 'visual':
            if (!item.content) return null;
            return item.content.trimStart().startsWith('<svg')
                ? { ...base, type: 'svg', payload: item.content }
                : { ...base, type: 'mermaid', payload: item.content };
        case 'comparison':
            return item.metadata?.comparison
                ? { ...base, type: 'json:comparison', payload: item.metadata.comparison }
                : null;
        default:
            return null;
    }
}

const API_BASE = API_BASE_URL;

export interface ExportFormat {
    id: string;
    name: string;
    extension: string;
    description: string;
}

export interface ExportOptions {
    notebookId: string;
    format: 'markdown' | 'html' | 'pdf' | 'pptx';
    includeSourcesContent?: boolean;
    pptxTheme?: 'light' | 'dark' | 'corporate' | 'academic';
    chatHistory?: Array<{
        question: string;
        answer: string;
        citations?: any[];
        timestamp?: string;
    }>;
}

export interface SlideData {
    title: string;
    bullets: string[];
    slide_type: 'title' | 'content' | 'sources' | 'qa' | 'thankyou' | 'visual_overview';
    mermaid_code?: string;
}

export const exportService = {
    /**
     * Get available export formats
     */
    async getAvailableFormats(): Promise<ExportFormat[]> {
        const response = await localFetch(`${API_BASE}/export/formats`);

        if (!response.ok) {
            throw new Error(`Failed to get export formats: ${response.statusText}`);
        }

        const data = await response.json();
        return data.formats;
    },

    /**
     * Export a notebook to the specified format
     * Returns a Blob that can be downloaded
     */
    async exportNotebook(options: ExportOptions): Promise<Blob> {
        const response = await localFetch(`${API_BASE}/export/notebook`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                notebook_id: options.notebookId,
                format: options.format,
                include_sources_content: options.includeSourcesContent || false,
                chat_history: options.chatHistory || null,
                pptx_theme: options.pptxTheme || 'light',
            }),
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: response.statusText }));
            throw new Error(error.detail || `Failed to export notebook: ${response.statusText}`);
        }

        return response.blob();
    },

    /**
     * Trigger download of exported content using Tauri's native file save dialog
     * Returns the path where the file was saved, or null if cancelled
     */
    async downloadBlob(blob: Blob, filename: string): Promise<string | null> {
        try {
            // Show save dialog and get the path
            const path = await save({
                defaultPath: filename,
                filters: [{
                    name: 'Export File',
                    extensions: [filename.split('.').pop() || '*']
                }]
            });

            // If user cancelled, path will be null
            if (!path) {
                return null;
            }

            // Convert blob to Uint8Array
            const arrayBuffer = await blob.arrayBuffer();
            const uint8Array = new Uint8Array(arrayBuffer);

            // Write file using Tauri's fs API
            await writeFile(path, uint8Array);

            return path;
        } catch (error) {
            console.error('Failed to save file:', error);
            throw new Error(`Failed to save file: ${error instanceof Error ? error.message : 'Unknown error'}`);
        }
    },

    /**
     * Generate PPTX slide preview as JSON for the revision UI
     */
    async previewPptxSlides(notebookId: string, theme?: string): Promise<{ slides: SlideData[]; theme: string }> {
        const response = await localFetch(`${API_BASE}/export/pptx/preview`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ notebook_id: notebookId, pptx_theme: theme || 'light' }),
        });
        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: response.statusText }));
            throw new Error(error.detail || 'Failed to generate slide preview');
        }
        return response.json();
    },

    /**
     * Revise slides using a natural language prompt
     */
    async revisePptxSlides(notebookId: string, slides: SlideData[], revisionPrompt: string, theme?: string): Promise<{ slides: SlideData[]; theme: string }> {
        const response = await localFetch(`${API_BASE}/export/pptx/revise`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                notebook_id: notebookId,
                slides,
                revision_prompt: revisionPrompt,
                pptx_theme: theme || 'light',
            }),
        });
        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: response.statusText }));
            throw new Error(error.detail || 'Failed to revise slides');
        }
        return response.json();
    },

    /**
     * Download finalized slides as .pptx file
     */
    async downloadPptxSlides(notebookId: string, slides: SlideData[], theme?: string): Promise<Blob> {
        const response = await localFetch(`${API_BASE}/export/pptx/download`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                notebook_id: notebookId,
                slides,
                pptx_theme: theme || 'light',
            }),
        });
        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: response.statusText }));
            throw new Error(error.detail || 'Failed to download slides');
        }
        return response.blob();
    },

    /**
     * Render a Mermaid diagram to a base64 PNG data URL for preview
     */
    async renderDiagramPreview(mermaidCode: string): Promise<{ success: boolean; image?: string; error?: string }> {
        const response = await localFetch(`${API_BASE}/export/pptx/render-diagram`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mermaid_code: mermaidCode }),
        });
        if (!response.ok) {
            return { success: false, error: response.statusText };
        }
        return response.json();
    },


    // === Custom PPTX template management ===

    async listTemplates(): Promise<{ id: string; name: string; filename: string; size: number; uploaded: string }[]> {
        const response = await localFetch(`${API_BASE}/export/templates`);
        if (!response.ok) return [];
        const data = await response.json();
        return data.templates || [];
    },

    async uploadTemplate(file: File, name: string): Promise<{ id: string; name: string }> {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('name', name);
        const response = await localFetch(`${API_BASE}/export/templates`, {
            method: 'POST',
            body: formData,
        });
        if (!response.ok) {
            const err = await response.json().catch(() => ({ detail: 'Upload failed' }));
            throw new Error(err.detail || 'Upload failed');
        }
        return response.json();
    },

    async deleteTemplate(templateId: string): Promise<void> {
        const response = await localFetch(`${API_BASE}/export/templates/${templateId}`, { method: 'DELETE' });
        if (!response.ok) throw new Error('Failed to delete template');
    },

    /**
     * Phase 5 — render an Artifact envelope to PNG / PDF / HTML via the
     * unified backend pipeline and trigger a browser download.
     */
    async downloadArtifact(
        artifact: ArtifactEnvelope,
        format: ArtifactDownloadFormat,
        filename?: string,
    ): Promise<void> {
        const response = await localFetch(`${API_BASE}/export/artifact`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ artifact, format, filename }),
        });
        if (!response.ok) {
            throw new Error(`Artifact export failed: HTTP ${response.status}`);
        }
        const blob = await response.blob();
        const ext = format === 'pdf' ? 'pdf' : format === 'png' ? 'png' : 'html';
        const safe = (filename || artifact.title || 'artifact')
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, '-')
            .replace(/^-|-$/g, '') || 'artifact';
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${safe}.${ext}`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    },
};
