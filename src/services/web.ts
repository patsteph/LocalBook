/**
 * Web search and scraping service
 */

import { API_BASE_URL } from './api';

const API_BASE = API_BASE_URL;

export interface WebSearchResult {
    title: string;
    snippet: string;
    url: string;
}

export interface WebSearchResponse {
    query: string;
    results: WebSearchResult[];
    count: number;
    offset: number;
    has_more: boolean;
}

export interface ScrapedContent {
    success: boolean;
    url: string;
    title?: string;
    author?: string;
    date?: string;
    text?: string;
    word_count?: number;
    char_count?: number;
    error?: string;
}

export interface WebScrapeResponse {
    results: ScrapedContent[];
    successful_count: number;
    failed_count: number;
}

export const webService = {
    /**
     * Search the web for a query with pagination
     */
    async search(query: string, maxResults: number = 20, offset: number = 0): Promise<WebSearchResponse> {
        const response = await fetch(`${API_BASE}/web/search`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                query,
                max_results: maxResults,
                offset,
            }),
        });

        if (!response.ok) {
            throw new Error(`Search failed: ${response.statusText}`);
        }

        return response.json();
    },

    /**
     * Get previously scraped web sources for a notebook
     */
    async getWebSources(notebookId: string): Promise<{ sources: Array<{ id: string; title: string; url: string; word_count: number; date_added: string; type: string }>; count: number }> {
        const response = await fetch(`${API_BASE}/web/sources/${notebookId}`);

        if (!response.ok) {
            throw new Error(`Failed to get web sources: ${response.statusText}`);
        }

        return response.json();
    },

    /**
     * Scrape multiple URLs (with timeout)
     */
    async scrape(urls: string[]): Promise<WebScrapeResponse> {
        // Create abort controller for timeout
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 60000); // 60s timeout
        
        try {
            const response = await fetch(`${API_BASE}/web/scrape`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    urls,
                }),
                signal: controller.signal,
            });

            clearTimeout(timeoutId);

            if (!response.ok) {
                throw new Error(`Scrape failed: ${response.statusText}`);
            }

            return response.json();
        } catch (error: any) {
            clearTimeout(timeoutId);
            if (error.name === 'AbortError') {
                throw new Error('Scraping timed out after 60 seconds');
            }
            throw error;
        }
    },

    /**
     * Quick add a URL to notebook - returns INSTANTLY
     * Scraping and ingestion happen in background, UI updates via WebSocket
     */
    async quickAdd(notebookId: string, url: string, title: string): Promise<{ source_id: string; status: string }> {
        const response = await fetch(`${API_BASE}/web/quick-add`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                notebook_id: notebookId,
                url,
                title,
            }),
        });

        if (!response.ok) {
            throw new Error(`Failed to add: ${response.statusText}`);
        }

        return response.json();
    },

    /**
     * Add scraped content to a notebook (with timeout)
     * @deprecated Use quickAdd instead for instant response
     */
    async addToNotebook(
        notebookId: string,
        urls: string[],
        scrapedContent: ScrapedContent[]
    ): Promise<any> {
        // Create abort controller for timeout (120s for RAG ingestion)
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 120000);
        
        try {
            const response = await fetch(`${API_BASE}/web/add-to-notebook`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    notebook_id: notebookId,
                    urls,
                    scraped_content: scrapedContent,
                }),
                signal: controller.signal,
            });

            clearTimeout(timeoutId);

            if (!response.ok) {
                throw new Error(`Add to notebook failed: ${response.statusText}`);
            }

            return response.json();
        } catch (error: any) {
            clearTimeout(timeoutId);
            if (error.name === 'AbortError') {
                throw new Error('Adding to notebook timed out');
            }
            throw error;
        }
    },
};
