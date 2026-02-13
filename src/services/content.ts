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
    style?: string;  // Output style: professional, casual, academic, etc.
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

        // Helper: check page break
        const checkPage = (needed: number) => {
            if (y > pageHeight - margin - needed) {
                doc.addPage();
                y = margin;
            }
        };

        // Helper: render a text line with inline **bold** and *italic* spans
        const renderRichLine = (text: string, x: number, currentY: number, fontSize: number) => {
            doc.setFontSize(fontSize);
            // Split into segments: **bold**, *italic*, and normal
            const parts: { text: string; style: 'normal' | 'bold' | 'italic' }[] = [];
            let remaining = text;
            while (remaining.length > 0) {
                // Match **bold** first, then *italic*
                const boldMatch = remaining.match(/\*\*(.+?)\*\*/);
                const italicMatch = remaining.match(/(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)/);
                
                let earliest = null;
                let earliestIdx = remaining.length;
                
                if (boldMatch && boldMatch.index !== undefined && boldMatch.index < earliestIdx) {
                    earliest = { match: boldMatch, style: 'bold' as const };
                    earliestIdx = boldMatch.index;
                }
                if (italicMatch && italicMatch.index !== undefined && italicMatch.index < earliestIdx) {
                    earliest = { match: italicMatch, style: 'italic' as const };
                    earliestIdx = italicMatch.index;
                }
                
                if (earliest) {
                    if (earliestIdx > 0) {
                        parts.push({ text: remaining.slice(0, earliestIdx), style: 'normal' });
                    }
                    parts.push({ text: earliest.match[1], style: earliest.style });
                    remaining = remaining.slice(earliestIdx + earliest.match[0].length);
                } else {
                    parts.push({ text: remaining, style: 'normal' });
                    remaining = '';
                }
            }
            
            // Render parts sequentially
            let cx = x;
            for (const part of parts) {
                doc.setFont('helvetica', part.style);
                // Wrap long segments
                const segLines = doc.splitTextToSize(part.text, maxWidth - (cx - margin));
                for (let i = 0; i < segLines.length; i++) {
                    if (i > 0) {
                        currentY += fontSize * 0.5;
                        checkPage(fontSize * 0.5);
                        cx = x;
                    }
                    doc.text(segLines[i], cx, currentY);
                    cx += doc.getTextWidth(segLines[i]);
                }
            }
            doc.setFont('helvetica', 'normal');
            return currentY;
        };

        // --- Title ---
        doc.setFontSize(20);
        doc.setFont('helvetica', 'bold');
        doc.text(title, margin, margin + 10);
        doc.setDrawColor(100, 100, 100);
        doc.line(margin, margin + 14, pageWidth - margin, margin + 14);
        let y = margin + 24;

        // Parse markdown line by line
        const rawLines = content.split('\n');

        for (let i = 0; i < rawLines.length; i++) {
            const line = rawLines[i];

            // --- Horizontal rule ---
            if (/^---+$/.test(line.trim()) || /^\*\*\*+$/.test(line.trim())) {
                checkPage(10);
                y += 4;
                doc.setDrawColor(180, 180, 180);
                doc.line(margin, y, pageWidth - margin, y);
                y += 8;
                continue;
            }

            // --- Headings ---
            const h1 = line.match(/^#\s+(.+)/);
            const h2 = line.match(/^##\s+(.+)/);
            const h3 = line.match(/^###\s+(.+)/);
            const h4 = line.match(/^####\s+(.+)/);

            if (h1 || h2 || h3 || h4) {
                const heading = h4 ? h4[1] : h3 ? h3[1] : h2 ? h2[1] : h1![1];
                const fontSize = h4 ? 12 : h3 ? 13 : h2 ? 15 : 17;
                checkPage(fontSize + 4);
                y += h1 ? 8 : h2 ? 6 : 4;
                // Strip inline markdown from heading
                const cleanHeading = heading.replace(/\*\*(.+?)\*\*/g, '$1').replace(/\*(.+?)\*/g, '$1');
                doc.setFontSize(fontSize);
                doc.setFont('helvetica', 'bold');
                const wrapped = doc.splitTextToSize(cleanHeading, maxWidth);
                for (const wl of wrapped) {
                    checkPage(fontSize * 0.5);
                    doc.text(wl, margin, y);
                    y += fontSize * 0.5;
                }
                doc.setFont('helvetica', 'normal');
                y += 2;
                continue;
            }

            // --- Empty line ---
            if (line.trim() === '') {
                y += 3;
                continue;
            }

            // --- List items (- or *) ---
            const listMatch = line.match(/^(\s*)([-*])\s+(.+)/);
            if (listMatch) {
                const indent = Math.min(listMatch[1].length, 8);
                const itemText = listMatch[3];
                const bulletX = margin + 4 + indent * 2;
                checkPage(7);
                doc.setFontSize(11);
                doc.setFont('helvetica', 'normal');
                doc.text('•', bulletX, y);
                y = renderRichLine(itemText, bulletX + 5, y, 11);
                y += 6;
                continue;
            }

            // --- Normal paragraph text ---
            checkPage(7);
            y = renderRichLine(line, margin, y, 11);
            y += 6;
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
