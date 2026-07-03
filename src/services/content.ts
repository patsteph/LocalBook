/**
 * Content Generation Service - Text-based skill outputs
 */
import { API_BASE_URL, localFetch } from './api';

export interface ContentGenerateRequest {
    notebook_id: string;
    skill_id: string;
    topic?: string;
    style?: string;  // Output style: professional, casual, academic, etc.
    chat_context?: string;  // Recent chat conversation for "From Chat" mode
    // Tier 4 voice register override. Backend picks the per-doc-type default
    // when omitted. Valid: measured / engaged / warm / urgent.
    register?: string;
    // Cross-medium visuals (2026-07-01) — when false, suppress VISUAL_INTERLEAVE
    // so the doc is pure prose. Defaults to true server-side (v2.0 behavior).
    include_visuals?: boolean;
}

export interface ContentGenerateResponse {
    notebook_id: string;
    skill_id: string;
    skill_name: string;
    content: string;
    sources_used: number;
    source_names: string[];
    relevance_scores: Record<string, number>;
}

export interface ContentGeneration {
    content_id: string;
    notebook_id: string;
    skill_id: string;
    skill_name: string;
    content: string;
    topic?: string;
    sources_used: number;
    created_at: string;
    updated_at: string;
}

export const contentService = {
    /**
     * Generate content using a skill (non-streaming)
     */
    async generate(request: ContentGenerateRequest): Promise<ContentGenerateResponse> {
        const response = await localFetch(`${API_BASE_URL}/content/generate`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(request),
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({ detail: 'Generation failed' }));
            throw new Error(error.detail || 'Generation failed');
        }

        return response.json();
    },


    /**
     * Export content to markdown format
     */
    exportMarkdown(content: string, title: string): string {
        return `# ${title}\n\n${content}`;
    },

    /**
     * Download content as a file
     */
    downloadAsFile(content: string, filename: string, type: 'md' | 'txt' = 'md') {
        const mimeType = type === 'md' ? 'text/markdown' : 'text/plain';
        const blob = new Blob([content], { type: mimeType });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${filename}.${type}`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    },

    /**
     * List all content generations for a notebook
     */
    async list(notebookId: string): Promise<ContentGeneration[]> {
        const response = await localFetch(`${API_BASE_URL}/content/list/${notebookId}`);
        if (!response.ok) {
            throw new Error('Failed to list content generations');
        }
        const data = await response.json();
        return data.generations;
    },

    /**
     * Get a specific content generation
     */
    async get(contentId: string): Promise<ContentGeneration> {
        const response = await localFetch(`${API_BASE_URL}/content/${contentId}`);
        if (!response.ok) {
            throw new Error('Failed to get content generation');
        }
        return response.json();
    },

    /**
     * Delete a content generation
     */
    async delete(contentId: string): Promise<void> {
        const response = await localFetch(`${API_BASE_URL}/content/${contentId}`, {
            method: 'DELETE',
        });
        if (!response.ok) {
            throw new Error('Failed to delete content generation');
        }
    },

};
