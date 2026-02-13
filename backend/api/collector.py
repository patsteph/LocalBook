"""Collector API endpoints for per-notebook content collection"""
import asyncio
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents.collector import get_collector, CollectionMode, ApprovalMode
from services.collection_scheduler import collection_scheduler
from services.collection_history import get_collection_history, get_collection_stats
from services.stock_price import get_stock_quote
from services.key_dates import get_key_dates
from services.event_logger import log_source_approved, log_source_rejected
from services.company_profiler import company_profiler

router = APIRouter(prefix="/collector", tags=["collector"])


class CollectorConfigUpdate(BaseModel):
    name: Optional[str] = None
    subject: Optional[str] = None
    intent: Optional[str] = None
    focus_areas: Optional[List[str]] = None
    excluded_topics: Optional[List[str]] = None
    collection_mode: Optional[str] = None
    approval_mode: Optional[str] = None
    sources: Optional[Dict[str, Any]] = None
    schedule: Optional[Dict[str, Any]] = None
    filters: Optional[Dict[str, Any]] = None


class RejectionRequest(BaseModel):
    reason: str
    feedback_type: Optional[str] = None  # wrong_topic, too_old, bad_source, already_knew, other


class BatchApproveRequest(BaseModel):
    item_ids: List[str]


@router.get("/{notebook_id}/config")
async def get_collector_config(notebook_id: str):
    """Get Collector configuration for a notebook"""
    collector = get_collector(notebook_id)
    config = collector.get_config()
    return config.model_dump()


@router.put("/{notebook_id}/config")
async def update_collector_config(notebook_id: str, request: CollectorConfigUpdate):
    """Update Collector configuration"""
    collector = get_collector(notebook_id)
    
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    
    # Convert string enums to actual enums
    if "collection_mode" in updates:
        updates["collection_mode"] = CollectionMode(updates["collection_mode"])
    if "approval_mode" in updates:
        updates["approval_mode"] = ApprovalMode(updates["approval_mode"])
    
    config = collector.update_config(updates)
    return {"success": True, "config": config.model_dump()}


@router.post("/{notebook_id}/first-sweep")
async def run_first_sweep(notebook_id: str):
    """Run immediate first sweep for instant gratification"""
    collector = get_collector(notebook_id)
    result = await collector.run_first_sweep()
    return result


@router.post("/{notebook_id}/collect-now")
async def collect_now(notebook_id: str, specific_query: Optional[str] = None):
    """Trigger immediate collection run - routed through Curator"""
    import traceback
    
    try:
        print(f"[COLLECT-NOW] Starting collection for notebook {notebook_id}")
        from agents.curator import curator
        print("[COLLECT-NOW] Curator imported, calling assign_immediate_collection")
        
        # Curator orchestrates all collection - Collectors are workers
        result = await curator.assign_immediate_collection(
            notebook_id=notebook_id,
            specific_query=specific_query
        )
        print(f"[COLLECT-NOW] Collection complete: {result}")
        return result
    except Exception as e:
        error_msg = f"Collection failed: {type(e).__name__}: {str(e)}"
        print(f"[COLLECT-NOW] Error: {error_msg}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=error_msg)


@router.get("/{notebook_id}/pending")
async def get_pending_approvals(notebook_id: str):
    """Get items pending approval"""
    collector = get_collector(notebook_id)
    pending = collector.get_pending_approvals()
    expiring = collector.get_expiring_soon(days=3)
    
    return {
        "pending": pending,
        "total": len(pending),
        "expiring_soon": len(expiring)
    }


@router.post("/{notebook_id}/approve/{item_id}")
async def approve_item(notebook_id: str, item_id: str):
    """Approve a pending item"""
    collector = get_collector(notebook_id)
    success = await collector.approve_item(item_id)
    
    if not success:
        raise HTTPException(status_code=404, detail="Item not found in approval queue")
    
    try:
        log_source_approved(notebook_id, item_id, {"item_id": item_id})
    except Exception:
        pass
    return {"success": True}


@router.post("/{notebook_id}/approve-batch")
async def approve_batch(notebook_id: str, request: BatchApproveRequest):
    """Approve multiple items at once"""
    collector = get_collector(notebook_id)
    approved = await collector.approve_batch(request.item_ids)
    return {"approved": approved, "total": len(request.item_ids)}


@router.post("/{notebook_id}/approve-source/{source_name}")
async def approve_all_from_source(notebook_id: str, source_name: str):
    """Approve all items from a specific source"""
    collector = get_collector(notebook_id)
    approved = await collector.approve_all_from_source(source_name)
    return {"approved": approved, "source": source_name}


@router.post("/{notebook_id}/reject/{item_id}")
async def reject_item(notebook_id: str, item_id: str, request: RejectionRequest):
    """Reject a pending item with feedback"""
    collector = get_collector(notebook_id)
    success = await collector.reject_item(
        item_id=item_id,
        reason=request.reason,
        feedback_type=request.feedback_type
    )
    
    if not success:
        raise HTTPException(status_code=404, detail="Item not found in approval queue")
    
    try:
        log_source_rejected(notebook_id, item_id, {"item_id": item_id, "reason": request.reason, "feedback_type": request.feedback_type})
    except Exception:
        pass
    return {"success": True}


@router.get("/{notebook_id}/source-health")
async def get_source_health(notebook_id: str):
    """Get health report for all configured sources"""
    collector = get_collector(notebook_id)
    report = collector.get_source_health_report()
    return {"sources": report}


@router.get("/scheduler/status")
async def get_scheduler_status():
    """Get collection scheduler status"""
    return collection_scheduler.get_status()


@router.post("/scheduler/start")
async def start_scheduler():
    """Start the collection scheduler"""
    await collection_scheduler.start()
    return {"success": True, "status": "started"}


@router.post("/scheduler/stop")
async def stop_scheduler():
    """Stop the collection scheduler"""
    collection_scheduler.stop()
    return {"success": True, "status": "stopped"}


# =========================================================================
# Profile, History, Source Toggle, Feedback Insights
# =========================================================================


class SourceToggleRequest(BaseModel):
    source_id: str
    enabled: bool


@router.get("/{notebook_id}/profile")
async def get_collector_profile(notebook_id: str):
    """
    Get comprehensive Collector profile for the Profile view.
    Aggregates: config, company info, sources + health, schedule,
    stock quote, key dates, collection stats, and feedback insights.
    """
    collector = get_collector(notebook_id)
    config = collector.get_config()
    config_dict = config.model_dump()

    # Normalize enum values
    for key in ("collection_mode", "approval_mode"):
        val = config_dict.get(key)
        if hasattr(val, "value"):
            config_dict[key] = val.value

    # --- Gather async data in parallel ---
    company = config.company_profile or {}
    ticker = company.get("ticker") or None
    cik = company.get("cik") or None
    industry = company.get("industry") or None

    # Ticker fallback: if stored ticker looks wrong, try fast lookup from subject name
    profile_dirty = False
    if config.subject:
        fast_ticker = company_profiler._fast_ticker_lookup(config.subject)
        if fast_ticker and fast_ticker != ticker:
            print(f"[PROFILE] Correcting ticker: {ticker} → {fast_ticker} (via fast lookup for '{config.subject}')")
            ticker = fast_ticker
            company["ticker"] = ticker
            profile_dirty = True

    # IR URL healing: validate stored investor_relations URL, fix if bad
    ir_url = company.get("investor_relations")
    if ir_url:
        ir_ok = await company_profiler._validate_url(ir_url)
        if not ir_ok:
            print(f"[PROFILE] Stored IR URL failed validation: {ir_url}")
            real_ir = await company_profiler._find_investor_relations_url(
                config.subject or company.get("name", ""),
                company.get("official_website") or company.get("website"),
            )
            if real_ir:
                print(f"[PROFILE] Correcting IR URL: {ir_url} → {real_ir}")
                company["investor_relations"] = real_ir
                profile_dirty = True

    # Persist corrections so they only run once
    if profile_dirty:
        try:
            collector.update_config({"company_profile": company})
        except Exception:
            pass

    # Build parallel tasks
    async def _no_stock():
        return None

    async def _no_dates():
        return []

    stock_task = get_stock_quote(ticker) if ticker else _no_stock()
    dates_task = get_key_dates(
        company_name=config.subject or company.get("name", ""),
        ticker=ticker,
        cik=cik,
        industry=industry,
    ) if (config.subject or company.get("name")) else _no_dates()

    stock_quote, key_dates = await asyncio.gather(
        stock_task, dates_task, return_exceptions=True
    )

    # Handle exceptions gracefully
    if isinstance(stock_quote, Exception):
        stock_quote = None
    if isinstance(key_dates, Exception):
        key_dates = []

    # --- Source health ---
    source_health = collector.get_source_health_report()

    # --- Build sources list with health and enabled status ---
    sources_list = []
    disabled = set(config.disabled_sources)

    # RSS feeds
    for url in config.sources.get("rss_feeds", []):
        health_info = next((h for h in source_health if h["url"] == url), None)
        sources_list.append({
            "id": url,
            "name": url.split("//")[-1].split("/")[0] if "//" in url else url,
            "url": url,
            "type": "rss",
            "enabled": url not in disabled,
            "health": health_info["health"] if health_info else "unknown",
            "items_collected": health_info["items_collected"] if health_info else 0,
            "avg_response_ms": health_info.get("avg_response_ms", 0) if health_info else 0,
        })

    # Web pages
    for url in config.sources.get("web_pages", []):
        health_info = next((h for h in source_health if h["url"] == url), None)
        sources_list.append({
            "id": url,
            "name": url.split("//")[-1].split("/")[0] if "//" in url else url,
            "url": url,
            "type": "web",
            "enabled": url not in disabled,
            "health": health_info["health"] if health_info else "unknown",
            "items_collected": health_info["items_collected"] if health_info else 0,
        })

    # News keywords
    for kw in config.sources.get("news_keywords", []):
        sources_list.append({
            "id": f"news:{kw}",
            "name": kw,
            "url": None,
            "type": "news_keyword",
            "enabled": f"news:{kw}" not in disabled,
            "health": "healthy",
            "items_collected": 0,
        })

    # --- Collection stats ---
    stats = get_collection_stats(notebook_id)

    # --- Feedback insights ---
    feedback = await _get_feedback_insights(notebook_id)

    # --- Assemble profile ---
    profile = {
        "subject": {
            "name": config.subject or company.get("name", ""),
            "ticker": company.get("ticker"),
            "industry": company.get("industry"),
            "sector": company.get("sector"),
            "website": company.get("official_website"),
            "investor_relations": company.get("investor_relations"),
            "news_page": company.get("news_page"),
            "competitors": company.get("competitors", []),
            "key_people": company.get("key_people", []),
        },
        "stock": stock_quote.to_dict() if stock_quote and hasattr(stock_quote, "to_dict") else None,
        "key_dates": key_dates if not isinstance(key_dates, Exception) else [],
        "sources": sources_list,
        "focus_areas": config.focus_areas,
        "excluded_topics": config.excluded_topics,
        "schedule": config.schedule,
        "filters": config.filters,
        "settings": {
            "collection_mode": config_dict["collection_mode"],
            "approval_mode": config_dict["approval_mode"],
            "name": config.name,
        },
        "stats": stats,
        "feedback": feedback,
        "created_at": config.created_at.isoformat() if hasattr(config.created_at, "isoformat") else str(config.created_at),
        "updated_at": config.updated_at.isoformat() if hasattr(config.updated_at, "isoformat") else str(config.updated_at),
    }

    return profile


async def _get_feedback_insights(notebook_id: str) -> Dict[str, Any]:
    """Aggregate feedback insights from user signals for the profile view"""
    try:
        from agents.curator import curator
        preferences = await curator.get_learned_preferences(notebook_id)

        # Build human-readable insights
        insights = []
        preferred_topics = preferences.get("preferred_topics", [])
        preferred_sources = preferences.get("preferred_sources", [])
        capture_count = preferences.get("capture_count", 0)
        approval_rate = preferences.get("approval_rate", 0)
        highlight_count = preferences.get("highlight_count", 0)

        if preferred_topics:
            insights.append({
                "type": "preferred_topics",
                "icon": "trending_up",
                "message": f"You're most interested in: {', '.join(preferred_topics[:5])}",
            })

        if preferred_sources:
            insights.append({
                "type": "preferred_sources",
                "icon": "star",
                "message": f"Your trusted sources: {', '.join(preferred_sources[:3])}",
            })

        if capture_count > 0:
            insights.append({
                "type": "engagement",
                "icon": "activity",
                "message": f"You've manually captured {capture_count} item{'s' if capture_count != 1 else ''}",
            })

        if highlight_count > 0:
            insights.append({
                "type": "highlights",
                "icon": "highlight",
                "message": f"You've highlighted {highlight_count} passage{'s' if highlight_count != 1 else ''} — these strongly influence what we collect",
            })

        if approval_rate > 0:
            emoji = "high" if approval_rate > 70 else "medium" if approval_rate > 40 else "low"
            insights.append({
                "type": "approval_rate",
                "icon": "check_circle",
                "message": f"Approval rate: {approval_rate:.0f}% — {'great match!' if approval_rate > 70 else 'we are learning your preferences' if approval_rate > 40 else 'we are still calibrating'}",
                "level": emoji,
            })

        return {
            "insights": insights,
            "preferred_topics": preferred_topics[:10],
            "preferred_sources": preferred_sources[:10],
            "approval_rate": approval_rate,
            "capture_count": capture_count,
            "highlight_count": highlight_count,
        }
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Failed to get feedback insights: {e}")
        return {"insights": [], "preferred_topics": [], "preferred_sources": [], "approval_rate": 0}


@router.get("/{notebook_id}/history")
async def get_history(notebook_id: str, limit: int = 20):
    """Get collection run history"""
    history = get_collection_history(notebook_id, limit=limit)
    stats = get_collection_stats(notebook_id)
    return {"history": history, "stats": stats}


@router.post("/{notebook_id}/source-toggle")
async def toggle_source(notebook_id: str, request: SourceToggleRequest):
    """Enable or disable a specific source"""
    collector = get_collector(notebook_id)
    config = collector.get_config()

    disabled = list(config.disabled_sources)

    if request.enabled:
        # Remove from disabled list
        disabled = [s for s in disabled if s != request.source_id]
    else:
        # Add to disabled list
        if request.source_id not in disabled:
            disabled.append(request.source_id)

    collector.update_config({"disabled_sources": disabled})
    return {"success": True, "source_id": request.source_id, "enabled": request.enabled}


@router.put("/{notebook_id}/company-profile")
async def update_company_profile(notebook_id: str, profile: Dict[str, Any]):
    """Update the cached company profile for a notebook's collector"""
    collector = get_collector(notebook_id)
    collector.update_config({"company_profile": profile})
    return {"success": True}
