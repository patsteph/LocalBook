/**
 * Web Search Results Component
 * Allows users to search, preview, and scrape web content
 */

import React, { useState, useEffect } from 'react';
import { webService, WebSearchResult, ScrapedContent } from '../services/web';

interface WebSearchResultsProps {
    notebookId: string;
    onContentScraped?: (content: ScrapedContent[]) => void;
    onAddedToNotebook?: () => void;
    initialQuery?: string;
}

export const WebSearchResults: React.FC<WebSearchResultsProps> = ({
    notebookId,
    onContentScraped,
    onAddedToNotebook,
    initialQuery = '',
}) => {
    const [query, setQuery] = useState(initialQuery);
    
    // Update query when initialQuery changes (e.g., from chat low confidence)
    useEffect(() => {
        if (initialQuery) {
            setQuery(initialQuery);
        }
    }, [initialQuery]);
    const [results, setResults] = useState<WebSearchResult[]>([]);
    const [selectedUrls, setSelectedUrls] = useState<Set<string>>(new Set());
    const [scrapedContent, setScrapedContent] = useState<ScrapedContent[]>([]);
    const [isSearching, setIsSearching] = useState(false);
    const [isAddingToNotebook, setIsAddingToNotebook] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const isUrl = (text: string): boolean => {
        try {
            new URL(text);
            return true;
        } catch {
            return false;
        }
    };

    const handleSearch = async () => {
        if (!query.trim()) {
            setError('Please enter a search query or URL');
            return;
        }

        setIsSearching(true);
        setError(null);
        setResults([]);
        setSelectedUrls(new Set());
        setScrapedContent([]);

        try {
            // Check if input is a URL
            if (isUrl(query.trim())) {
                // Direct URL - add to results immediately, pre-selected
                const url = query.trim();
                const isYouTube = url.includes('youtube.com') || url.includes('youtu.be');
                setResults([{
                    title: isYouTube ? 'YouTube Video' : 'Direct URL',
                    snippet: isYouTube ? 'YouTube video transcript will be extracted' : 'Content will be scraped from this URL',
                    url: url
                }]);
                setSelectedUrls(new Set([url]));
            } else {
                // Regular web search
                const response = await webService.search(query, 20);
                setResults(response.results);
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Search failed');
        } finally {
            setIsSearching(false);
        }
    };

    const handleToggleUrl = (url: string) => {
        const newSelected = new Set(selectedUrls);
        if (newSelected.has(url)) {
            newSelected.delete(url);
        } else {
            newSelected.add(url);
        }
        setSelectedUrls(newSelected);
    };

    const handleSelectAll = () => {
        if (selectedUrls.size === results.length) {
            setSelectedUrls(new Set());
        } else {
            setSelectedUrls(new Set(results.map(r => r.url)));
        }
    };

    const handleAddToNotebook = async () => {
        if (selectedUrls.size === 0) {
            setError('Please select at least one result');
            return;
        }

        setIsAddingToNotebook(true);
        setError(null);

        try {
            // Step 1: Scrape the selected URLs
            const response = await webService.scrape(Array.from(selectedUrls));
            setScrapedContent(response.results);

            // Step 2: Add successful scrapes to notebook
            const successfulScrapes = response.results.filter(c => c.success);

            if (successfulScrapes.length === 0) {
                setError('Failed to scrape any content. Please try again.');
                return;
            }

            await webService.addToNotebook(
                notebookId,
                Array.from(selectedUrls),
                successfulScrapes
            );

            if (onAddedToNotebook) {
                onAddedToNotebook();
            }

            // Reset state after successful addition
            setResults([]);
            setSelectedUrls(new Set());
            setScrapedContent([]);
            setQuery('');
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to add to notebook');
        } finally {
            setIsAddingToNotebook(false);
        }
    };

    return (
        <div className="flex flex-col h-full bg-white dark:bg-gray-800 rounded-lg shadow-lg">
            {/* Header */}
            <div className="p-4 border-b border-gray-200 dark:border-gray-700">
                <h2 className="text-lg font-semibold text-gray-900 dark:text-white mb-2">
                    Web Research
                </h2>
                <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
                    Search the web or paste a URL (web pages, YouTube videos)
                </p>

                {/* Search Input */}
                <div className="flex gap-2">
                    <div className="flex-1 relative">
                        <input
                            type="text"
                            value={query}
                            onChange={(e) => setQuery(e.target.value)}
                            onKeyPress={(e) => e.key === 'Enter' && handleSearch()}
                            placeholder="Search or paste URL (web page/YouTube)..."
                            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-700 text-gray-900 dark:text-white text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                            disabled={isSearching}
                        />
                        {query.trim() && isUrl(query.trim()) && (
                            <span className="absolute right-3 top-2.5 text-xs bg-blue-100 dark:bg-blue-900 text-blue-700 dark:text-blue-300 px-2 py-0.5 rounded">
                                URL
                            </span>
                        )}
                    </div>
                    <button
                        onClick={handleSearch}
                        disabled={isSearching}
                        className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-400 text-white rounded-lg text-sm font-medium transition-colors"
                    >
                        {isSearching ? 'Processing...' : (isUrl(query.trim()) ? 'Add URL' : 'Search')}
                    </button>
                </div>

                {/* Error Message */}
                {error && (
                    <div className="mt-2 p-2 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded text-sm text-red-700 dark:text-red-400">
                        {error}
                    </div>
                )}
            </div>

            {/* Results */}
            <div className="flex-1 overflow-y-auto p-4">
                {results.length > 0 && (
                    <>
                        {/* Action Bar */}
                        <div className="flex items-center justify-between mb-4 p-3 bg-gray-50 dark:bg-gray-750 rounded-lg">
                            <div className="flex items-center gap-3">
                                <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        checked={selectedUrls.size === results.length}
                                        onChange={handleSelectAll}
                                        className="rounded border-gray-300 dark:border-gray-600"
                                    />
                                    <span>
                                        {selectedUrls.size === results.length ? 'Deselect All' : 'Select All'}
                                    </span>
                                </label>
                                <span className="text-xs text-gray-500 dark:text-gray-400">
                                    ({selectedUrls.size} of {results.length} selected)
                                </span>
                            </div>

                            <button
                                onClick={handleAddToNotebook}
                                disabled={selectedUrls.size === 0 || isAddingToNotebook}
                                className="px-4 py-2 bg-blue-600 hover:bg-blue-700 disabled:bg-gray-400 text-white rounded text-sm font-medium transition-colors"
                            >
                                {isAddingToNotebook ? 'Adding to Notebook...' : 'Add to Notebook'}
                            </button>
                        </div>

                        {/* Search Results List */}
                        <div className="space-y-2">
                            {results.map((result, idx) => (
                                <label
                                    key={idx}
                                    className="flex gap-3 p-3 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-750 cursor-pointer transition-colors"
                                >
                                    <input
                                        type="checkbox"
                                        checked={selectedUrls.has(result.url)}
                                        onChange={() => handleToggleUrl(result.url)}
                                        className="mt-1 rounded border-gray-300 dark:border-gray-600"
                                    />
                                    <div className="flex-1 min-w-0">
                                        <p className="font-medium text-blue-700 dark:text-blue-400 text-sm mb-1">
                                            {result.title}
                                        </p>
                                        <p className="text-xs text-gray-600 dark:text-gray-400 mb-1">
                                            {result.snippet}
                                        </p>
                                        <p className="text-xs text-gray-500 dark:text-gray-500 truncate">
                                            {result.url}
                                        </p>
                                    </div>
                                </label>
                            ))}
                        </div>
                    </>
                )}

                {/* Empty State */}
                {results.length === 0 && !isSearching && (
                    <div className="flex flex-col items-center justify-center h-full text-gray-400 dark:text-gray-500">
                        <svg
                            className="w-16 h-16 mb-4"
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                        >
                            <path
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                strokeWidth={2}
                                d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
                            />
                        </svg>
                        <p className="text-sm font-medium mb-2">Search the web or scrape a URL</p>
                        <p className="text-xs text-center max-w-xs">
                            Enter keywords to search, or paste a URL to scrape content from web pages or extract YouTube video transcripts
                        </p>
                    </div>
                )}
            </div>

            {/* Scraped Content Preview */}
            {scrapedContent.length > 0 && (
                <div className="border-t border-gray-200 dark:border-gray-700 p-4 bg-gray-50 dark:bg-gray-750">
                    <h3 className="text-sm font-semibold text-gray-900 dark:text-white mb-2">
                        Scraped Content ({scrapedContent.filter(c => c.success).length} successful)
                    </h3>
                    <div className="max-h-40 overflow-y-auto space-y-2">
                        {scrapedContent.map((content, idx) => (
                            <div
                                key={idx}
                                className={`p-2 rounded text-xs ${
                                    content.success
                                        ? 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400'
                                        : 'bg-red-50 dark:bg-red-900/20 text-red-700 dark:text-red-400'
                                }`}
                            >
                                <p className="font-medium">{content.title || content.url}</p>
                                {content.success && (
                                    <p className="text-xs opacity-75 mt-1">
                                        {content.word_count} words extracted
                                    </p>
                                )}
                                {!content.success && (
                                    <p className="text-xs opacity-75 mt-1">Error: {content.error}</p>
                                )}
                            </div>
                        ))}
                    </div>
                </div>
            )}
        </div>
    );
};
