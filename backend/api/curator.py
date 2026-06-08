"""Curator API endpoints for cross-notebook synthesis and oversight"""
import asyncio
import json
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agents.curator import curator, CollectedItem
import logging
logger = logging.getLogger(__name__)

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


class MentalModelFieldUpdate(BaseModel):
    """Body for PUT /curator/notebooks/{nb_id}/mental-model.

    Curator Phase 3a. User edits one field at a time. Optional `pin` flag
    also adds the field to pinned_fields (or removes via pin=false).
    """
    field: str
    value: Any
    pin: Optional[bool] = None


class AsideThumbsRequest(BaseModel):
    """Body for POST /curator/asides/{nag_id}/thumbs (Phase 3c).

    `response` is one of: 'up', 'down', 'dismissed'. Two thumbs_down
    on the same (kind, notebook_id) within 7 days triggers the
    cool-off policy in can_fire_nag.
    """
    response: str


class EngagementCaptureRequest(BaseModel):
    """Body for POST /curator/engagement (Curator Phase 2a).

    Frontend surfaces (morning brief tile, reflections panel, connection
    cards) POST here when the user takes an action — opens a brief,
    clicks a story, thumbs-up a reflection. Local-only telemetry.
    """
    kind: str
    signal: str
    subject_type: Optional[str] = None
    subject_id: Optional[str] = None
    notebook_id: Optional[str] = None
    payload: Optional[Dict[str, Any]] = None


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


@router.get("/notebook-dashboard/{notebook_id}")
async def get_notebook_dashboard(notebook_id: str):
    """Phase 13 — per-notebook dashboard HTML."""
    from datetime import datetime as _dt
    html = await curator.compose_notebook_dashboard_html(notebook_id)
    return {"html": html, "generated_at": _dt.utcnow().isoformat()}


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
        # Phase 10 — HTML dashboard variant + consensus + deep-read audit.
        "narrative_html": brief.narrative_html,
        "consensus_clusters": brief.consensus_clusters,
        "deep_reads_triggered": brief.deep_reads_triggered,
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
    except Exception as _e:
        logger.debug(f"[curator] {type(_e).__name__}: {_e}")

    aside_payload = await curator.generate_overwatch_aside(
        query=request.query,
        answer=request.answer,
        notebook_id=request.notebook_id
    )
    # Fix #5 (2026-05-23): aside_payload is now a dict (text + nag_id + kind)
    # not a bare string, so the UI can wire thumbs feedback through to
    # POST /curator/asides/{nag_id}/thumbs.
    if aside_payload:
        return {
            "aside": aside_payload.get("aside_text"),
            "nag_id": aside_payload.get("nag_id"),
            "kind": aside_payload.get("kind"),
            "curator_name": curator.name,
        }
    return {"aside": None, "nag_id": None, "kind": None, "curator_name": curator.name}


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
    from api.updates import are_models_ready
    import logging
    
    log = logging.getLogger("curator.brief")
    
    # Gate on model readiness — don't generate briefs before LLM is warm
    if not are_models_ready():
        log.debug("Brief suppressed: models still loading")
        return {"should_show": False, "should_show_weekly": False, "reason": "models_loading"}
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
        except Exception as _e:
            logger.debug(f"[curator] {type(_e).__name__}: {_e}")
    
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
    """Persist this week's wrap up so it can be recalled later.

    Guardrail: refuse to overwrite an existing wrap for the same day if the
    incoming one has an empty narrative and the existing one does not. This
    is belt-and-braces behind the curator's single-flight lock — a regressed
    second generation with no content cannot clobber a good one.
    """
    import json
    from pathlib import Path
    from services.event_logger import event_logger
    
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    wrap_dir = Path(event_logger.data_dir) / "memory"
    wrap_dir.mkdir(parents=True, exist_ok=True)
    wrap_file = wrap_dir / f"weekly_wrap_{today_str}.json"

    incoming_narrative = (wrap.get("narrative") or "").strip()
    if wrap_file.exists() and not incoming_narrative:
        try:
            existing = json.loads(wrap_file.read_text())
            existing_narrative = (existing.get("narrative") or "").strip()
            if existing_narrative:
                return {
                    "success": False,
                    "date": today_str,
                    "reason": "refused_overwrite_with_empty",
                    "cleaned": 0,
                }
        except Exception:
            pass  # Corrupt existing file — allow overwrite.

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


@router.get("/note-themes/{notebook_id}")
async def get_note_themes(notebook_id: str):
    """Extract themes from a notebook's notes and suggest new collector focus areas."""
    result = await curator.suggest_collector_keywords_from_notes(notebook_id)
    return result


class ApplyNoteSuggestionsRequest(BaseModel):
    keywords: List[str]


@router.post("/note-themes/{notebook_id}/apply")
async def apply_note_suggestions(notebook_id: str, request: ApplyNoteSuggestionsRequest):
    """Apply suggested keywords from note themes to the notebook's collector config."""
    if not request.keywords:
        raise HTTPException(status_code=400, detail="No keywords provided")
    result = await curator.apply_note_suggestions_to_collector(notebook_id, request.keywords)
    return result


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


# =========================================================================
# Phase 4: Curator Brain — User-Facing Signal API
# =========================================================================

class ConnectionFeedbackRequest(BaseModel):
    connection_id: int


class MarkSurfacedRequest(BaseModel):
    reflection_ids: List[int]


@router.post("/brain/connections/{connection_id}/dismiss")
async def dismiss_brain_connection(connection_id: int):
    """User dismisses a cross-notebook connection — never surface it again."""
    try:
        from services.curator_brain import curator_brain
        ok = curator_brain.dismiss_connection(connection_id)
        if ok:
            return {"success": True, "connection_id": connection_id}
        raise HTTPException(status_code=404, detail="Connection not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[curator-brain] dismiss_connection failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/brain/connections/{connection_id}/thumbs-up")
async def thumbs_up_brain_connection(connection_id: int):
    """User signals a connection was valuable — boosts its strength."""
    try:
        from services.curator_brain import curator_brain
        ok = curator_brain.thumbs_up_connection(connection_id)
        if ok:
            return {"success": True, "connection_id": connection_id}
        raise HTTPException(status_code=404, detail="Connection not found")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[curator-brain] thumbs_up_connection failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/brain/reflections/mark-surfaced")
async def mark_reflections_surfaced(request: MarkSurfacedRequest):
    """Mark reflections as shown to the user so they aren't repeated."""
    try:
        from services.curator_brain import curator_brain
        curator_brain.mark_reflections_surfaced(request.reflection_ids)
        return {"success": True, "marked": len(request.reflection_ids)}
    except Exception as e:
        logger.error(f"[curator-brain] mark_reflections_surfaced failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/brain-status")
async def get_brain_status():
    """Diagnostic endpoint — shows brain digest and connection health.

    Useful for debugging the Curator Brain without touching the DB directly.
    Returns: digest counts, dirty counts, active connections, unsurfaced reflections.
    """
    try:
        from services.curator_brain import curator_brain
        stats = curator_brain.get_stats()
        connections = curator_brain.get_active_connections()
        digests = curator_brain.get_all_digests()
        return {
            "stats": stats,
            "digests": [
                {
                    "notebook_id": d["notebook_id"],
                    "name": d["name"],
                    "dirty": bool(d.get("dirty")),
                    "source_count": d.get("source_count", 0),
                    "last_updated": d.get("last_updated"),
                    "has_summary": bool(d.get("current_summary")),
                    "key_themes": d.get("key_themes", "[]"),
                }
                for d in digests
            ],
            "connections": [
                {
                    "id": c["id"],
                    "notebooks": [c["notebook_a"], c["notebook_b"]],
                    "description": c["description"],
                    "strength": round(c["strength"], 3),
                    # Curator Phase 4: tier classification so the UI can
                    # render confidence-aware visuals without recomputing.
                    "tier": (
                        "strong" if (c["strength"] or 0) >= 0.7
                        else "weak" if (c["strength"] or 0) < 0.4
                        else "medium"
                    ),
                    "status": c["status"],
                    "discovered_at": c["discovered_at"],
                }
                for c in connections
            ],
        }
    except Exception as e:
        logger.error(f"[curator-brain] brain-status failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Engagement telemetry (Curator Phase 2a — 2026-05-13) ─────────────────

@router.post("/engagement")
async def record_engagement_event(request: EngagementCaptureRequest):
    """Record a UI engagement event into the curator brain.

    Local-only. Returns `{ok: True, suppressed: True}` without persisting
    when settings.engagement_tracking_enabled is False. The Phase 2b UI
    surfaces (morning brief tile, reflection cards, connection cards)
    will POST here when the user opens / clicks / thumbs / dismisses.
    """
    try:
        from config import settings
        if not getattr(settings, "engagement_tracking_enabled", True):
            return {"ok": True, "suppressed": True}

        from services.curator_brain import curator_brain
        new_id = curator_brain.record_engagement(
            kind=request.kind,
            signal=request.signal,
            subject_type=request.subject_type,
            subject_id=request.subject_id,
            notebook_id=request.notebook_id,
            payload=request.payload,
        )
        if new_id is None:
            # record_engagement returned None — either feature disabled
            # via env mid-flight, or a DB write error already logged.
            return {"ok": False, "id": None}
        return {"ok": True, "id": new_id}
    except Exception as e:
        logger.error(f"[curator] record_engagement failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/engagement")
async def get_recent_engagement(
    limit: int = 100,
    kind: Optional[str] = None,
    signal: Optional[str] = None,
    notebook_id: Optional[str] = None,
):
    """Read recent engagement events. Used by debug/observability UI."""
    try:
        from services.curator_brain import curator_brain
        return {
            "events": curator_brain.recent_engagement(
                limit=limit, kind=kind, signal=signal, notebook_id=notebook_id
            )
        }
    except Exception as e:
        logger.error(f"[curator] recent_engagement failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Plan event stream (Curator Phase 2b — 2026-05-13) ────────────────────

# Action prefixes the SSE stream forwards. Other events stay internal.
_STREAMED_ACTION_PREFIXES = ("plan_",)


@router.get("/events/stream")
async def stream_curator_events(request: Request):
    """Server-Sent Events stream of plan_* events from the curator event bus.

    Multi-client safe — each connection registers its own subscriber
    handler that pushes to a per-connection asyncio.Queue. Handler is
    unsubscribed on disconnect so handler lists don't accumulate.

    Heartbeat ping every 15s keeps the connection alive across any
    intermediate proxy timeouts.
    """
    from services.curator_event_bus import event_bus, CuratorEvent

    queue: asyncio.Queue = asyncio.Queue(maxsize=200)

    async def _on_event(event: CuratorEvent) -> None:
        # Filter at subscribe time so we don't burn queue capacity on
        # unrelated events (source_added, rag_query, etc).
        if not any(event.action.startswith(p) for p in _STREAMED_ACTION_PREFIXES):
            return
        try:
            queue.put_nowait({
                "ts": event.ts.isoformat(),
                "actor": event.actor,
                "action": event.action,
                "intent": event.intent,
                "notebook_id": event.notebook_id,
                "payload": event.payload,
                "outcome": event.outcome,
            })
        except asyncio.QueueFull:
            # Drop this event for this client rather than block the bus.
            # The next event will recover state — plan reconciliation in
            # the client is incremental but tolerates gaps.
            logger.debug("[sse] per-client queue full; dropping event")

    event_bus.subscribe(_on_event)

    async def _generator():
        # Initial ping so the client knows the connection is open.
        yield ": connected\n\n"
        try:
            while True:
                # If the client disconnected, fastapi sets is_disconnected.
                if await request.is_disconnected():
                    break
                try:
                    # Wait up to 15s for an event; if none, send heartbeat.
                    evt = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"event: curator_event\ndata: {json.dumps(evt)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        except asyncio.CancelledError:
            # Normal cleanup path when the client connection closes.
            pass
        finally:
            event_bus.unsubscribe(_on_event)
            logger.debug("[sse] client disconnected, unsubscribed handler")

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx proxy buffering
        },
    )


# Read-one endpoint for plans. Used by the PlanCard component when the
# SSE stream hasn't delivered events yet (race: collection finishes faster
# than the EventSource connection establishes). Cheap idempotent fetch
# powered by curator_brain.get_plan.
@router.get("/plans/{plan_id}")
async def get_plan_by_id(plan_id: str):
    """Return a single plan with its steps. 404 if not found."""
    try:
        from services.curator_brain import curator_brain
        plan = curator_brain.get_plan(plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="Plan not found")
        return plan
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[curator] get_plan({plan_id}) failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Mental Model endpoints (Curator Phase 3a — 2026-05-13) ──────────────

_EMPTY_MENTAL_MODEL = {
    "thesis": "",
    "goals": [],
    "audience": "",
    "stage": "",
    "blocked_on": "",
    "recent_focus": "",
    "pinned_fields": [],
    "confidence": 0.0,
    "last_inferred_at": None,
    "last_user_edit_at": None,
}

_VALID_MM_FIELDS = {"thesis", "goals", "audience", "stage", "blocked_on", "recent_focus"}


@router.get("/notebooks/{nb_id}/mental-model")
async def get_mental_model(nb_id: str):
    """Return the curator's mental model for a notebook.

    Returns an empty default shape when no model exists yet (rather than
    404) so frontend always has a renderable response.
    """
    try:
        from services.curator_brain import curator_brain
        model = curator_brain.get_mental_model(nb_id)
        if model is None:
            return {"notebook_id": nb_id, **_EMPTY_MENTAL_MODEL, "exists": False}
        return {**model, "exists": True}
    except Exception as e:
        logger.error(f"[curator] get_mental_model({nb_id}) failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/notebooks/{nb_id}/mental-model")
async def update_mental_model_field(nb_id: str, request: MentalModelFieldUpdate):
    """Set a single field on the mental model + optionally pin/unpin.

    Curator Phase 3a. by_user=True is stamped automatically; pin flag
    is handled if present in the request.
    """
    if request.field not in _VALID_MM_FIELDS:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown field '{request.field}'. Valid: {sorted(_VALID_MM_FIELDS)}",
        )
    try:
        from services.curator_brain import curator_brain
        ok = curator_brain.set_mental_model_field(
            nb_id, request.field, request.value, by_user=True,
        )
        if not ok:
            raise HTTPException(status_code=500, detail="Failed to write mental model")
        if request.pin is True:
            curator_brain.pin_field(nb_id, request.field)
        elif request.pin is False:
            curator_brain.unpin_field(nb_id, request.field)
        return curator_brain.get_mental_model(nb_id) or {}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[curator] update_mental_model_field({nb_id}, {request.field}) failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Nag-budget / aside feedback (Curator Phase 3c — 2026-05-13) ────────

@router.post("/asides/{nag_id}/thumbs")
async def record_aside_thumbs(nag_id: int, request: AsideThumbsRequest):
    """Record the user's response to a proactive curator surface.

    `response`: 'up' | 'down' | 'dismissed'. Two 'down' responses
    within 7 days for the same (kind, notebook_id) trigger cool-off:
    curator stops firing that kind of surface for that notebook for
    7 days. Backend-only in Phase 3c; UI buttons land later.
    """
    if request.response not in ("up", "down", "dismissed"):
        raise HTTPException(
            status_code=400,
            detail="response must be 'up', 'down', or 'dismissed'",
        )
    try:
        from services.curator_brain import curator_brain
        ok = curator_brain.set_nag_response(nag_id, request.response)
        if not ok:
            raise HTTPException(status_code=404, detail=f"No nag record with id {nag_id}")
        return {"ok": True, "nag_id": nag_id, "response": request.response}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[curator] record_aside_thumbs({nag_id}) failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Source stance endpoints (Curator Phase 3b — 2026-05-13) ────────────

@router.get("/notebooks/{nb_id}/stances")
async def get_notebook_stances(nb_id: str, dissent_limit: int = 5):
    """Return stance counts + top dissenting sources for the notebook.

    Used by MentalModelPanel's dissent meter. Cheap query — all rows
    aggregated server-side. Includes source titles by joining with
    source_store (best-effort).
    """
    try:
        from services.curator_brain import curator_brain
        counts = curator_brain.get_notebook_stance_counts(nb_id)
        dissent_rows = curator_brain.get_dissenting_sources(nb_id, limit=dissent_limit)

        # Best-effort: attach source titles
        try:
            from storage.source_store import source_store
            for row in dissent_rows:
                src = await source_store.get(row["source_id"])
                if src:
                    row["title"] = (
                        src.get("filename") or src.get("title") or src.get("url") or ""
                    )[:200]
                else:
                    row["title"] = row["source_id"]
        except Exception:
            for row in dissent_rows:
                row.setdefault("title", row["source_id"])

        return {
            "notebook_id": nb_id,
            "counts": counts,
            "top_dissent": dissent_rows,
            "total": sum(counts.values()),
        }
    except Exception as e:
        logger.error(f"[curator] get_notebook_stances({nb_id}) failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/notebooks/{nb_id}/sources/{source_id}/rescore")
async def rescore_single_source(nb_id: str, source_id: str):
    """Force a fresh stance scoring for one source.

    Bypasses the thesis-hash skip. Returns the new stance dict.
    Useful for "I disagree with the curator's classification — try again."
    """
    try:
        from services.curator_brain import curator_brain
        stance = await curator_brain.score_source_stance(
            notebook_id=nb_id, source_id=source_id, force=True,
        )
        if stance is None:
            raise HTTPException(
                status_code=400,
                detail="Could not score — no thesis on this notebook, or scoring failed",
            )
        return stance
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[curator] rescore_single_source({nb_id}, {source_id}) failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/notebooks/{nb_id}/stances/rescore-all")
async def rescore_all_notebook_stances(nb_id: str):
    """Kick off a background re-score of all sources in the notebook.

    Returns immediately with a queued response. Actual scoring happens
    in a background task with the batched/throttled policy. Used by
    UI "Rescore all" button + as a manual recovery from a bad thesis.
    """
    try:
        from services.curator_brain import curator_brain
        from storage.source_store import source_store
        sources = await source_store.list(nb_id)
        count = len(sources)

        # Kick off in background — don't await
        asyncio.create_task(
            curator_brain.rescore_notebook_stances(nb_id),
            name=f"stance-rescore-{nb_id[:8]}",
        )
        return {"queued": True, "source_count": count, "notebook_id": nb_id}
    except Exception as e:
        logger.error(f"[curator] rescore_all_notebook_stances({nb_id}) failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/notebooks/{nb_id}/mental-model/infer")
async def trigger_mental_model_inference(nb_id: str):
    """Force a fresh mental-model inference (bypasses the 30s debounce).

    Used by the UI 'Refresh inference' button. Pinned fields are still
    preserved. Returns the post-inference model.
    """
    try:
        from services.curator_brain import curator_brain
        model = await curator_brain.infer_mental_model(nb_id, force=True)
        if model is None:
            # Inference failed but we still have storage; return current.
            return curator_brain.get_mental_model(nb_id) or {
                "notebook_id": nb_id, **_EMPTY_MENTAL_MODEL, "exists": False,
            }
        return model
    except Exception as e:
        logger.error(f"[curator] trigger_mental_model_inference({nb_id}) failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/voice-scoreboard")
async def voice_scoreboard(lookback_days: int = 30):
    """Phase 7.2 readiness diagnostic — returns brief engagement broken down
    by voice. Lets the UI (and a future auto-rotate worker) see which
    narrative voice the user is responding to. Empty on fresh installs
    until briefs accumulate.
    """
    try:
        from services.curator_brain import curator_brain
        return curator_brain.get_voice_scoreboard(lookback_days=lookback_days)
    except Exception as e:
        logger.error(f"[curator] voice_scoreboard failed: {e}")
        return {"voices": {}, "lookback_days": lookback_days, "total_events": 0}


@router.get("/studio-scoreboard")
async def studio_scoreboard(lookback_days: int = 30):
    """Phase 7.5 readiness diagnostic — Studio output engagement by kind.
    Powers the medium-selection learning layer when it lands.
    """
    try:
        from services.curator_brain import curator_brain
        return curator_brain.get_studio_kind_scores(lookback_days=lookback_days)
    except Exception as e:
        logger.error(f"[curator] studio_scoreboard failed: {e}")
        return {"kinds": {}, "lookback_days": lookback_days}


@router.get("/notebooks/{nb_id}/source-reputation")
async def source_reputation(nb_id: str, limit: int = 50):
    """Phase 7.6 readiness diagnostic — per-source rolling acceptance rates
    for the notebook. UI can display "trending down" sources once the
    surfacing rule ships; meantime this is also a useful debugging surface.
    """
    try:
        from services.curator_brain import curator_brain
        return {"sources": curator_brain.get_source_reputation_summary(nb_id, limit=limit)}
    except Exception as e:
        logger.error(f"[curator] source_reputation({nb_id}) failed: {e}")
        return {"sources": []}


@router.get("/notebooks/{nb_id}/anticipatory-draft")
async def get_anticipatory_draft(nb_id: str):
    """Fix #3 (2026-05-23): expose the latest unconsumed anticipatory draft
    for a notebook so the CuratorPanel can show a "✨ Draft ready" pill.

    Returns null when no draft is queued — UI hides the pill in that case.
    Reading does NOT consume the draft; the user has to open it via
    `@curator show draft` for that side effect.
    """
    try:
        from services.curator_brain import curator_brain
        draft = curator_brain.get_latest_unconsumed_draft(nb_id)
        if not draft:
            return {"has_draft": False, "draft": None}
        # Trim the markdown to a preview so the panel doesn't fetch
        # the full body until the user opens it.
        preview = (draft.get("content_markdown") or "")[:300]
        return {
            "has_draft": True,
            "draft": {
                "id": draft.get("id"),
                "kind": draft.get("kind"),
                "preview": preview,
                "source_signal": draft.get("source_signal"),
                "created_at": draft.get("created_at"),
            },
        }
    except Exception as e:
        logger.error(f"[curator] get_anticipatory_draft({nb_id}) failed: {e}")
        return {"has_draft": False, "draft": None}


@router.post("/plans/{plan_id}/cancel")
async def cancel_plan(plan_id: str):
    """Request cancellation of a running plan.

    Sets an asyncio.Event the plan's runner checks at natural breakpoints.
    Returns 200 if the plan was found and signal sent, 404 if no such
    cancellable plan is registered (already done, never started, or
    not a cancellable kind).
    """
    try:
        from services.curator_brain import curator_brain
        ok = curator_brain.trigger_cancel(plan_id)
        if not ok:
            raise HTTPException(status_code=404, detail="No cancellable plan with that id")
        return {"ok": True, "plan_id": plan_id, "signal": "sent"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[curator] cancel_plan({plan_id}) failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

