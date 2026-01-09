/**
 * Web Search Results Component
 * Allows users to search, preview, and scrape web content
 */

import React, { useState, useEffect, useRef } from 'react';
import { webService, WebSearchResult } from '../services/web';
import { API_BASE_URL } from '../services/api';

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
    onSourceAdded?: () => void;  // Called when a source is added (doesn't close modal)
    initialQuery?: string;
}

export const WebSearchResults: React.FC<WebSearchResultsProps> = ({
    notebookId,
    onSourceAdded,
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
    const [isSearching, setIsSearching] = useState(false);
    const [isLoadingMore, setIsLoadingMore] = useState(false);
    const [addedUrls, setAddedUrls] = useState<Set<string>>(new Set());    // Track which URLs have been added
    const [failedUrls, setFailedUrls] = useState<Map<string, string>>(new Map()); // Track failed URLs with error message
    const [error, setError] = useState<string | null>(null);
    const wsRef = useRef<WebSocket | null>(null);
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

    // WebSocket to listen for source processing failures and update UI
    useEffect(() => {
        if (!notebookId) return;

        const wsUrl = API_BASE_URL.replace('http', 'ws') + '/constellation/ws';
        const ws = new WebSocket(wsUrl);
        wsRef.current = ws;

        ws.onmessage = (event) => {
            try {
                const message = JSON.parse(event.data);
                if (message.type === 'source_updated' && message.data?.notebook_id === notebookId) {
                    const { status, title, error: errorMsg } = message.data;
                    
                    // Find the URL that matches this title (best effort)
                    const matchingResult = results.find(r => r.title === title || r.url.includes(title));
                    
                    if (status === 'failed' && matchingResult) {
                        // Mark as failed - remove from added, add to failed
                        setAddedUrls(prev => {
                            const next = new Set(prev);
                            next.delete(matchingResult.url);
                            return next;
                        });
                        setFailedUrls(prev => {
                            const next = new Map(prev);
                            next.set(matchingResult.url, errorMsg || 'Failed to process');
                            return next;
                        });
                    }
                }
            } catch (e) {
                console.error('WebSocket message parse error:', e);
            }
        };

        return () => {
            ws.close();
            wsRef.current = null;
        };
    }, [notebookId, results]);

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
        setFailedUrls(new Map());
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

    // Add single article - OPTIMISTIC UI: show success immediately, fire-and-forget API call
    const handleAddSingle = async (url: string) => {
        // Allow retry if previously failed, otherwise skip if already added
        if (addedUrls.has(url) && !failedUrls.has(url)) return;
        
        // Find the title from results
        const result = results.find(r => r.url === url);
        const title = result?.title || url;
        
        // Clear any previous failure state for this URL
        if (failedUrls.has(url)) {
            setFailedUrls(prev => {
                const next = new Map(prev);
                next.delete(url);
                return next;
            });
        }
        
        // OPTIMISTIC: Show success IMMEDIATELY (before API call)
        setAddedUrls(prev => new Set(prev).add(url));
        
        // Refresh sources list to show new "processing" source
        if (onSourceAdded) {
            onSourceAdded();
        }
        
        // Fire-and-forget: API call happens in background
        // Only show error if it fails, don't block the UI
        webService.quickAdd(notebookId, url, title).catch(err => {
            // Rollback on failure
            setAddedUrls(prev => {
                const next = new Set(prev);
                next.delete(url);
                return next;
            });
            setError(err instanceof Error ? err.message : 'Failed to add');
        });
    };

    // Add all articles - OPTIMISTIC UI: show success immediately for all
    const handleAddAll = async () => {
        const resultsToAdd = results.filter(r => !addedUrls.has(r.url));
        if (resultsToAdd.length === 0) return;
        
        const urlsToAdd = resultsToAdd.map(r => r.url);
        
        // OPTIMISTIC: Show all as added IMMEDIATELY
        setAddedUrls(prev => new Set([...prev, ...urlsToAdd]));
        
        // Refresh sources list
        if (onSourceAdded) {
            onSourceAdded();
        }
        
        // Fire-and-forget: API calls happen in background
        resultsToAdd.forEach(r => {
            webService.quickAdd(notebookId, r.url, r.title).catch(err => {
                // Rollback this specific URL on failure
                setAddedUrls(prev => {
                    const next = new Set(prev);
                    next.delete(r.url);
                    return next;
                });
                console.error(`Failed to add ${r.url}:`, err);
            });
        });
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
                                disabled={addedUrls.size === results.length}
                                className="flex items-center gap-1.5 hover:text-blue-600 dark:hover:text-blue-400 disabled:opacity-50 disabled:cursor-not-allowed group"
                                title="Scrapes and adds all results to your notebook sources"
                            >
                                <span className="w-5 h-5 flex items-center justify-center rounded bg-blue-100 dark:bg-blue-900/30 text-blue-600 dark:text-blue-400 group-hover:bg-blue-200 dark:group-hover:bg-blue-900/50 text-sm font-bold">+</span>
                                <span>Add All</span>
                                <span className="text-gray-400 dark:text-gray-500">({results.length - addedUrls.size} remaining)</span>
                            </button>
                            <span>
                                {currentOffset > 20 ? `${getFreshnessLabel(0)} + ${getFreshnessLabel(20)}${currentOffset > 40 ? ` + ${getFreshnessLabel(40)}` : ''}` : `${results.length} results`}
                            </span>
                        </div>

                        {/* Search Results List - Unified format matching Site Search */}
                        <div className="space-y-3">
                            {results.map((result, idx) => (
                                <div
                                    key={idx}
                                    className={`p-3 border rounded-lg transition-colors ${
                                        failedUrls.has(result.url)
                                            ? 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800'
                                            : addedUrls.has(result.url)
                                            ? 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800'
                                            : 'bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700 hover:border-blue-300 dark:hover:border-blue-600'
                                    }`}
                                >
                                    <div className="flex justify-between items-start gap-2">
                                        <div className="flex-1 min-w-0">
                                            {/* Title */}
                                            <button
                                                onClick={() => handlePreview(result.url)}
                                                className="text-sm font-medium text-blue-600 dark:text-blue-400 hover:underline text-left line-clamp-2"
                                            >
                                                {result.title}
                                            </button>
                                            
                                            {/* Source badge and read time */}
                                            <div className="flex items-center gap-2 mt-1 text-xs text-gray-500 dark:text-gray-400">
                                                <span className="bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded">
                                                    {(() => {
                                                        try {
                                                            return new URL(result.url).hostname.replace('www.', '');
                                                        } catch {
                                                            return 'Web';
                                                        }
                                                    })()}
                                                </span>
                                                {result.read_time && (
                                                    <span className="bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded">
                                                        📖 {result.read_time}
                                                    </span>
                                                )}
                                            </div>
                                            
                                            {/* Snippet */}
                                            <p className="mt-1 text-xs text-gray-600 dark:text-gray-300 line-clamp-2" dangerouslySetInnerHTML={{ __html: result.snippet }} />
                                        </div>
                                        
                                        {/* Add Button - positioned like Site Search action */}
                                        <button
                                            onClick={() => handleAddSingle(result.url)}
                                            disabled={addedUrls.has(result.url) && !failedUrls.has(result.url)}
                                            className={`w-7 h-7 flex-shrink-0 flex items-center justify-center rounded text-sm font-bold transition-all ${
                                                failedUrls.has(result.url)
                                                    ? 'bg-red-500 text-white cursor-pointer hover:bg-red-600'
                                                    : addedUrls.has(result.url)
                                                    ? 'bg-green-500 text-white cursor-default'
                                                    : 'bg-gray-100 dark:bg-gray-700 text-gray-500 dark:text-gray-400 hover:bg-blue-100 dark:hover:bg-blue-900/50 hover:text-blue-600 dark:hover:text-blue-400'
                                            }`}
                                            title={
                                                failedUrls.has(result.url) 
                                                    ? `Failed: ${failedUrls.get(result.url)} - Click to retry` 
                                                    : addedUrls.has(result.url) 
                                                    ? 'Added' 
                                                    : 'Add to notebook'
                                            }
                                        >
                                            {failedUrls.has(result.url) ? '✕' : addedUrls.has(result.url) ? '✓' : '+'}
                                        </button>
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

        </div>
    );
};
