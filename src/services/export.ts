/**
 * Export service for downloading notebooks
 */

import { save } from '@tauri-apps/plugin-dialog';
import { writeFile } from '@tauri-apps/plugin-fs';
import { jsPDF } from 'jspdf';
import { API_BASE_URL } from './api';

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
        const response = await fetch(`${API_BASE}/export/formats`);

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
        const response = await fetch(`${API_BASE}/export/notebook`, {
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
     * Generate a PDF directly using jsPDF
     * Returns a Blob containing the PDF data
     */
    /**
     * Generate PPTX slide preview as JSON for the revision UI
     */
    async previewPptxSlides(notebookId: string, theme?: string): Promise<{ slides: SlideData[]; theme: string }> {
        const response = await fetch(`${API_BASE}/export/pptx/preview`, {
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
        const response = await fetch(`${API_BASE}/export/pptx/revise`, {
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
        const response = await fetch(`${API_BASE}/export/pptx/download`, {
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
        const response = await fetch(`${API_BASE}/export/pptx/render-diagram`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mermaid_code: mermaidCode }),
        });
        if (!response.ok) {
            return { success: false, error: response.statusText };
        }
        return response.json();
    },

    async generatePDF(notebookTitle: string, sources: any[], chatHistory?: any[]): Promise<Blob> {
        const doc = new jsPDF();
        const pageWidth = doc.internal.pageSize.getWidth();
        const margin = 20;
        const maxWidth = pageWidth - (margin * 2);
        let yPosition = 20;

        // Helper function to add text with automatic page breaks
        const addText = (text: string, fontSize: number = 12, isBold: boolean = false) => {
            doc.setFontSize(fontSize);
            if (isBold) {
                doc.setFont('helvetica', 'bold');
            } else {
                doc.setFont('helvetica', 'normal');
            }

            const lines = doc.splitTextToSize(text, maxWidth);
            for (const line of lines) {
                if (yPosition > 270) { // Near bottom of page
                    doc.addPage();
                    yPosition = 20;
                }
                doc.text(line, margin, yPosition);
                yPosition += fontSize * 0.5;
            }
        };

        // Title
        addText(notebookTitle, 24, true);
        yPosition += 10;

        // Timestamp
        doc.setFontSize(10);
        doc.setTextColor(100);
        doc.text(`Exported: ${new Date().toLocaleString()}`, margin, yPosition);
        yPosition += 15;
        doc.setTextColor(0);

        // Sources Section
        addText('📚 Sources', 18, true);
        yPosition += 5;

        if (sources && sources.length > 0) {
            addText(`Total Sources: ${sources.length}`, 12, false);
            yPosition += 5;

            sources.forEach((source, index) => {
                if (yPosition > 250) {
                    doc.addPage();
                    yPosition = 20;
                }

                addText(`${index + 1}. ${source.filename || 'Unknown'}`, 14, true);
                yPosition += 2;

                doc.setFontSize(10);
                doc.setTextColor(80);
                doc.text(`Format: ${(source.format || 'unknown').toUpperCase()}`, margin + 10, yPosition);
                yPosition += 5;
                doc.text(`Chunks: ${source.chunks || 0} | Characters: ${(source.characters || 0).toLocaleString()}`, margin + 10, yPosition);
                yPosition += 5;
                doc.text(`Status: ${source.status || 'unknown'}`, margin + 10, yPosition);
                yPosition += 8;
                doc.setTextColor(0);
            });
        } else {
            addText('No sources in this notebook', 12, false);
            yPosition += 5;
        }

        yPosition += 10;

        // Chat History Section
        if (chatHistory && chatHistory.length > 0) {
            addText('💬 Q&A History', 18, true);
            yPosition += 5;

            chatHistory.forEach((exchange, index) => {
                if (yPosition > 230) {
                    doc.addPage();
                    yPosition = 20;
                }

                addText(`Q${index + 1}: ${exchange.question || ''}`, 12, true);
                yPosition += 3;

                if (exchange.timestamp) {
                    doc.setFontSize(9);
                    doc.setTextColor(100);
                    doc.text(`Asked: ${exchange.timestamp}`, margin + 5, yPosition);
                    yPosition += 5;
                    doc.setTextColor(0);
                }

                addText('Answer:', 11, true);
                yPosition += 2;
                addText(exchange.answer || '', 10, false);
                yPosition += 5;

                if (exchange.citations && exchange.citations.length > 0) {
                    addText('Citations:', 10, true);
                    yPosition += 2;
                    exchange.citations.forEach((citation: any) => {
                        const citationText = `[${citation.number}] ${citation.filename}: ${(citation.snippet || '').substring(0, 100)}...`;
                        doc.setFontSize(9);
                        const citationLines = doc.splitTextToSize(citationText, maxWidth - 10);
                        citationLines.forEach((line: string) => {
                            if (yPosition > 270) {
                                doc.addPage();
                                yPosition = 20;
                            }
                            doc.text(line, margin + 5, yPosition);
                            yPosition += 4;
                        });
                    });
                    yPosition += 3;
                }

                yPosition += 5;
            });
        }

        // Footer
        const pageCount = doc.getNumberOfPages();
        doc.setFontSize(8);
        doc.setTextColor(150);
        for (let i = 1; i <= pageCount; i++) {
            doc.setPage(i);
            doc.text('Generated by LocalBook', pageWidth / 2, 285, { align: 'center' });
            doc.text(`Page ${i} of ${pageCount}`, pageWidth - margin, 285, { align: 'right' });
        }

        return doc.output('blob');
    },
};
