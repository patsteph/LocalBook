"""Site-Specific Search API endpoints

Provides targeted search across research-focused sites.
Supports: YouTube, ArXiv, GitHub, Reddit, Wikipedia, Semantic Scholar,
          Hacker News, Stack Overflow, PubMed, and any site via Brave fallback.
"""

from typing import List, Optional
from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from services.site_search import (
    site_search_service, 
    TimeRange
)


router = APIRouter(prefix="/site-search", tags=["site-search"])


# =============================================================================
# Request/Response Models
# =============================================================================

class SiteSearchRequest(BaseModel):
    query: str = Field(description="Search query")
    site_domain: Optional[str] = Field(
        default=None, 
        description="Site domain to search (e.g., 'youtube.com'). If omitted, searches all web."
    )
    time_range: str = Field(
        default="all",
        description="Time filter: all, 24h, 7d, 14d, 30d, 90d, 1y"
    )
    max_results: int = Field(default=10, ge=1, le=50)


class SearchResultResponse(BaseModel):
    title: str
    url: str
    snippet: str
    source_site: str
    published_date: Optional[str] = None
    author: Optional[str] = None
    thumbnail: Optional[str] = None
    metadata: Optional[dict] = None


class SiteSearchResponse(BaseModel):
    query: str
    site_domain: Optional[str]
    time_range: str
    results: List[SearchResultResponse]
    total_results: int


class SupportedSite(BaseModel):
    domain: str
    name: str
    requires_api_key: bool
    api_key_env_var: Optional[str] = None


# =============================================================================
# API Endpoints
# =============================================================================

@router.get("/supported-sites", response_model=List[SupportedSite])
async def get_supported_sites():
    """Get list of sites with native search support."""
    return site_search_service.get_supported_sites()


@router.post("/search", response_model=SiteSearchResponse)
async def search(request: SiteSearchRequest):
    """
    Search a specific site or the web.
    
    Examples:
    - Site search: {"query": "quantum computing", "site_domain": "youtube.com", "time_range": "30d"}
    - Web search: {"query": "quantum computing", "time_range": "7d"}
    """
    # Parse time range
    time_range_map = {
        "all": TimeRange.ALL_TIME,
        "24h": TimeRange.LAST_24H,
        "7d": TimeRange.LAST_7D,
        "14d": TimeRange.LAST_14D,
        "30d": TimeRange.LAST_30D,
        "90d": TimeRange.LAST_90D,
        "1y": TimeRange.LAST_YEAR,
    }
    
    time_range = time_range_map.get(request.time_range, TimeRange.ALL_TIME)
    
    # Execute search
    results = await site_search_service.search(
        query=request.query,
        site_domain=request.site_domain,
        time_range=time_range,
        max_results=request.max_results
    )
    
    # Convert to response format
    result_responses = [
        SearchResultResponse(
            title=r.title,
            url=r.url,
            snippet=r.snippet,
            source_site=r.source_site,
            published_date=r.published_date,
            author=r.author,
            thumbnail=r.thumbnail,
            metadata=r.metadata
        )
        for r in results
    ]
    
    return SiteSearchResponse(
        query=request.query,
        site_domain=request.site_domain,
        time_range=request.time_range,
        results=result_responses,
        total_results=len(result_responses)
    )


@router.get("/search")
async def search_get(
    q: str = Query(..., description="Search query"),
    site: Optional[str] = Query(None, description="Site domain to search"),
    time: str = Query("all", description="Time filter: all, 24h, 7d, 14d, 30d, 90d, 1y"),
    limit: int = Query(10, ge=1, le=50, description="Max results")
):
    """
    GET endpoint for site search (convenience for browser testing).
    
    Examples:
    - /site-search/search?q=quantum+computing&site=youtube.com&time=30d
    - /site-search/search?q=machine+learning&site=arxiv.org
    """
    request = SiteSearchRequest(
        query=q,
        site_domain=site,
        time_range=time,
        max_results=limit
    )
    return await search(request)
