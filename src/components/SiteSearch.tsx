/**
 * Site-Specific Search Component
 * 
 * Allows searching specific research sites with time filters.
 * Supports tab-completion for site selection.
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { 
  siteSearchService, 
  SearchResult, 
  SupportedSite, 
  TimeRange, 
  TIME_RANGE_OPTIONS 
} from '../services/siteSearch';
import { webService } from '../services/web';
import { API_BASE_URL } from '../services/api';
import { Button } from './shared/Button';
import { LoadingSpinner } from './shared/LoadingSpinner';

interface SiteSearchProps {
  notebookId?: string;
  onSourceAdded?: () => void;  // Called when a source is added
  initialQuery?: string;
}

export const SiteSearch: React.FC<SiteSearchProps> = ({ 
  notebookId, 
  onSourceAdded,
  initialQuery = '' 
}) => {
  const [input, setInput] = useState(initialQuery);
  const [selectedSite, setSelectedSite] = useState<string | undefined>();
  const [timeRange, setTimeRange] = useState<TimeRange>('all');
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [supportedSites, setSupportedSites] = useState<SupportedSite[]>([]);
  const [showSiteDropdown, setShowSiteDropdown] = useState(false);
  const [addedUrls, setAddedUrls] = useState<Set<string>>(new Set());
  const [failedUrls, setFailedUrls] = useState<Map<string, string>>(new Map());
  const inputRef = useRef<HTMLInputElement>(null);
  const wsRef = useRef<WebSocket | null>(null);

  // Load supported sites on mount
  useEffect(() => {
    siteSearchService.getSupportedSites()
      .then(setSupportedSites)
      .catch(console.error);
  }, []);

  // WebSocket to listen for source processing failures
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
          
          // Find the URL that matches this title
          const matchingResult = results.find(r => r.title === title || r.url.includes(title));
          
          if (status === 'failed' && matchingResult) {
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

  // Add single result to notebook with optimistic UI
  const handleAddSingle = async (url: string, title: string) => {
    if (!notebookId) return;
    if (addedUrls.has(url) && !failedUrls.has(url)) return;
    
    // Clear any previous failure state
    if (failedUrls.has(url)) {
      setFailedUrls(prev => {
        const next = new Map(prev);
        next.delete(url);
        return next;
      });
    }
    
    // OPTIMISTIC: Show success immediately
    setAddedUrls(prev => new Set(prev).add(url));
    
    if (onSourceAdded) {
      onSourceAdded();
    }
    
    // Fire-and-forget API call
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

  // Handle input change with site detection
  const handleInputChange = (value: string) => {
    setInput(value);
    
    // Check if user typed a domain followed by space/tab
    const parsed = siteSearchService.parseSearchInput(value);
    if (parsed.site && parsed.query) {
      // Check if it's a supported site
      if (siteSearchService.isSupportedSite(parsed.site, supportedSites)) {
        setSelectedSite(parsed.site);
        setInput(parsed.query);
        setShowSiteDropdown(false);
      }
    }
  };

  // Handle keyboard shortcuts
  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Tab' && !selectedSite) {
      // Check if current input looks like a domain
      const domainMatch = input.match(/^([a-z0-9][-a-z0-9]*(?:\.[a-z0-9][-a-z0-9]*)+)$/i);
      if (domainMatch) {
        e.preventDefault();
        const site = domainMatch[1].toLowerCase();
        setSelectedSite(site);
        setInput('');
        inputRef.current?.focus();
      }
    } else if (e.key === 'Backspace' && input === '' && selectedSite) {
      // Remove site chip on backspace when input is empty
      setSelectedSite(undefined);
    } else if (e.key === 'Enter' && input.trim()) {
      e.preventDefault();
      handleSearch();
    }
  };

  const handleSearch = useCallback(async () => {
    if (!input.trim()) return;
    
    setLoading(true);
    setError(null);
    
    try {
      const response = await siteSearchService.search(
        input,
        selectedSite,
        timeRange,
        20
      );
      setResults(response.results);
    } catch (err: any) {
      setError(err.message || 'Search failed');
    } finally {
      setLoading(false);
    }
  }, [input, selectedSite, timeRange]);

  const clearSite = () => {
    setSelectedSite(undefined);
    inputRef.current?.focus();
  };

  const selectSite = (domain: string) => {
    setSelectedSite(domain);
    setShowSiteDropdown(false);
    inputRef.current?.focus();
  };

  return (
    <div className="flex flex-col h-full">
      {/* Search Bar */}
      <div className="p-4 border-b border-gray-200 dark:border-gray-700 space-y-3">
        {/* Input with site chip */}
        <div className="flex items-center gap-2 bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-600 rounded-lg px-3 py-2 focus-within:ring-2 focus-within:ring-blue-500">
          {/* Site chip */}
          {selectedSite && (
            <div className="flex items-center gap-1 bg-blue-100 dark:bg-blue-900/40 text-blue-800 dark:text-blue-200 px-2 py-0.5 rounded-full text-xs font-medium">
              <span>{selectedSite}</span>
              <button
                onClick={clearSite}
                className="hover:text-blue-600 dark:hover:text-blue-300"
              >
                ×
              </button>
            </div>
          )}
          
          {/* Search input */}
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => handleInputChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={selectedSite ? "Search query..." : "site.com [TAB] or search query..."}
            className="flex-1 bg-transparent outline-none text-sm text-gray-900 dark:text-white placeholder-gray-500"
          />
          
          {/* Site picker button */}
          <div className="relative">
            <button
              onClick={() => setShowSiteDropdown(!showSiteDropdown)}
              className="p-1 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300"
              title="Select site"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 12a9 9 0 01-9 9m9-9a9 9 0 00-9-9m9 9H3m9 9a9 9 0 01-9-9m9 9c1.657 0 3-4.03 3-9s-1.343-9-3-9m0 18c-1.657 0-3-4.03-3-9s1.343-9 3-9m-9 9a9 9 0 019-9" />
              </svg>
            </button>
            
            {/* Site dropdown */}
            {showSiteDropdown && (
              <div className="absolute right-0 top-full mt-1 w-64 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg z-50 max-h-64 overflow-y-auto">
                <div className="p-2 border-b border-gray-200 dark:border-gray-700">
                  <p className="text-xs text-gray-500 dark:text-gray-400">Supported Sites</p>
                </div>
                {supportedSites.map((site) => (
                  <button
                    key={site.domain}
                    onClick={() => selectSite(site.domain)}
                    className="w-full px-3 py-2 text-left text-sm hover:bg-gray-100 dark:hover:bg-gray-700 flex justify-between items-center text-gray-900 dark:text-gray-100"
                  >
                    <span className="font-medium">{site.name}</span>
                    <span className="text-xs text-gray-500 dark:text-gray-400">{site.domain}</span>
                  </button>
                ))}
                <div className="p-2 border-t border-gray-200 dark:border-gray-700">
                  <p className="text-xs text-gray-500 dark:text-gray-400">
                    Or type any domain + TAB
                  </p>
                </div>
              </div>
            )}
          </div>
        </div>
        
        {/* Filters row */}
        <div className="flex items-center gap-3">
          {/* Time filter */}
          <select
            value={timeRange}
            onChange={(e) => setTimeRange(e.target.value as TimeRange)}
            className="text-xs px-2 py-1 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-700 text-gray-900 dark:text-white"
          >
            {TIME_RANGE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </select>
          
          <Button onClick={handleSearch} disabled={loading || !input.trim()} size="sm">
            {loading ? <LoadingSpinner size="sm" /> : '🔍 Search'}
          </Button>
        </div>
        
        {/* Hint */}
        <p className="text-xs text-gray-500 dark:text-gray-400">
          💡 Type <code className="bg-gray-100 dark:bg-gray-700 px-1 rounded">youtube.com</code> then press <kbd className="bg-gray-100 dark:bg-gray-700 px-1 rounded">TAB</kbd> to search that site
        </p>
      </div>
      
      {/* Error */}
      {error && (
        <div className="p-3 m-4 bg-red-50 dark:bg-red-900/20 text-red-600 dark:text-red-400 rounded-lg text-sm">
          {error}
        </div>
      )}
      
      {/* Results */}
      <div className="flex-1 overflow-y-auto p-4 space-y-3">
        {results.length === 0 && !loading && (
          <div className="text-center text-gray-500 dark:text-gray-400 py-8">
            <p className="text-lg mb-2">🔍</p>
            <p className="text-sm">Search results will appear here</p>
          </div>
        )}
        
        {results.map((result, idx) => (
          <div
            key={idx}
            className={`relative bg-white dark:bg-gray-800 border rounded-lg p-3 transition-colors ${
              failedUrls.has(result.url)
                ? 'bg-red-50 dark:bg-red-900/20 border-red-200 dark:border-red-800'
                : addedUrls.has(result.url)
                ? 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-800'
                : 'border-gray-200 dark:border-gray-700 hover:border-blue-300 dark:hover:border-blue-600'
            }`}
          >
            <div className="flex justify-between items-start gap-3">
              <div className="flex-1 min-w-0">
                <a
                  href={result.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm font-medium text-blue-600 dark:text-blue-400 hover:underline line-clamp-2"
                >
                  {result.title}
                </a>
                
                <div className="flex items-center gap-2 mt-1 text-xs text-gray-500 dark:text-gray-400">
                  <span className="bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded">
                    {result.source_site}
                  </span>
                  {result.author && <span>by {result.author}</span>}
                  {result.published_date && (
                    <span>{new Date(result.published_date).toLocaleDateString()}</span>
                  )}
                </div>
                
                <p className="mt-1 text-xs text-gray-600 dark:text-gray-300 line-clamp-2">
                  {result.snippet}
                </p>
                
                {/* Metadata badges */}
                {result.metadata && (
                  <div className="flex flex-wrap gap-2 mt-2">
                    {result.metadata.duration && (
                      <span className="text-xs bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 px-1.5 py-0.5 rounded">🎬 {result.metadata.duration}</span>
                    )}
                    {result.metadata.read_time && !result.metadata.duration && (
                      <span className="text-xs bg-gray-100 dark:bg-gray-700 text-gray-700 dark:text-gray-300 px-1.5 py-0.5 rounded">📖 {result.metadata.read_time}</span>
                    )}
                    {result.metadata.view_count && (
                      <span className="text-xs text-gray-500 dark:text-gray-400">👁️ {result.metadata.view_count}</span>
                    )}
                    {result.metadata.stars && (
                      <span className="text-xs text-yellow-600">⭐ {result.metadata.stars}</span>
                    )}
                    {result.metadata.citations && (
                      <span className="text-xs text-purple-600">📚 {result.metadata.citations} citations</span>
                    )}
                    {result.metadata.score && (
                      <span className="text-xs text-orange-600">▲ {result.metadata.score}</span>
                    )}
                    {result.metadata.num_comments && (
                      <span className="text-xs text-blue-600">💬 {result.metadata.num_comments}</span>
                    )}
                  </div>
                )}
              </div>
              
              {/* Thumbnail + Add Button column */}
              <div className="flex flex-col items-center gap-1 flex-shrink-0">
                {result.thumbnail && (
                  <img
                    src={result.thumbnail}
                    alt=""
                    className="w-24 h-16 object-cover rounded"
                  />
                )}
                {/* Add Button - positioned below thumbnail or standalone */}
                {notebookId && (
                  <button
                    onClick={() => handleAddSingle(result.url, result.title)}
                    disabled={addedUrls.has(result.url) && !failedUrls.has(result.url)}
                    className={`w-7 h-7 flex items-center justify-center rounded text-sm font-bold transition-all ${
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
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};
