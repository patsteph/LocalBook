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
    Respects oversight.overwatch_enabled config (defaults to True).
    """
    # Check if overwatch is disabled in curator config
    try:
        cfg = curator.get_config()
        oversight = cfg.get("oversight", {})
        if isinstance(oversight, dict) and oversight.get("overwatch_enabled") is False:
            return {"aside": None, "curator_name": curator.name}
    except Exception:
        pass

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
async def should_show_morning_brief(local_hour: int = -1):
    """Check if a morning brief or weekly wrap up should be displayed.
    
    Triggers when BOTH conditions are met:
    1. Time-of-day: user's local hour is in the morning window (5 AM – 12 PM),
       OR the user has been away for 8+ hours (long absence override).
    2. There is recent notebook activity to report on.
    
    The frontend passes local_hour so the backend respects the user's timezone.
    
    Returns:
    - should_show=True for a regular morning brief (Tue-Sun)
    - should_show_weekly=True on Mondays (replaces morning brief with weekly wrap up)
    """
    from services.event_logger import event_logger
    from pathlib import Path
    import logging
    
    log = logging.getLogger("curator.brief")
    now = datetime.utcnow()
    today_str = now.strftime("%Y-%m-%d")
    yesterday = now - timedelta(days=1)
    yesterday_str = yesterday.strftime("%Y-%m-%d")
    
    # ── 1. Read marker file (stores ISO timestamp of last brief shown) ──
    brief_marker = Path(event_logger.data_dir) / "memory" / "last_morning_brief.txt"
    last_shown_ts: Optional[datetime] = None
    if brief_marker.exists():
        try:
            raw = brief_marker.read_text().strip()
            # Support both legacy date-only ("2026-02-27") and new ISO format
            if "T" in raw:
                last_shown_ts = datetime.fromisoformat(raw)
            else:
                last_shown_ts = datetime.strptime(raw, "%Y-%m-%d")
        except Exception:
            pass
    
    # ── 2. Compute real hours since last brief ──
    if last_shown_ts:
        hours_since_last = (now - last_shown_ts).total_seconds() / 3600
    else:
        hours_since_last = 999  # never shown before
    
    # Already shown within last 6 hours — don't spam
    if hours_since_last < 6:
        return {"should_show": False, "should_show_weekly": False, "reason": "shown_recently", "hours_since_last": round(hours_since_last, 1)}
    
    # ── 3. Time-of-day gating ──
    # Use frontend-provided local_hour if available, otherwise fall back to UTC
    hour = local_hour if 0 <= local_hour <= 23 else now.hour
    is_morning = 5 <= hour < 12
    is_long_absence = hours_since_last >= 8
    
    if not is_morning and not is_long_absence:
        log.debug(f"Brief suppressed: local_hour={hour}, hours_since_last={hours_since_last:.1f}")
        return {"should_show": False, "should_show_weekly": False, "reason": "not_morning_and_not_long_absence", "local_hour": hour, "hours_since_last": round(hours_since_last, 1)}
    
    # ── 4. Check for activity yesterday or today ──
    has_activity = False
    events_dir = event_logger.events_dir
    for date_str in [yesterday_str, today_str]:
        log_file = events_dir / f"events_{date_str}.jsonl"
        if log_file.exists() and log_file.stat().st_size > 0:
            has_activity = True
            break
    
    if not has_activity:
        return {"should_show": False, "should_show_weekly": False, "reason": "no_recent_activity"}
    
    hours_away = max(1, min(48, round(hours_since_last)))
    
    # Monday check uses the frontend's local day-of-week context via local_hour
    # (if it's morning locally on Monday, the frontend will know)
    is_monday = now.weekday() == 0  # rough check; frontend can override
    
    if is_monday and is_morning:
        return {"should_show": False, "should_show_weekly": True, "hours_away": hours_away, "trigger": "morning" if is_morning else "long_absence"}
    
    return {"should_show": True, "should_show_weekly": False, "hours_away": hours_away, "trigger": "morning" if is_morning else "long_absence"}


@router.post("/morning-brief/mark-shown")
async def mark_morning_brief_shown():
    """Mark that the morning brief was shown — stores full ISO timestamp for accurate time tracking."""
    from pathlib import Path
    from services.event_logger import event_logger
    
    now_iso = datetime.utcnow().isoformat()
    brief_marker = Path(event_logger.data_dir) / "memory" / "last_morning_brief.txt"
    brief_marker.parent.mkdir(parents=True, exist_ok=True)
    brief_marker.write_text(now_iso)
    return {"success": True, "timestamp": now_iso}


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


# =========================================================================
# Weekly Wrap Up
# =========================================================================

@router.get("/weekly-wrap")
async def get_weekly_wrap():
    """Generate a Weekly Wrap Up covering the past 7 days."""
    wrap = await curator.generate_weekly_wrap_up()
    
    return {
        "week_start": wrap.week_start,
        "week_end": wrap.week_end,
        "notebooks": [nb.model_dump() for nb in wrap.notebook_summaries],
        "cross_notebook_insight": wrap.cross_notebook_insight,
        "narrative": wrap.narrative,
        "generated_at": wrap.generated_at.isoformat(),
        "totals": {
            "sources_added": wrap.total_sources_added,
            "collector_added": wrap.total_collector_added,
            "user_added": wrap.total_user_added,
            "conversations": wrap.total_conversations,
            "audio_generated": wrap.total_audio_generated,
            "documents_generated": wrap.total_documents_generated,
        }
    }


WEEKLY_RETENTION_WEEKS = 4


@router.post("/weekly-wrap/save")
async def save_weekly_wrap(wrap: dict):
    """Persist this week's wrap up so it can be recalled later."""
    import json
    from pathlib import Path
    from services.event_logger import event_logger
    
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    wrap_dir = Path(event_logger.data_dir) / "memory"
    wrap_dir.mkdir(parents=True, exist_ok=True)
    wrap_file = wrap_dir / f"weekly_wrap_{today_str}.json"
    wrap_file.write_text(json.dumps(wrap, default=str))
    
    # Age out old wraps
    cutoff = (datetime.utcnow() - timedelta(weeks=WEEKLY_RETENTION_WEEKS)).strftime("%Y-%m-%d")
    cleaned = 0
    for old_file in wrap_dir.glob("weekly_wrap_*.json"):
        date_part = old_file.stem.replace("weekly_wrap_", "")
        if date_part < cutoff:
            old_file.unlink()
            cleaned += 1
    
    return {"success": True, "date": today_str, "cleaned": cleaned}


@router.get("/weekly-wrap/recall")
async def recall_weekly_wrap():
    """Retrieve the most recent saved weekly wrap up."""
    import json
    from pathlib import Path
    from services.event_logger import event_logger
    
    wrap_dir = Path(event_logger.data_dir) / "memory"
    if not wrap_dir.exists():
        return {"wrap": None, "reason": "no_wraps_saved"}
    
    # Find most recent wrap file
    wrap_files = sorted(wrap_dir.glob("weekly_wrap_*.json"), reverse=True)
    if not wrap_files:
        return {"wrap": None, "reason": "no_wraps_saved"}
    
    wrap_file = wrap_files[0]
    try:
        wrap = json.loads(wrap_file.read_text())
        return {"wrap": wrap, "date": wrap_file.stem.replace("weekly_wrap_", "")}
    except Exception:
        return {"wrap": None, "reason": "parse_error"}
