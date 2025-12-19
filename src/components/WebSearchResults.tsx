/**
 * Web Search Results Component
 * Allows users to search, preview, and scrape web content
 */

import React, { useState, useEffect } from 'react';
import { webService, WebSearchResult, ScrapedContent } from '../services/web';

interface WebSource {
    id: string;
    title: string;
    url: string;
    word_count: number;
    date_added: string;
    type: string;
}

interface WebSearchResultsProps {
    notebookId: string;
    onContentScraped?: (content: ScrapedContent[]) => void;
    onAddedToNotebook?: () => void;
    onSourceAdded?: () => void;  // Called when a source is added (doesn't close modal)
    initialQuery?: string;
}

export const WebSearchResults: React.FC<WebSearchResultsProps> = ({
    notebookId,
    onContentScraped: _onContentScraped,
    onAddedToNotebook: _onAddedToNotebook,
    onSourceAdded,
    initialQuery = '',
}) => {
    // Note: onContentScraped and onAddedToNotebook are kept in interface for API compatibility
    // but intentionally unused - onSourceAdded is the preferred callback
    void _onContentScraped;
    void _onAddedToNotebook;
    const [query, setQuery] = useState(initialQuery);
    
    // Update query when initialQuery changes (e.g., from chat low confidence)
    useEffect(() => {
        if (initialQuery) {
            setQuery(initialQuery);
        }
    }, [initialQuery]);
    const [results, setResults] = useState<WebSearchResult[]>([]);
    const [scrapedContent, setScrapedContent] = useState<ScrapedContent[]>([]);
    const [isSearching, setIsSearching] = useState(false);
    const [isLoadingMore, setIsLoadingMore] = useState(false);
    const [addingUrls, setAddingUrls] = useState<Set<string>>(new Set());  // Track which URLs are being added
    const [addedUrls, setAddedUrls] = useState<Set<string>>(new Set());    // Track which URLs have been added
    const [error, setError] = useState<string | null>(null);
    const [currentOffset, setCurrentOffset] = useState(0);
    const [hasMore, setHasMore] = useState(false);
    const [lastQuery, setLastQuery] = useState('');
    const [existingSources, setExistingSources] = useState<WebSource[]>([]);
    const [showExisting, setShowExisting] = useState(false);

    // Load existing web sources when component mounts
    useEffect(() => {
        if (notebookId) {
            webService.getWebSources(notebookId)
                .then(data => setExistingSources(data.sources))
                .catch(err => console.error('Failed to load existing sources:', err));
        }
    }, [notebookId]);

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
        setAddedUrls(new Set());
        setScrapedContent([]);
        setCurrentOffset(0);
        setHasMore(false);

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
            } else {
                // Regular web search
                const response = await webService.search(query, 20, 0);
                setResults(response.results);
                setHasMore(response.has_more);
                setCurrentOffset(20);
                setLastQuery(query);
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Search failed');
        } finally {
            setIsSearching(false);
        }
    };

    // Map offset to freshness label for UI (order: initial → pw → pm → py)
    const getFreshnessLabel = (offset: number): string => {
        const labels: Record<number, string> = {
            0: 'All Time',
            20: 'Past Week',
            40: 'Past Month', 
            60: 'Past Year'
        };
        return labels[offset] || '';
    };

    const handleLoadMore = async () => {
        if (!lastQuery || isLoadingMore) return;
        
        setIsLoadingMore(true);
        setError(null);

        try {
            const response = await webService.search(lastQuery, 20, currentOffset);
            // Filter out duplicates by URL
            const existingUrls = new Set(results.map(r => r.url));
            const newResults = response.results.filter(r => !existingUrls.has(r.url));
            setResults(prev => [...prev, ...newResults]);
            setHasMore(response.has_more);
            setCurrentOffset(prev => prev + 20);
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to load more results');
        } finally {
            setIsLoadingMore(false);
        }
    };

    // Add single article
    const handleAddSingle = async (url: string) => {
        if (addingUrls.has(url) || addedUrls.has(url)) return;
        
        setAddingUrls(prev => new Set(prev).add(url));
        setError(null);

        try {
            const response = await webService.scrape([url]);
            const successfulScrapes = response.results.filter(c => c.success);

            if (successfulScrapes.length === 0) {
                setError('Failed to scrape content');
                return;
            }

            await webService.addToNotebook(notebookId, [url], successfulScrapes);
            setAddedUrls(prev => new Set(prev).add(url));
            
            // Refresh sources list without closing modal
            if (onSourceAdded) {
                onSourceAdded();
            }
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to add');
        } finally {
            setAddingUrls(prev => {
                const next = new Set(prev);
                next.delete(url);
                return next;
            });
        }
    };

    // Add all articles
    const handleAddAll = async () => {
        const urlsToAdd = results.map(r => r.url).filter(url => !addedUrls.has(url));
        if (urlsToAdd.length === 0) return;
        
        setAddingUrls(new Set(urlsToAdd));
        setError(null);

        try {
            const response = await webService.scrape(urlsToAdd);
            setScrapedContent(response.results);
            const successfulScrapes = response.results.filter(c => c.success);

            if (successfulScrapes.length === 0) {
                setError('Failed to scrape any content');
                return;
            }

            await webService.addToNotebook(notebookId, urlsToAdd, successfulScrapes);
            setAddedUrls(prev => new Set([...prev, ...urlsToAdd]));
            
            // Don't call onAddedToNotebook - it closes the modal
        } catch (err) {
            setError(err instanceof Error ? err.message : 'Failed to add to notebook');
        } finally {
            setAddingUrls(new Set());
        }
    };

    // Open URL in browser for preview (use Tauri opener plugin)
    const handlePreview = async (url: string) => {
        try {
            const { openUrl } = await import('@tauri-apps/plugin-opener');
            await openUrl(url);
        } catch (e) {
            console.error('Failed to open URL:', e);
            // Fallback for non-Tauri environment
            window.open(url, '_blank', 'noopener,noreferrer');
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
                        {/* Compact Action Bar */}
                        <div className="flex items-center justify-between mb-3 text-xs text-gray-500 dark:text-gray-400">
                            <button
                                onClick={handleAddAll}
                                disabled={addingUrls.size > 0 || addedUrls.size === results.length}
                                className="flex items-center gap-1.5 hover:text-blue-600 dark:hover:text-blue-400 disabled:opacity-50 disabled:cursor-not-allowed group"
                                title="Scrapes and adds all results to your notebook sources"
                            >
                                <span className="w-5 h-5 flex items-center justify-center rounded bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 group-hover:bg-blue-200 dark:group-hover:bg-blue-900/50 text-sm font-bold">+</span>
                                <span>Add All</span>
                                <span className="text-gray-400">({results.length - addedUrls.size} remaining)</span>
                            </button>
                            <span>
                                {currentOffset > 20 ? `${getFreshnessLabel(0)} + ${getFreshnessLabel(20)}${currentOffset > 40 ? ` + ${getFreshnessLabel(40)}` : ''}` : `${results.length} results`}
                            </span>
                        </div>

                        {/* Search Results List */}
                        <div className="space-y-2">
                            {results.map((result, idx) => (
                                <div
                                    key={idx}
                                    className={`flex gap-3 p-3 border rounded-lg transition-colors ${
                                        addedUrls.has(result.url)
                                            ? 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800'
                                            : 'bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-750'
                                    }`}
                                >
                                    {/* Add Button */}
                                    <button
                                        onClick={() => handleAddSingle(result.url)}
                                        disabled={addingUrls.has(result.url) || addedUrls.has(result.url)}
                                        className={`w-6 h-6 flex-shrink-0 flex items-center justify-center rounded text-sm font-bold transition-all ${
                                            addedUrls.has(result.url)
                                                ? 'bg-green-500 text-white cursor-default'
                                                : addingUrls.has(result.url)
                                                ? 'bg-blue-200 dark:bg-blue-800 text-blue-600 dark:text-blue-300 animate-pulse'
                                                : 'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 hover:bg-blue-100 dark:hover:bg-blue-900/50 hover:text-blue-600 dark:hover:text-blue-400'
                                        }`}
                                        title={addedUrls.has(result.url) ? 'Added' : 'Add to notebook'}
                                    >
                                        {addedUrls.has(result.url) ? '✓' : addingUrls.has(result.url) ? '...' : '+'}
                                    </button>
                                    
                                    {/* Content - click title to preview */}
                                    <div className="flex-1 min-w-0">
                                        <button
                                            onClick={() => handlePreview(result.url)}
                                            className="font-medium text-blue-700 dark:text-blue-400 text-sm mb-1 hover:underline text-left"
                                        >
                                            {result.title}
                                        </button>
                                        <p className="text-xs text-gray-600 dark:text-gray-400 mb-1" dangerouslySetInnerHTML={{ __html: result.snippet }} />
                                        <p className="text-xs text-gray-500 dark:text-gray-500 truncate">
                                            {result.url}
                                        </p>
                                    </div>
                                </div>
                            ))}
                        </div>

                        {/* Load More Button */}
                        {hasMore && (
                            <div className="mt-4 text-center">
                                <button
                                    onClick={handleLoadMore}
                                    disabled={isLoadingMore}
                                    className="px-4 py-2 bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
                                >
                                    {isLoadingMore ? 'Loading...' : `Load More (${getFreshnessLabel(currentOffset)})`}
                                </button>
                            </div>
                        )}

                    </>
                )}

                {/* Existing Web Sources */}
                {existingSources.length > 0 && results.length === 0 && !isSearching && (
                    <div className="mb-4">
                        <button
                            onClick={() => setShowExisting(!showExisting)}
                            className="flex items-center gap-2 text-sm font-medium text-gray-700 dark:text-gray-300 mb-2"
                        >
                            <svg 
                                className={`w-4 h-4 transition-transform ${showExisting ? 'rotate-90' : ''}`} 
                                fill="none" 
                                stroke="currentColor" 
                                viewBox="0 0 24 24"
                            >
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                            </svg>
                            Previously Added ({existingSources.length} web sources)
                        </button>
                        
                        {showExisting && (
                            <div className="space-y-2 pl-6">
                                {existingSources.map((source) => (
                                    <div
                                        key={source.id}
                                        className="p-2 bg-gray-50 dark:bg-gray-750 border border-gray-200 dark:border-gray-700 rounded text-xs"
                                    >
                                        <p className="font-medium text-gray-800 dark:text-gray-200 truncate">
                                            {source.title}
                                        </p>
                                        <p className="text-gray-500 dark:text-gray-400 truncate">
                                            {source.url}
                                        </p>
                                        <p className="text-gray-400 dark:text-gray-500 mt-1">
                                            {source.word_count.toLocaleString()} words • {source.type}
                                        </p>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
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
