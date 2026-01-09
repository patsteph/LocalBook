"""Site-Specific Search Service

Provides targeted search across research-focused sites with proper APIs.
Falls back to Brave Search with site: operator for unsupported sites.

Supported Sites (with native APIs):
1. YouTube - YouTube Data API v3
2. ArXiv - ArXiv API
3. GitHub - GitHub Search API
4. Reddit - Reddit API
5. Wikipedia - MediaWiki API
6. Semantic Scholar - Semantic Scholar API
7. Hacker News - Algolia HN API
8. Stack Overflow - Stack Exchange API
9. PubMed - NCBI E-utilities API
10. Medium - Web scrape / Brave fallback

Fallback: Brave Search with site:domain.com operator
"""

import asyncio
import httpx
from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from dataclasses import dataclass
from enum import Enum
import os
import json
import re

from api.settings import get_api_key


class TimeRange(Enum):
    """Time range options for filtering search results."""
    ALL_TIME = "all"
    LAST_24H = "24h"
    LAST_7D = "7d"
    LAST_14D = "14d"
    LAST_30D = "30d"
    LAST_90D = "90d"
    LAST_YEAR = "1y"


@dataclass
class SearchResult:
    """Unified search result across all site handlers."""
    title: str
    url: str
    snippet: str
    source_site: str
    published_date: Optional[str] = None
    author: Optional[str] = None
    thumbnail: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None  # Site-specific extra data


def estimate_read_time(text: str, content_type: str = "article") -> str:
    """Estimate reading time based on text length and content type.
    
    Args:
        text: The text to estimate (usually snippet)
        content_type: Type of content - article, paper, discussion, code
    
    Returns:
        Human readable estimate like "5 min read" or "~10 min"
    """
    if not text:
        return ""
    
    # Count words in snippet
    words = len(text.split())
    
    # Estimation multipliers (snippet is typically ~5-10% of content)
    multipliers = {
        "article": 8,      # Web articles - snippet ~12% of content
        "paper": 1,        # Academic papers - use fixed estimates
        "discussion": 5,   # Reddit/HN - shorter content
        "wiki": 10,        # Wikipedia - comprehensive
    }
    
    multiplier = multipliers.get(content_type, 8)
    estimated_words = words * multiplier
    
    # Average reading speed: 238 words per minute
    minutes = max(1, round(estimated_words / 238))
    
    # Cap estimates for articles to be reasonable
    if content_type == "article" and minutes > 30:
        minutes = 30
    
    if minutes == 1:
        return "1 min read"
    elif minutes < 5:
        return f"{minutes} min read"
    else:
        # Round to nearest 5 for longer articles
        rounded = 5 * round(minutes / 5)
        return f"~{rounded} min read"


class SiteSearchHandler(ABC):
    """Base class for site-specific search handlers."""
    
    site_domain: str = ""
    site_name: str = ""
    requires_api_key: bool = False
    api_key_env_var: str = ""
    
    @abstractmethod
    async def search(
        self, 
        query: str, 
        time_range: TimeRange = TimeRange.ALL_TIME,
        max_results: int = 10
    ) -> List[SearchResult]:
        """Execute search and return results."""
        pass
    
    def _get_date_filter(self, time_range: TimeRange) -> Optional[datetime]:
        """Convert time range to datetime for filtering."""
        if time_range == TimeRange.ALL_TIME:
            return None
        
        days_map = {
            TimeRange.LAST_24H: 1,
            TimeRange.LAST_7D: 7,
            TimeRange.LAST_14D: 14,
            TimeRange.LAST_30D: 30,
            TimeRange.LAST_90D: 90,
            TimeRange.LAST_YEAR: 365,
        }
        
        days = days_map.get(time_range, 0)
        return datetime.now() - timedelta(days=days) if days else None


# =============================================================================
# YouTube Handler
# =============================================================================

class YouTubeSearchHandler(SiteSearchHandler):
    """YouTube search using YouTube Data API v3."""
    
    site_domain = "youtube.com"
    site_name = "YouTube"
    requires_api_key = True
    api_key_env_var = "YOUTUBE_API_KEY"
    
    def _parse_duration(self, duration_str: str) -> str:
        """Parse ISO 8601 duration (PT1H2M3S) to human readable format."""
        if not duration_str:
            return ""
        
        # Remove PT prefix
        duration_str = duration_str.replace("PT", "")
        
        hours = 0
        minutes = 0
        seconds = 0
        
        # Parse hours
        if "H" in duration_str:
            parts = duration_str.split("H")
            hours = int(parts[0])
            duration_str = parts[1] if len(parts) > 1 else ""
        
        # Parse minutes
        if "M" in duration_str:
            parts = duration_str.split("M")
            minutes = int(parts[0])
            duration_str = parts[1] if len(parts) > 1 else ""
        
        # Parse seconds
        if "S" in duration_str:
            seconds = int(duration_str.replace("S", ""))
        
        # Format output
        if hours > 0:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        else:
            return f"{minutes}:{seconds:02d}"
    
    async def search(
        self, 
        query: str, 
        time_range: TimeRange = TimeRange.ALL_TIME,
        max_results: int = 10
    ) -> List[SearchResult]:
        # Try settings storage first, then fall back to env var
        api_key = get_api_key("youtube_api_key") or os.getenv(self.api_key_env_var)
        
        # If no API key, fall back to Brave
        if not api_key:
            return await BraveFallbackHandler(self.site_domain).search(query, time_range, max_results)
        
        params = {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": min(max_results, 50),
            "key": api_key,
            "order": "relevance",
        }
        
        # Add date filter
        date_after = self._get_date_filter(time_range)
        if date_after:
            params["publishedAfter"] = date_after.isoformat() + "Z"
        
        async with httpx.AsyncClient() as client:
            # Step 1: Search for videos
            response = await client.get(
                "https://www.googleapis.com/youtube/v3/search",
                params=params,
                timeout=15.0
            )
            
            if response.status_code != 200:
                # Fallback to Brave on error
                return await BraveFallbackHandler(self.site_domain).search(query, time_range, max_results)
            
            data = response.json()
            
            # Collect video IDs for duration lookup
            video_ids = []
            video_data = []
            
            for item in data.get("items", []):
                snippet = item.get("snippet", {})
                video_id = item.get("id", {}).get("videoId", "")
                if video_id:
                    video_ids.append(video_id)
                    video_data.append({
                        "video_id": video_id,
                        "title": snippet.get("title", ""),
                        "description": snippet.get("description", "")[:300],
                        "published_at": snippet.get("publishedAt"),
                        "channel_title": snippet.get("channelTitle"),
                        "channel_id": snippet.get("channelId"),
                        "thumbnail": snippet.get("thumbnails", {}).get("medium", {}).get("url"),
                    })
            
            # Step 2: Batch fetch video details (duration, view count)
            durations = {}
            view_counts = {}
            if video_ids:
                details_response = await client.get(
                    "https://www.googleapis.com/youtube/v3/videos",
                    params={
                        "part": "contentDetails,statistics",
                        "id": ",".join(video_ids),
                        "key": api_key,
                    },
                    timeout=15.0
                )
                
                if details_response.status_code == 200:
                    details_data = details_response.json()
                    for item in details_data.get("items", []):
                        vid = item.get("id", "")
                        content_details = item.get("contentDetails", {})
                        statistics = item.get("statistics", {})
                        durations[vid] = content_details.get("duration", "")
                        view_counts[vid] = statistics.get("viewCount", "")
            
            # Build results with duration
            results = []
            for vd in video_data:
                video_id = vd["video_id"]
                duration_iso = durations.get(video_id, "")
                duration_formatted = self._parse_duration(duration_iso)
                view_count = view_counts.get(video_id, "")
                
                # Format view count
                view_count_formatted = ""
                if view_count:
                    try:
                        count = int(view_count)
                        if count >= 1_000_000:
                            view_count_formatted = f"{count / 1_000_000:.1f}M views"
                        elif count >= 1_000:
                            view_count_formatted = f"{count / 1_000:.1f}K views"
                        else:
                            view_count_formatted = f"{count} views"
                    except:
                        pass
                
                results.append(SearchResult(
                    title=vd["title"],
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    snippet=vd["description"],
                    source_site=self.site_name,
                    published_date=vd["published_at"],
                    author=vd["channel_title"],
                    thumbnail=vd["thumbnail"],
                    metadata={
                        "video_id": video_id,
                        "channel_id": vd["channel_id"],
                        "duration": duration_formatted,
                        "duration_iso": duration_iso,
                        "view_count": view_count_formatted,
                    }
                ))
            
            return results


# =============================================================================
# ArXiv Handler
# =============================================================================

class ArXivSearchHandler(SiteSearchHandler):
    """ArXiv search using the ArXiv API."""
    
    site_domain = "arxiv.org"
    site_name = "ArXiv"
    requires_api_key = False
    
    async def search(
        self, 
        query: str, 
        time_range: TimeRange = TimeRange.ALL_TIME,
        max_results: int = 10
    ) -> List[SearchResult]:
        # ArXiv API uses Atom feed format
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "http://export.arxiv.org/api/query",
                params=params,
                timeout=15.0
            )
            
            if response.status_code != 200:
                return await BraveFallbackHandler(self.site_domain).search(query, time_range, max_results)
            
            # Parse Atom XML response
            results = self._parse_arxiv_response(response.text, time_range)
            return results
    
    def _parse_arxiv_response(self, xml_text: str, time_range: TimeRange) -> List[SearchResult]:
        """Parse ArXiv Atom feed response."""
        import xml.etree.ElementTree as ET
        
        results = []
        try:
            root = ET.fromstring(xml_text)
            ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
            
            date_filter = self._get_date_filter(time_range)
            
            for entry in root.findall("atom:entry", ns):
                title = entry.find("atom:title", ns)
                summary = entry.find("atom:summary", ns)
                published = entry.find("atom:published", ns)
                
                # Get authors
                authors = entry.findall("atom:author/atom:name", ns)
                author_str = ", ".join([a.text for a in authors[:3]]) if authors else None
                if authors and len(authors) > 3:
                    author_str += f" et al."
                
                # Get PDF link
                pdf_link = None
                for link in entry.findall("atom:link", ns):
                    if link.get("title") == "pdf":
                        pdf_link = link.get("href")
                        break
                
                # Get arxiv ID
                id_elem = entry.find("atom:id", ns)
                arxiv_id = id_elem.text.split("/")[-1] if id_elem is not None else ""
                
                pub_date = published.text if published is not None else None
                
                # Apply date filter
                if date_filter and pub_date:
                    try:
                        pub_datetime = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                        if pub_datetime.replace(tzinfo=None) < date_filter:
                            continue
                    except:
                        pass
                
                results.append(SearchResult(
                    title=title.text.strip() if title is not None else "",
                    url=f"https://arxiv.org/abs/{arxiv_id}",
                    snippet=summary.text.strip()[:300] if summary is not None else "",
                    source_site=self.site_name,
                    published_date=pub_date,
                    author=author_str,
                    metadata={
                        "arxiv_id": arxiv_id,
                        "pdf_url": pdf_link,
                        "read_time": "~15 min read",  # Average academic paper
                    }
                ))
        except Exception as e:
            print(f"[ARXIV] Parse error: {e}")
        
        return results


# =============================================================================
# GitHub Handler
# =============================================================================

class GitHubSearchHandler(SiteSearchHandler):
    """GitHub search using GitHub Search API."""
    
    site_domain = "github.com"
    site_name = "GitHub"
    requires_api_key = False  # Works without key, rate limited
    api_key_env_var = "GITHUB_TOKEN"
    
    async def search(
        self, 
        query: str, 
        time_range: TimeRange = TimeRange.ALL_TIME,
        max_results: int = 10
    ) -> List[SearchResult]:
        headers = {"Accept": "application/vnd.github.v3+json"}
        
        # Add auth if available (higher rate limit)
        token = os.getenv(self.api_key_env_var)
        if token:
            headers["Authorization"] = f"token {token}"
        
        # Build query with date filter
        search_query = query
        date_filter = self._get_date_filter(time_range)
        if date_filter:
            search_query += f" pushed:>{date_filter.strftime('%Y-%m-%d')}"
        
        params = {
            "q": search_query,
            "sort": "stars",
            "order": "desc",
            "per_page": max_results,
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.github.com/search/repositories",
                params=params,
                headers=headers,
                timeout=15.0
            )
            
            if response.status_code != 200:
                return await BraveFallbackHandler(self.site_domain).search(query, time_range, max_results)
            
            data = response.json()
            results = []
            
            for repo in data.get("items", []):
                results.append(SearchResult(
                    title=repo.get("full_name", ""),
                    url=repo.get("html_url", ""),
                    snippet=repo.get("description", "") or "No description",
                    source_site=self.site_name,
                    published_date=repo.get("updated_at"),
                    author=repo.get("owner", {}).get("login"),
                    metadata={
                        "stars": repo.get("stargazers_count"),
                        "forks": repo.get("forks_count"),
                        "language": repo.get("language"),
                    }
                ))
            
            return results


# =============================================================================
# Reddit Handler
# =============================================================================

class RedditSearchHandler(SiteSearchHandler):
    """Reddit search using Reddit JSON API."""
    
    site_domain = "reddit.com"
    site_name = "Reddit"
    requires_api_key = False
    
    async def search(
        self, 
        query: str, 
        time_range: TimeRange = TimeRange.ALL_TIME,
        max_results: int = 10
    ) -> List[SearchResult]:
        # Reddit time filter mapping
        time_map = {
            TimeRange.ALL_TIME: "all",
            TimeRange.LAST_24H: "day",
            TimeRange.LAST_7D: "week",
            TimeRange.LAST_14D: "week",
            TimeRange.LAST_30D: "month",
            TimeRange.LAST_90D: "year",
            TimeRange.LAST_YEAR: "year",
        }
        
        params = {
            "q": query,
            "sort": "relevance",
            "t": time_map.get(time_range, "all"),
            "limit": max_results,
        }
        
        headers = {"User-Agent": "LocalBook/1.0"}
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://www.reddit.com/search.json",
                params=params,
                headers=headers,
                timeout=15.0
            )
            
            if response.status_code != 200:
                return await BraveFallbackHandler(self.site_domain).search(query, time_range, max_results)
            
            data = response.json()
            results = []
            
            for post in data.get("data", {}).get("children", []):
                post_data = post.get("data", {})
                
                # Convert Unix timestamp
                created = post_data.get("created_utc")
                pub_date = datetime.fromtimestamp(created).isoformat() if created else None
                
                results.append(SearchResult(
                    title=post_data.get("title", ""),
                    url=f"https://reddit.com{post_data.get('permalink', '')}",
                    snippet=post_data.get("selftext", "")[:300] or "Link post",
                    source_site=self.site_name,
                    published_date=pub_date,
                    author=post_data.get("author"),
                    metadata={
                        "subreddit": post_data.get("subreddit"),
                        "score": post_data.get("score"),
                        "num_comments": post_data.get("num_comments"),
                    }
                ))
            
            return results


# =============================================================================
# Wikipedia Handler
# =============================================================================

class WikipediaSearchHandler(SiteSearchHandler):
    """Wikipedia search using MediaWiki API."""
    
    site_domain = "wikipedia.org"
    site_name = "Wikipedia"
    requires_api_key = False
    
    async def search(
        self, 
        query: str, 
        time_range: TimeRange = TimeRange.ALL_TIME,
        max_results: int = 10
    ) -> List[SearchResult]:
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": max_results,
            "format": "json",
            "srprop": "snippet|timestamp",
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://en.wikipedia.org/w/api.php",
                params=params,
                timeout=15.0
            )
            
            if response.status_code != 200:
                return await BraveFallbackHandler(self.site_domain).search(query, time_range, max_results)
            
            data = response.json()
            results = []
            
            for item in data.get("query", {}).get("search", []):
                # Clean HTML from snippet
                snippet = re.sub(r'<[^>]+>', '', item.get("snippet", ""))
                
                read_time = estimate_read_time(snippet, "wiki")
                results.append(SearchResult(
                    title=item.get("title", ""),
                    url=f"https://en.wikipedia.org/wiki/{item.get('title', '').replace(' ', '_')}",
                    snippet=snippet,
                    source_site=self.site_name,
                    published_date=item.get("timestamp"),
                    metadata={
                        "page_id": item.get("pageid"),
                        "read_time": read_time,
                    }
                ))
            
            return results


# =============================================================================
# Semantic Scholar Handler
# =============================================================================

class SemanticScholarSearchHandler(SiteSearchHandler):
    """Semantic Scholar search using their API."""
    
    site_domain = "semanticscholar.org"
    site_name = "Semantic Scholar"
    requires_api_key = False  # Free tier available
    
    async def search(
        self, 
        query: str, 
        time_range: TimeRange = TimeRange.ALL_TIME,
        max_results: int = 10
    ) -> List[SearchResult]:
        # Build year filter
        year_filter = None
        date_filter = self._get_date_filter(time_range)
        if date_filter:
            year_filter = f"{date_filter.year}-"
        
        params = {
            "query": query,
            "limit": max_results,
            "fields": "title,abstract,url,year,authors,citationCount,publicationDate",
        }
        
        if year_filter:
            params["year"] = year_filter
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.semanticscholar.org/graph/v1/paper/search",
                params=params,
                timeout=15.0
            )
            
            if response.status_code != 200:
                return await BraveFallbackHandler(self.site_domain).search(query, time_range, max_results)
            
            data = response.json()
            results = []
            
            for paper in data.get("data", []):
                authors = paper.get("authors", [])
                author_str = ", ".join([a.get("name", "") for a in authors[:3]])
                if len(authors) > 3:
                    author_str += " et al."
                
                results.append(SearchResult(
                    title=paper.get("title", ""),
                    url=paper.get("url", "") or f"https://www.semanticscholar.org/paper/{paper.get('paperId', '')}",
                    snippet=paper.get("abstract", "")[:300] if paper.get("abstract") else "No abstract available",
                    source_site=self.site_name,
                    published_date=paper.get("publicationDate"),
                    author=author_str,
                    metadata={
                        "year": paper.get("year"),
                        "citations": paper.get("citationCount"),
                        "paper_id": paper.get("paperId"),
                        "read_time": "~15 min read",  # Average academic paper
                    }
                ))
            
            return results


# =============================================================================
# Hacker News Handler
# =============================================================================

class HackerNewsSearchHandler(SiteSearchHandler):
    """Hacker News search using Algolia API."""
    
    site_domain = "news.ycombinator.com"
    site_name = "Hacker News"
    requires_api_key = False
    
    async def search(
        self, 
        query: str, 
        time_range: TimeRange = TimeRange.ALL_TIME,
        max_results: int = 10
    ) -> List[SearchResult]:
        # Algolia HN API time filter (Unix timestamp)
        numeric_filters = None
        date_filter = self._get_date_filter(time_range)
        if date_filter:
            numeric_filters = f"created_at_i>{int(date_filter.timestamp())}"
        
        params = {
            "query": query,
            "tags": "story",
            "hitsPerPage": max_results,
        }
        
        if numeric_filters:
            params["numericFilters"] = numeric_filters
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://hn.algolia.com/api/v1/search",
                params=params,
                timeout=15.0
            )
            
            if response.status_code != 200:
                return await BraveFallbackHandler(self.site_domain).search(query, time_range, max_results)
            
            data = response.json()
            results = []
            
            for hit in data.get("hits", []):
                # HN links to external articles - estimate based on typical article
                read_time = "~5 min read" if hit.get("url") else "2 min read"
                results.append(SearchResult(
                    title=hit.get("title", ""),
                    url=hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                    snippet=f"{hit.get('points', 0)} points • {hit.get('num_comments', 0)} comments",
                    source_site=self.site_name,
                    published_date=hit.get("created_at"),
                    author=hit.get("author"),
                    metadata={
                        "hn_id": hit.get("objectID"),
                        "points": hit.get("points"),
                        "comments": hit.get("num_comments"),
                        "read_time": read_time,
                    }
                ))
            
            return results


# =============================================================================
# Stack Overflow Handler
# =============================================================================

class StackOverflowSearchHandler(SiteSearchHandler):
    """Stack Overflow search using Stack Exchange API."""
    
    site_domain = "stackoverflow.com"
    site_name = "Stack Overflow"
    requires_api_key = False  # Works without key, rate limited
    
    async def search(
        self, 
        query: str, 
        time_range: TimeRange = TimeRange.ALL_TIME,
        max_results: int = 10
    ) -> List[SearchResult]:
        params = {
            "order": "desc",
            "sort": "relevance",
            "intitle": query,
            "site": "stackoverflow",
            "pagesize": max_results,
            "filter": "withbody",
        }
        
        # Add date filter
        date_filter = self._get_date_filter(time_range)
        if date_filter:
            params["fromdate"] = int(date_filter.timestamp())
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.stackexchange.com/2.3/search/advanced",
                params=params,
                timeout=15.0
            )
            
            if response.status_code != 200:
                return await BraveFallbackHandler(self.site_domain).search(query, time_range, max_results)
            
            data = response.json()
            results = []
            
            for item in data.get("items", []):
                # Clean HTML from body
                body = re.sub(r'<[^>]+>', '', item.get("body", ""))[:300]
                
                read_time = estimate_read_time(body, "discussion")
                results.append(SearchResult(
                    title=item.get("title", ""),
                    url=item.get("link", ""),
                    snippet=body,
                    source_site=self.site_name,
                    published_date=datetime.fromtimestamp(item.get("creation_date", 0)).isoformat(),
                    author=item.get("owner", {}).get("display_name"),
                    metadata={
                        "score": item.get("score"),
                        "answer_count": item.get("answer_count"),
                        "is_answered": item.get("is_answered"),
                        "tags": item.get("tags"),
                        "read_time": read_time,
                    }
                ))
            
            return results


# =============================================================================
# PubMed Handler
# =============================================================================

class PubMedSearchHandler(SiteSearchHandler):
    """PubMed search using NCBI E-utilities API."""
    
    site_domain = "pubmed.ncbi.nlm.nih.gov"
    site_name = "PubMed"
    requires_api_key = False
    
    async def search(
        self, 
        query: str, 
        time_range: TimeRange = TimeRange.ALL_TIME,
        max_results: int = 10
    ) -> List[SearchResult]:
        # First, search for IDs
        search_params = {
            "db": "pubmed",
            "term": query,
            "retmax": max_results,
            "retmode": "json",
            "sort": "relevance",
        }
        
        # Add date filter
        date_filter = self._get_date_filter(time_range)
        if date_filter:
            search_params["mindate"] = date_filter.strftime("%Y/%m/%d")
            search_params["datetype"] = "pdat"
        
        async with httpx.AsyncClient() as client:
            # Get IDs
            search_response = await client.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                params=search_params,
                timeout=15.0
            )
            
            if search_response.status_code != 200:
                return await BraveFallbackHandler(self.site_domain).search(query, time_range, max_results)
            
            search_data = search_response.json()
            ids = search_data.get("esearchresult", {}).get("idlist", [])
            
            if not ids:
                return []
            
            # Fetch details for IDs
            fetch_params = {
                "db": "pubmed",
                "id": ",".join(ids),
                "retmode": "xml",
            }
            
            fetch_response = await client.get(
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi",
                params=fetch_params,
                timeout=15.0
            )
            
            if fetch_response.status_code != 200:
                return await BraveFallbackHandler(self.site_domain).search(query, time_range, max_results)
            
            return self._parse_pubmed_xml(fetch_response.text)
    
    def _parse_pubmed_xml(self, xml_text: str) -> List[SearchResult]:
        """Parse PubMed XML response."""
        import xml.etree.ElementTree as ET
        
        results = []
        try:
            root = ET.fromstring(xml_text)
            
            for article in root.findall(".//PubmedArticle"):
                pmid = article.find(".//PMID")
                title = article.find(".//ArticleTitle")
                abstract = article.find(".//AbstractText")
                
                # Get authors
                authors = article.findall(".//Author")
                author_names = []
                for auth in authors[:3]:
                    last = auth.find("LastName")
                    first = auth.find("ForeName")
                    if last is not None:
                        name = last.text
                        if first is not None:
                            name = f"{first.text} {name}"
                        author_names.append(name)
                author_str = ", ".join(author_names)
                if len(authors) > 3:
                    author_str += " et al."
                
                # Get publication date
                pub_date = article.find(".//PubDate")
                date_str = None
                if pub_date is not None:
                    year = pub_date.find("Year")
                    month = pub_date.find("Month")
                    if year is not None:
                        date_str = year.text
                        if month is not None:
                            date_str = f"{month.text} {date_str}"
                
                pmid_text = pmid.text if pmid is not None else ""
                
                results.append(SearchResult(
                    title=title.text if title is not None else "",
                    url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid_text}/",
                    snippet=abstract.text[:300] if abstract is not None and abstract.text else "No abstract available",
                    source_site=self.site_name,
                    published_date=date_str,
                    author=author_str,
                    metadata={
                        "pmid": pmid_text,
                        "read_time": "~10 min read",  # Average medical paper abstract + key findings
                    }
                ))
        except Exception as e:
            print(f"[PUBMED] Parse error: {e}")
        
        return results


# =============================================================================
# Brave Fallback Handler
# =============================================================================

class BraveFallbackHandler(SiteSearchHandler):
    """Fallback handler using Brave Search with site: operator."""
    
    requires_api_key = True
    api_key_env_var = "BRAVE_API_KEY"
    
    def __init__(self, site_domain: str = ""):
        self.site_domain = site_domain
        self.site_name = f"Web ({site_domain})" if site_domain else "Web"
    
    async def search(
        self, 
        query: str, 
        time_range: TimeRange = TimeRange.ALL_TIME,
        max_results: int = 10
    ) -> List[SearchResult]:
        # Try settings storage first, then fall back to env var
        api_key = get_api_key("brave_api_key") or os.getenv(self.api_key_env_var)
        
        if not api_key:
            print(f"[BRAVE] No API key found in settings or environment, returning empty results")
            return []
        
        # Build query with site: operator
        search_query = f"site:{self.site_domain} {query}" if self.site_domain else query
        
        # Time filter mapping
        freshness_map = {
            TimeRange.LAST_24H: "pd",
            TimeRange.LAST_7D: "pw",
            TimeRange.LAST_30D: "pm",
            TimeRange.LAST_90D: "py",
            TimeRange.LAST_YEAR: "py",
        }
        
        params = {
            "q": search_query,
            "count": max_results,
        }
        
        freshness = freshness_map.get(time_range)
        if freshness:
            params["freshness"] = freshness
        
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params=params,
                headers=headers,
                timeout=15.0
            )
            
            if response.status_code != 200:
                print(f"[BRAVE] Search failed: {response.status_code}")
                return []
            
            data = response.json()
            results = []
            
            for item in data.get("web", {}).get("results", []):
                snippet = item.get("description", "")
                read_time = estimate_read_time(snippet, "article")
                
                results.append(SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=snippet,
                    source_site=self.site_name,
                    published_date=item.get("age"),
                    metadata={
                        "source": "brave_search",
                        "read_time": read_time,
                    }
                ))
            
            return results


# =============================================================================
# Site Search Service (Main Interface)
# =============================================================================

class SiteSearchService:
    """Main service for site-specific search."""
    
    # Registry of site handlers
    HANDLERS: Dict[str, type] = {
        "youtube.com": YouTubeSearchHandler,
        "www.youtube.com": YouTubeSearchHandler,
        "arxiv.org": ArXivSearchHandler,
        "github.com": GitHubSearchHandler,
        "reddit.com": RedditSearchHandler,
        "www.reddit.com": RedditSearchHandler,
        "wikipedia.org": WikipediaSearchHandler,
        "en.wikipedia.org": WikipediaSearchHandler,
        "semanticscholar.org": SemanticScholarSearchHandler,
        "www.semanticscholar.org": SemanticScholarSearchHandler,
        "news.ycombinator.com": HackerNewsSearchHandler,
        "ycombinator.com": HackerNewsSearchHandler,
        "hackernews.com": HackerNewsSearchHandler,
        "stackoverflow.com": StackOverflowSearchHandler,
        "pubmed.ncbi.nlm.nih.gov": PubMedSearchHandler,
        "pubmed.gov": PubMedSearchHandler,
        "ncbi.nlm.nih.gov": PubMedSearchHandler,
    }
    
    @classmethod
    def get_supported_sites(cls) -> List[Dict[str, Any]]:
        """Get list of supported sites with metadata."""
        sites = []
        seen = set()
        
        for domain, handler_class in cls.HANDLERS.items():
            if handler_class not in seen:
                seen.add(handler_class)
                handler = handler_class()
                sites.append({
                    "domain": handler.site_domain,
                    "name": handler.site_name,
                    "requires_api_key": handler.requires_api_key,
                    "api_key_env_var": handler.api_key_env_var if handler.requires_api_key else None,
                })
        
        return sites
    
    @classmethod
    async def search(
        cls,
        query: str,
        site_domain: Optional[str] = None,
        time_range: TimeRange = TimeRange.ALL_TIME,
        max_results: int = 10
    ) -> List[SearchResult]:
        """
        Search a specific site or the web.
        
        Args:
            query: Search query
            site_domain: Optional site domain to search (e.g., "youtube.com")
            time_range: Time filter
            max_results: Maximum results to return
        
        Returns:
            List of SearchResult objects
        """
        if site_domain:
            # Normalize domain
            domain = site_domain.lower().strip()
            if domain.startswith("http"):
                domain = domain.split("//")[1].split("/")[0]
            
            # Get handler for site
            handler_class = cls.HANDLERS.get(domain)
            
            if handler_class:
                handler = handler_class()
            else:
                # Fall back to Brave with site: filter
                handler = BraveFallbackHandler(domain)
        else:
            # No site specified, use Brave general search
            handler = BraveFallbackHandler()
        
        try:
            return await handler.search(query, time_range, max_results)
        except Exception as e:
            print(f"[SITE_SEARCH] Error searching {site_domain or 'web'}: {e}")
            # Final fallback
            if site_domain:
                try:
                    return await BraveFallbackHandler(site_domain).search(query, time_range, max_results)
                except:
                    pass
            return []


# Singleton instance
site_search_service = SiteSearchService()
