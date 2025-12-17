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
     * Scrape multiple URLs
     */
    async scrape(urls: string[]): Promise<WebScrapeResponse> {
        const response = await fetch(`${API_BASE}/web/scrape`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                urls,
            }),
        });

        if (!response.ok) {
            throw new Error(`Scrape failed: ${response.statusText}`);
        }

        return response.json();
    },

    /**
     * Add scraped content to a notebook
     */
    async addToNotebook(
        notebookId: string,
        urls: string[],
        scrapedContent: ScrapedContent[]
    ): Promise<any> {
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
        });

        if (!response.ok) {
            throw new Error(`Add to notebook failed: ${response.statusText}`);
        }

        return response.json();
    },
};
