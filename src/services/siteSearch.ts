/**
 * Site-Specific Search Service
 * 
 * Provides targeted search across research-focused sites:
 * YouTube, ArXiv, GitHub, Reddit, Wikipedia, Semantic Scholar,
 * Hacker News, Stack Overflow, PubMed, and any site via Brave fallback.
 */

import { API_BASE_URL } from './api';

const API_BASE = API_BASE_URL;

export interface SearchResult {
  title: string;
  url: string;
  snippet: string;
  source_site: string;
  published_date?: string;
  author?: string;
  thumbnail?: string;
  metadata?: Record<string, any>;
}

export interface SiteSearchResponse {
  query: string;
  site_domain?: string;
  time_range: string;
  results: SearchResult[];
  total_results: number;
}

export interface SupportedSite {
  domain: string;
  name: string;
  requires_api_key: boolean;
  api_key_env_var?: string;
}

export type TimeRange = 'all' | '24h' | '7d' | '14d' | '30d' | '90d' | '1y';

export const TIME_RANGE_OPTIONS: { value: TimeRange; label: string }[] = [
  { value: 'all', label: 'All Time' },
  { value: '24h', label: 'Last 24 Hours' },
  { value: '7d', label: 'Last 7 Days' },
  { value: '14d', label: 'Last 2 Weeks' },
  { value: '30d', label: 'Last 30 Days' },
  { value: '90d', label: 'Last 90 Days' },
  { value: '1y', label: 'Last Year' },
];

export const siteSearchService = {
  /**
   * Get list of sites with native search support
   */
  async getSupportedSites(): Promise<SupportedSite[]> {
    const response = await fetch(`${API_BASE}/site-search/supported-sites`);
    if (!response.ok) throw new Error('Failed to get supported sites');
    return response.json();
  },

  /**
   * Search a specific site or the web
   * 
   * @param query - Search query
   * @param siteDomain - Optional site domain (e.g., "youtube.com")
   * @param timeRange - Time filter
   * @param maxResults - Maximum results to return
   */
  async search(
    query: string,
    siteDomain?: string,
    timeRange: TimeRange = 'all',
    maxResults: number = 10
  ): Promise<SiteSearchResponse> {
    const response = await fetch(`${API_BASE}/site-search/search`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        query,
        site_domain: siteDomain,
        time_range: timeRange,
        max_results: maxResults,
      }),
    });
    
    if (!response.ok) throw new Error('Search failed');
    return response.json();
  },

  /**
   * Parse a search input to extract site: prefix
   * 
   * Examples:
   * - "youtube.com quantum computing" -> { site: "youtube.com", query: "quantum computing" }
   * - "quantum computing" -> { site: undefined, query: "quantum computing" }
   */
  parseSearchInput(input: string): { site?: string; query: string } {
    // Check for domain-like prefix followed by space or tab
    const domainMatch = input.match(/^([a-z0-9][-a-z0-9]*(?:\.[a-z0-9][-a-z0-9]*)+)[\s\t]+(.+)$/i);
    
    if (domainMatch) {
      return {
        site: domainMatch[1].toLowerCase(),
        query: domainMatch[2].trim(),
      };
    }
    
    return { query: input.trim() };
  },

  /**
   * Check if a domain is in our supported sites list
   */
  isSupportedSite(domain: string, supportedSites: SupportedSite[]): boolean {
    const normalized = domain.toLowerCase();
    return supportedSites.some(s => 
      s.domain === normalized || 
      s.domain.includes(normalized) ||
      normalized.includes(s.domain)
    );
  },
};
