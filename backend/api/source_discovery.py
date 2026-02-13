"""
Source Discovery API - Endpoints for intelligent source discovery

Flow:
1. POST /discover - Trigger discovery from intent
2. POST /validate - Curator validates discovered sources
3. POST /approve - User approves/rejects sources
4. GET /status - Check discovery status
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any

from services.source_discovery import source_discovery
from services.company_profiler import company_profiler
from agents.curator import curator
from agents.collector import get_collector

router = APIRouter(prefix="/source-discovery", tags=["source-discovery"])


class CompanyDetails(BaseModel):
    """User-provided company details for clarification"""
    name: str
    ticker: Optional[str] = None
    industry: Optional[str] = None

class DiscoverRequest(BaseModel):
    """Request to discover sources for a notebook"""
    notebook_id: str
    subject: str = ""  # Key research entity (e.g. "Costco") - takes priority over LLM extraction
    intent: str
    focus_areas: List[str] = Field(default_factory=list)
    override_purpose: Optional[str] = None  # User-specified purpose if intent was ambiguous
    company_details: Optional[CompanyDetails] = None  # User-provided company info if lookup failed


class DiscoverResponse(BaseModel):
    """Response from source discovery"""
    notebook_id: str
    intent_analysis: Dict[str, Any]
    sources: List[Dict[str, Any]]
    discovery_time_ms: float
    company_profile: Optional[Dict[str, Any]] = None


class ValidateRequest(BaseModel):
    """Request to have Curator validate sources"""
    notebook_id: str
    intent: str
    sources: List[Dict[str, Any]]


class ApproveSourcesRequest(BaseModel):
    """Request to approve/reject discovered sources"""
    notebook_id: str
    approved_sources: List[Dict[str, Any]]
    rejected_sources: List[Dict[str, Any]] = Field(default_factory=list)


class ApproveSourcesResponse(BaseModel):
    """Response after approving sources"""
    notebook_id: str
    sources_added: int
    sources_rejected: int
    collector_config_updated: bool


@router.post("/discover", response_model=DiscoverResponse)
async def discover_sources(request: DiscoverRequest):
    """
    Discover sources based on user intent.
    
    This is the magic - takes intent and returns:
    - Company intelligence (if company research)
    - Industry RSS feeds
    - News sources
    - YouTube keywords
    - arXiv categories (if deep research)
    """
    import traceback
    
    try:
        print(f"[DISCOVERY] Starting discovery for intent: {request.intent[:100]}...")
        
        # Gather existing source URLs for seed-based discovery
        existing_urls = []
        try:
            from storage.source_store import source_store
            existing_sources = await source_store.list(request.notebook_id)
            existing_urls = [s.get("url") for s in existing_sources if s.get("url")]
            if existing_urls:
                print(f"[DISCOVERY] Found {len(existing_urls)} existing source URLs for seed discovery")
        except Exception:
            pass
        
        # Step 1: Run source discovery
        result = await source_discovery.discover_sources(
            intent=request.intent,
            focus_areas=request.focus_areas,
            subject=request.subject,
            override_purpose=request.override_purpose,
            company_details=request.company_details.model_dump() if request.company_details else None,
            existing_source_urls=existing_urls if existing_urls else None
        )
        print(f"[DISCOVERY] Found {len(result.sources)} raw sources, errors: {result.errors}")
        
        # If discovery itself had errors but still returned sources, continue
        if result.errors and not result.sources:
            raise Exception(f"Discovery failed: {'; '.join(result.errors)}")
        
        # Step 2: If company research, get detailed company profile
        company_profile = None
        if result.intent_analysis.is_company_research and result.intent_analysis.company_name:
            try:
                print(f"[DISCOVERY] Profiling company: {result.intent_analysis.company_name}")
                profile = await company_profiler.profile_company(result.intent_analysis.company_name)
                company_profile = profile.model_dump()
                
                # Enrich ticker if not found
                if not result.intent_analysis.company_ticker and profile.ticker:
                    result.intent_analysis.company_ticker = profile.ticker
                
                # Persist company profile to collector config for the Profile view
                try:
                    collector = get_collector(request.notebook_id)
                    collector.update_config({"company_profile": company_profile})
                    print(f"[DISCOVERY] Company profile saved to collector config")
                except Exception as save_err:
                    print(f"[DISCOVERY] Failed to save company profile (non-fatal): {save_err}")
            except Exception as profile_err:
                print(f"[DISCOVERY] Company profiling failed (non-fatal): {profile_err}")
        
        # Step 3: Have Curator validate and rank sources
        sources_dict = [s.model_dump() for s in result.sources]
        print(f"[DISCOVERY] Curator validating {len(sources_dict)} sources...")
        
        validated_sources = await curator.validate_discovered_sources(
            notebook_id=request.notebook_id,
            intent=request.intent,
            discovered_sources=sources_dict
        )
        print(f"[DISCOVERY] Validation complete, returning {len(validated_sources)} sources")
        
        return DiscoverResponse(
            notebook_id=request.notebook_id,
            intent_analysis=result.intent_analysis.model_dump(),
            sources=validated_sources,
            discovery_time_ms=result.discovery_time_ms,
            company_profile=company_profile
        )
        
    except Exception as e:
        error_detail = f"Discovery failed: {str(e)}"
        print(f"[DISCOVERY] ERROR: {error_detail}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=error_detail)


@router.post("/validate")
async def validate_sources(request: ValidateRequest):
    """
    Have Curator validate a list of sources.
    Used when user wants to re-validate or add custom sources.
    """
    try:
        validated = await curator.validate_discovered_sources(
            notebook_id=request.notebook_id,
            intent=request.intent,
            discovered_sources=request.sources
        )
        
        return {
            "notebook_id": request.notebook_id,
            "validated_sources": validated
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Validation failed: {str(e)}")


@router.post("/approve", response_model=ApproveSourcesResponse)
async def approve_sources(request: ApproveSourcesRequest):
    """
    User approves/rejects discovered sources.
    Approved sources are added to the Collector's config.
    """
    try:
        collector = get_collector(request.notebook_id)
        config = collector.get_config()
        
        # Initialize sources dict if empty
        if not config.sources:
            config.sources = {
                "rss_feeds": [],
                "web_pages": [],
                "youtube_keywords": [],
                "arxiv_categories": [],
                "sec_tickers": [],
                "news_keywords": []
            }
        
        sources_added = 0
        
        for source in request.approved_sources:
            source_type = source.get("source_type", "")
            url = source.get("url") or source.get("rss_url")
            name = source.get("name", "")
            
            # Add to appropriate category in collector config
            if source_type in ["rss_feed", "RSS_FEED"]:
                if url and url not in config.sources.get("rss_feeds", []):
                    config.sources.setdefault("rss_feeds", []).append(url)
                    sources_added += 1
                    
            elif source_type in ["web_page", "WEB_PAGE", "company_news", "COMPANY_NEWS"]:
                if url and url not in config.sources.get("web_pages", []):
                    config.sources.setdefault("web_pages", []).append(url)
                    sources_added += 1
                    
            elif source_type in ["youtube_keyword", "YOUTUBE_KEYWORD"]:
                keyword = source.get("metadata", {}).get("keyword", name)
                if keyword and keyword not in config.sources.get("youtube_keywords", []):
                    config.sources.setdefault("youtube_keywords", []).append(keyword)
                    sources_added += 1
                    
            elif source_type in ["arxiv_category", "ARXIV_CATEGORY"]:
                category = source.get("metadata", {}).get("category")
                if category and category not in config.sources.get("arxiv_categories", []):
                    config.sources.setdefault("arxiv_categories", []).append(category)
                    sources_added += 1
                    
            elif source_type in ["sec_filing", "SEC_FILING"]:
                ticker = source.get("metadata", {}).get("ticker")
                company_name = source.get("metadata", {}).get("company_name")
                if ticker:
                    # Store as dict with both ticker and company_name for precise EDGAR queries
                    sec_entry = {"ticker": ticker, "company_name": company_name or ticker}
                    existing_tickers = [
                        (e["ticker"] if isinstance(e, dict) else e) 
                        for e in config.sources.get("sec_tickers", [])
                    ]
                    if ticker not in existing_tickers:
                        config.sources.setdefault("sec_tickers", []).append(sec_entry)
                        sources_added += 1
                    
            elif source_type in ["news_keyword", "NEWS_KEYWORD"]:
                keyword = source.get("metadata", {}).get("keyword", name)
                if keyword and keyword not in config.sources.get("news_keywords", []):
                    config.sources.setdefault("news_keywords", []).append(keyword)
                    sources_added += 1
        
        # Save updated config
        collector.update_config({"sources": config.sources})
        
        # Learn from user's decisions
        await curator.learn_from_source_decisions(
            notebook_id=request.notebook_id,
            approved_sources=request.approved_sources,
            rejected_sources=request.rejected_sources
        )
        
        return ApproveSourcesResponse(
            notebook_id=request.notebook_id,
            sources_added=sources_added,
            sources_rejected=len(request.rejected_sources),
            collector_config_updated=True
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Source approval failed: {str(e)}")


@router.get("/{notebook_id}/sources")
async def get_configured_sources(notebook_id: str):
    """Get the currently configured sources for a notebook's Collector"""
    try:
        collector = get_collector(notebook_id)
        config = collector.get_config()
        
        return {
            "notebook_id": notebook_id,
            "sources": config.sources,
            "intent": config.intent,
            "focus_areas": config.focus_areas
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to get sources: {str(e)}")


@router.delete("/{notebook_id}/sources/{source_type}/{source_index}")
async def remove_source(notebook_id: str, source_type: str, source_index: int):
    """Remove a specific source from the Collector's config"""
    try:
        collector = get_collector(notebook_id)
        config = collector.get_config()
        
        # Map source_type to config key
        type_map = {
            "rss": "rss_feeds",
            "web": "web_pages",
            "youtube": "youtube_keywords",
            "arxiv": "arxiv_categories",
            "sec": "sec_tickers",
            "news": "news_keywords"
        }
        
        config_key = type_map.get(source_type)
        if not config_key or config_key not in config.sources:
            raise HTTPException(status_code=400, detail=f"Invalid source type: {source_type}")
        
        sources_list = config.sources.get(config_key, [])
        if source_index < 0 or source_index >= len(sources_list):
            raise HTTPException(status_code=400, detail=f"Invalid source index: {source_index}")
        
        removed = sources_list.pop(source_index)
        collector.update_config({"sources": config.sources})
        
        return {
            "removed": removed,
            "remaining_count": len(sources_list)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to remove source: {str(e)}")


@router.post("/{notebook_id}/add-custom")
async def add_custom_source(notebook_id: str, source: Dict[str, Any]):
    """Add a custom source manually"""
    try:
        # Validate the source with Curator
        collector = get_collector(notebook_id)
        config = collector.get_config()
        
        validated = await curator.validate_discovered_sources(
            notebook_id=notebook_id,
            intent=config.intent,
            discovered_sources=[source]
        )
        
        if validated:
            # Use the approve endpoint logic
            approve_request = ApproveSourcesRequest(
                notebook_id=notebook_id,
                approved_sources=validated
            )
            return await approve_sources(approve_request)
        
        return {"added": False, "reason": "Source validation failed"}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to add custom source: {str(e)}")
