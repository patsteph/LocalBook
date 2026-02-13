"""Curator API endpoints for cross-notebook synthesis and oversight"""
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents.curator import curator, CollectedItem

router = APIRouter(prefix="/curator", tags=["curator"])


class JudgeItemsRequest(BaseModel):
    collector_id: str
    notebook_intent: str
    items: List[Dict[str, Any]]


class SynthesisRequest(BaseModel):
    query: str
    notebook_ids: Optional[List[str]] = None


class CounterargumentsRequest(BaseModel):
    notebook_id: str
    thesis: Optional[str] = None


class ConfigUpdateRequest(BaseModel):
    name: Optional[str] = None
    personality: Optional[str] = None
    oversight: Optional[Dict[str, Any]] = None
    synthesis: Optional[Dict[str, Any]] = None
    voice: Optional[Dict[str, Any]] = None


class CuratorChatRequest(BaseModel):
    message: str
    notebook_id: Optional[str] = None
    history: Optional[List[Dict[str, str]]] = None


class OverwatchRequest(BaseModel):
    query: str
    answer: str
    notebook_id: str


@router.get("/config")
async def get_curator_config():
    """Get current Curator configuration"""
    return curator.get_config()


@router.put("/config")
async def update_curator_config(request: ConfigUpdateRequest):
    """Update Curator configuration (name, personality, etc.)"""
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")
    
    config = curator.update_config(updates)
    return {"success": True, "config": config}


@router.post("/judge")
async def judge_collected_items(request: JudgeItemsRequest):
    """Have Curator judge items proposed by a Collector"""
    items = [
        CollectedItem(
            id=item.get("id", ""),
            title=item.get("title", ""),
            url=item.get("url"),
            preview=item.get("preview", ""),
            source_name=item.get("source_name", "unknown"),
            collected_at=datetime.fromisoformat(item.get("collected_at", datetime.utcnow().isoformat())),
            relevance_score=item.get("relevance_score", 0.5),
            source_trust=item.get("source_trust", 0.5),
            freshness_score=item.get("freshness_score", 0.5),
            overall_confidence=item.get("overall_confidence", 0.5),
            confidence_reasons=item.get("confidence_reasons", [])
        )
        for item in request.items
    ]
    
    results = await curator.judge_collection(
        collector_id=request.collector_id,
        proposed_items=items,
        notebook_intent=request.notebook_intent
    )
    
    return {
        "judgments": [
            {
                "item_id": items[i].id,
                "decision": results[i].decision.value,
                "reason": results[i].reason,
                "confidence": results[i].confidence,
                "modifications": results[i].modifications
            }
            for i in range(len(results))
        ]
    }


@router.post("/synthesize")
async def synthesize_across_notebooks(request: SynthesisRequest):
    """Synthesize information across multiple notebooks"""
    result = await curator.synthesize_across_notebooks(
        query=request.query,
        notebook_ids=request.notebook_ids
    )
    return result


@router.get("/morning-brief")
async def get_morning_brief(hours_away: int = 8):
    """Generate morning brief based on time away"""
    last_seen = datetime.utcnow() - timedelta(hours=hours_away)
    brief = await curator.generate_morning_brief(last_seen)
    
    return {
        "away_duration": brief.away_duration,
        "notebooks": [s.model_dump() for s in brief.notebook_summaries],
        "cross_notebook_insight": brief.cross_notebook_insight,
        "narrative": brief.narrative,
        "generated_at": brief.generated_at.isoformat()
    }


@router.post("/discover-patterns")
async def discover_cross_notebook_patterns():
    """Run cross-notebook pattern discovery"""
    insights = await curator.discover_cross_notebook_patterns()
    return {
        "insights": [
            {
                "type": i.insight_type,
                "entity": i.entity,
                "notebooks": i.notebooks,
                "summary": i.summary,
                "confidence": i.confidence
            }
            for i in insights
        ]
    }


@router.post("/devils-advocate")
async def find_counterarguments(request: CounterargumentsRequest):
    """Find contradicting evidence (Devil's Advocate mode)"""
    result = await curator.find_counterarguments(
        notebook_id=request.notebook_id,
        thesis=request.thesis
    )
    
    return {
        "inferred_thesis": result.inferred_thesis,
        "counterpoints": result.counterpoints,
        "confidence": result.confidence
    }


@router.get("/insight-for-query")
async def get_insight_for_query(query: str):
    """Check if there's a relevant proactive insight for a query"""
    insight = await curator.surface_insight_if_relevant(query)
    return {"insight": insight}


@router.get("/setup-followup/{notebook_id}")
async def get_setup_followup(notebook_id: str):
    """Generate a contextual follow-up message after notebook setup"""
    message = await curator.generate_setup_followup(notebook_id)
    return {"message": message, "curator_name": curator.name}


class InferConfigRequest(BaseModel):
    notebook_id: str
    filenames: List[str]
    sample_content: str = ""


@router.post("/chat")
async def curator_chat(request: CuratorChatRequest):
    """Conversational interaction with the Curator.
    
    The Curator can synthesize across notebooks, surface insights,
    play devil's advocate, and discuss research strategy.
    """
    result = await curator.conversational_reply(
        message=request.message,
        notebook_id=request.notebook_id,
        history=request.history or []
    )
    return {
        "reply": result,
        "curator_name": curator.name
    }


@router.post("/overwatch")
async def curator_overwatch(request: OverwatchRequest):
    """Check if the Curator has cross-notebook insights for a chat answer.
    
    Called after each RAG answer. Returns an aside if relevant, or null.
    Should be lightweight — only surfaces genuinely useful cross-notebook context.
    """
    aside = await curator.generate_overwatch_aside(
        query=request.query,
        answer=request.answer,
        notebook_id=request.notebook_id
    )
    if aside:
        return {
            "aside": aside,
            "curator_name": curator.name
        }
    return {"aside": None, "curator_name": curator.name}


@router.post("/infer-config")
async def infer_config_from_content(request: InferConfigRequest):
    """Analyze dropped files and suggest Collector configuration"""
    result = await curator.infer_config_from_content(
        notebook_id=request.notebook_id,
        filenames=request.filenames,
        sample_content=request.sample_content
    )
    return result


@router.get("/morning-brief/should-show")
async def should_show_morning_brief():
    """Check if a morning brief should be displayed.
    
    Returns should_show=True when:
    1. There was user activity (events) logged yesterday or earlier today
    2. A morning brief hasn't already been shown today
    
    The frontend calls this on app focus/visibility to catch the case
    where the app was left running overnight.
    """
    from services.event_logger import event_logger
    from pathlib import Path
    
    now = datetime.utcnow()
    today_str = now.strftime("%Y-%m-%d")
    yesterday = now - timedelta(days=1)
    yesterday_str = yesterday.strftime("%Y-%m-%d")
    
    # Check if we already showed a brief today
    brief_marker = Path(event_logger.data_dir) / "memory" / "last_morning_brief.txt"
    if brief_marker.exists():
        try:
            last_brief_date = brief_marker.read_text().strip()
            if last_brief_date == today_str:
                return {"should_show": False, "reason": "already_shown_today"}
        except Exception:
            pass
    
    # Check for activity yesterday or today
    has_activity = False
    events_dir = event_logger.events_dir
    for date_str in [yesterday_str, today_str]:
        log_file = events_dir / f"events_{date_str}.jsonl"
        if log_file.exists() and log_file.stat().st_size > 0:
            has_activity = True
            break
    
    if not has_activity:
        return {"should_show": False, "reason": "no_recent_activity"}
    
    return {"should_show": True, "hours_away": max(1, min(24, int((now - yesterday).total_seconds() / 3600)))}


@router.post("/morning-brief/mark-shown")
async def mark_morning_brief_shown():
    """Mark that the morning brief was shown today so we don't repeat it."""
    from pathlib import Path
    from services.event_logger import event_logger
    
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    brief_marker = Path(event_logger.data_dir) / "memory" / "last_morning_brief.txt"
    brief_marker.parent.mkdir(parents=True, exist_ok=True)
    brief_marker.write_text(today_str)
    return {"success": True, "date": today_str}


BRIEF_RETENTION_DAYS = 7


@router.post("/morning-brief/save")
async def save_morning_brief(brief: dict):
    """Persist today's morning brief so it can be recalled later.
    Also cleans up briefs older than BRIEF_RETENTION_DAYS."""
    import json
    from pathlib import Path
    from services.event_logger import event_logger
    
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    brief_dir = Path(event_logger.data_dir) / "memory"
    brief_dir.mkdir(parents=True, exist_ok=True)
    brief_file = brief_dir / f"morning_brief_{today_str}.json"
    brief_file.write_text(json.dumps(brief, default=str))
    
    # Age out old briefs
    cutoff = (datetime.utcnow() - timedelta(days=BRIEF_RETENTION_DAYS)).strftime("%Y-%m-%d")
    cleaned = 0
    for old_file in brief_dir.glob("morning_brief_*.json"):
        date_part = old_file.stem.replace("morning_brief_", "")
        if date_part < cutoff:
            old_file.unlink()
            cleaned += 1
    
    return {"success": True, "date": today_str, "cleaned": cleaned}


@router.get("/morning-brief/recall")
async def recall_morning_brief():
    """Retrieve today's saved morning brief (or most recent)."""
    import json
    from pathlib import Path
    from services.event_logger import event_logger
    
    brief_dir = Path(event_logger.data_dir) / "memory"
    if not brief_dir.exists():
        return {"brief": None, "reason": "no_briefs_saved"}
    
    # Try today first, then scan for most recent
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    brief_file = brief_dir / f"morning_brief_{today_str}.json"
    
    if not brief_file.exists():
        # Find most recent brief file
        brief_files = sorted(brief_dir.glob("morning_brief_*.json"), reverse=True)
        if not brief_files:
            return {"brief": None, "reason": "no_briefs_saved"}
        brief_file = brief_files[0]
    
    try:
        brief = json.loads(brief_file.read_text())
        return {"brief": brief, "date": brief_file.stem.replace("morning_brief_", "")}
    except Exception:
        return {"brief": None, "reason": "parse_error"}
