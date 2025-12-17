/**
 * Content Generation Service - Text-based skill outputs
 */
import { API_BASE_URL } from './api';
import jsPDF from 'jspdf';
import { save } from '@tauri-apps/plugin-dialog';
import { writeFile } from '@tauri-apps/plugin-fs';

export interface ContentGenerateRequest {
    notebook_id: string;
    skill_id: string;
    topic?: string;
}

export interface ContentGenerateResponse {
    notebook_id: string;
    skill_id: string;
    skill_name: string;
    content: string;
    sources_used: number;
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
        const response = await fetch(`${API_BASE_URL}/content/generate`, {
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
     * Generate content with streaming for real-time display
     */
    async *generateStream(request: ContentGenerateRequest): AsyncGenerator<string, void, unknown> {
        const response = await fetch(`${API_BASE_URL}/content/generate/stream`, {
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

        const reader = response.body?.getReader();
        if (!reader) throw new Error('No response body');

        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    const data = line.slice(6);
                    if (data === '[DONE]') return;
                    try {
                        const parsed = JSON.parse(data);
                        if (parsed.content) {
                            yield parsed.content;
                        }
                    } catch {
                        // Ignore parse errors
                    }
                }
            }
        }
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
        const response = await fetch(`${API_BASE_URL}/content/list/${notebookId}`);
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
        const response = await fetch(`${API_BASE_URL}/content/${contentId}`);
        if (!response.ok) {
            throw new Error('Failed to get content generation');
        }
        return response.json();
    },

    /**
     * Delete a content generation
     */
    async delete(contentId: string): Promise<void> {
        const response = await fetch(`${API_BASE_URL}/content/${contentId}`, {
            method: 'DELETE',
        });
        if (!response.ok) {
            throw new Error('Failed to delete content generation');
        }
    },

    /**
     * Download content as PDF using Tauri's native file save dialog
     */
    async downloadAsPDF(content: string, title: string, filename: string): Promise<string | null> {
        const doc = new jsPDF();
        const pageWidth = doc.internal.pageSize.getWidth();
        const pageHeight = doc.internal.pageSize.getHeight();
        const margin = 20;
        const maxWidth = pageWidth - margin * 2;
        const lineHeight = 7;
        
        // Add title
        doc.setFontSize(18);
        doc.setFont('helvetica', 'bold');
        doc.text(title, margin, margin + 10);
        
        // Add content
        doc.setFontSize(11);
        doc.setFont('helvetica', 'normal');
        
        // Split content into lines that fit the page width
        const lines = doc.splitTextToSize(content, maxWidth);
        
        let y = margin + 25;
        for (const line of lines) {
            if (y > pageHeight - margin) {
                doc.addPage();
                y = margin;
            }
            doc.text(line, margin, y);
            y += lineHeight;
        }
        
        // Use Tauri's native file save dialog
        try {
            const path = await save({
                defaultPath: `${filename}.pdf`,
                filters: [{
                    name: 'PDF Document',
                    extensions: ['pdf']
                }]
            });
            
            if (!path) {
                console.log('User cancelled PDF save');
                return null;
            }
            
            // Get PDF as array buffer and write to file
            const pdfOutput = doc.output('arraybuffer');
            const uint8Array = new Uint8Array(pdfOutput);
            await writeFile(path, uint8Array);
            
            console.log(`PDF saved successfully to: ${path}`);
            return path;
        } catch (error) {
            console.error('Failed to save PDF:', error);
            throw new Error(`Failed to save PDF: ${error instanceof Error ? error.message : 'Unknown error'}`);
        }
    },
};
