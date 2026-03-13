"""
Collection History Service - Track and persist collection run history

Stores a rolling log of collection runs per notebook for the Profile view.
"""
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional

from config import settings

logger = logging.getLogger(__name__)

MAX_HISTORY_ENTRIES = 50


def _get_history_path(notebook_id: str) -> Path:
    """Get path to collection history file for a notebook"""
    notebooks_dir = Path(settings.data_dir) / "notebooks" / notebook_id
    notebooks_dir.mkdir(parents=True, exist_ok=True)
    return notebooks_dir / "collection_history.json"


def _load_history(notebook_id: str) -> List[Dict[str, Any]]:
    """Load collection history from disk"""
    path = _get_history_path(notebook_id)
    if not path.exists():
        return []
    try:
        with open(path, "r") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.error(f"Error loading collection history for {notebook_id}: {e}")
        return []


def _save_history(notebook_id: str, history: List[Dict[str, Any]]) -> None:
    """Save collection history to disk"""
    path = _get_history_path(notebook_id)
    try:
        # Keep only the most recent entries
        trimmed = history[-MAX_HISTORY_ENTRIES:]
        with open(path, "w") as f:
            json.dump(trimmed, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"Error saving collection history for {notebook_id}: {e}")


def record_collection_run(
    notebook_id: str,
    items_found: int = 0,
    items_approved: int = 0,
    items_pending: int = 0,
    items_rejected: int = 0,
    sources_checked: int = 0,
    duration_ms: float = 0,
    trigger: str = "manual",  # manual, scheduled, first_sweep
    keywords_used: Optional[List[str]] = None,
    queries_used: Optional[List[str]] = None,
    exploration_queries: Optional[List[str]] = None,
    rejection_reasons: Optional[Dict[str, int]] = None,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    """Record a collection run in the history"""
    entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "items_found": items_found,
        "items_approved": items_approved,
        "items_pending": items_pending,
        "items_rejected": items_rejected,
        "sources_checked": sources_checked,
        "duration_ms": round(duration_ms, 1),
        "trigger": trigger,
        "keywords_used": keywords_used or [],
        "queries_used": queries_used or [],
        "exploration_queries": exploration_queries or [],
        "rejection_reasons": rejection_reasons or {},
        "error": error,
    }

    history = _load_history(notebook_id)
    history.append(entry)
    _save_history(notebook_id, history)

    logger.info(
        f"Collection history recorded for {notebook_id}: "
        f"{items_found} found, {items_approved} approved, {trigger}"
    )
    return entry


def get_collection_history(
    notebook_id: str,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Get recent collection history for a notebook"""
    history = _load_history(notebook_id)
    # Most recent first
    return list(reversed(history[-limit:]))


def get_recent_queries(
    notebook_id: str,
    lookback_runs: int = 5,
) -> List[str]:
    """Get queries used in recent collection runs for rotation/dedup.
    
    Returns a flat list of all queries (smart + exploration) used in the
    last N runs. The collector uses this to avoid repeating the same
    searches and ensure each run explores different terrain.
    """
    history = _load_history(notebook_id)
    recent = history[-lookback_runs:] if history else []
    
    all_queries = []
    for h in recent:
        all_queries.extend(h.get("queries_used", []))
        all_queries.extend(h.get("exploration_queries", []))
        all_queries.extend(h.get("keywords_used", []))
    
    # Deduplicate while preserving order (most recent first)
    seen = set()
    unique = []
    for q in reversed(all_queries):
        q_lower = q.lower().strip()
        if q_lower not in seen:
            seen.add(q_lower)
            unique.append(q)
    return list(reversed(unique))


# ── Stagnation Detection ──

STAGNATION_MILD_DAYS = 5       # Widen queries, cross-notebook seeds, lower confidence floor
STAGNATION_MODERATE_DAYS = 10  # + Morning brief mention, suggest adding sources
STAGNATION_PLATEAU_DAYS = 15   # + Auto-reduce collection frequency


def detect_stagnation(notebook_id: str) -> Dict[str, Any]:
    """Analyze collection history to detect growth stagnation.

    Returns a stagnation report with:
        stagnating: bool — whether the notebook is stagnating
        severity: None | 'mild' | 'moderate' | 'plateau'
        days_since_growth: int — calendar days since last approved item
        total_dry_runs: int — consecutive scheduled runs with 0 approved
        dominant_rejection_reasons: dict — why items are failing
    """
    history = _load_history(notebook_id)

    if not history:
        return {
            "stagnating": False,
            "severity": None,
            "days_since_growth": 0,
            "total_dry_runs": 0,
            "dominant_rejection_reasons": {},
        }

    # Find the last run that approved at least one item
    last_growth_ts = None
    consecutive_dry = 0
    dry_rejection_reasons: Dict[str, int] = {}

    for entry in reversed(history):
        if entry.get("items_approved", 0) > 0:
            last_growth_ts = entry.get("timestamp")
            break
        # Only count scheduled runs (manual runs are user-triggered experiments)
        if entry.get("trigger") in ("scheduled", "first_sweep"):
            consecutive_dry += 1
            for reason, count in entry.get("rejection_reasons", {}).items():
                dry_rejection_reasons[reason] = dry_rejection_reasons.get(reason, 0) + count

    # Calculate days since last growth
    now = datetime.utcnow()
    if last_growth_ts:
        try:
            last_growth_dt = datetime.fromisoformat(last_growth_ts.replace("Z", "+00:00").replace("+00:00", ""))
            days_since = (now - last_growth_dt).days
        except (ValueError, TypeError):
            days_since = 0
    elif history:
        # Never had growth — measure from first run
        try:
            first_ts = history[0].get("timestamp", "")
            first_dt = datetime.fromisoformat(first_ts.replace("Z", "+00:00").replace("+00:00", ""))
            days_since = (now - first_dt).days
        except (ValueError, TypeError):
            days_since = 0
    else:
        days_since = 0

    # Determine severity tier
    severity = None
    stagnating = False
    if days_since >= STAGNATION_PLATEAU_DAYS:
        severity = "plateau"
        stagnating = True
    elif days_since >= STAGNATION_MODERATE_DAYS:
        severity = "moderate"
        stagnating = True
    elif days_since >= STAGNATION_MILD_DAYS:
        severity = "mild"
        stagnating = True

    if stagnating:
        logger.info(
            f"[Stagnation] {notebook_id}: severity={severity}, "
            f"days_since_growth={days_since}, dry_runs={consecutive_dry}"
        )

    return {
        "stagnating": stagnating,
        "severity": severity,
        "days_since_growth": days_since,
        "total_dry_runs": consecutive_dry,
        "dominant_rejection_reasons": dry_rejection_reasons,
    }


def get_collection_stats(notebook_id: str) -> Dict[str, Any]:
    """Get aggregate collection statistics for a notebook"""
    history = _load_history(notebook_id)

    if not history:
        return {
            "total_runs": 0,
            "total_items_found": 0,
            "total_items_approved": 0,
            "total_items_rejected": 0,
            "total_items_pending": 0,
            "avg_items_per_run": 0,
            "last_collection": None,
            "first_collection": None,
            "avg_duration_ms": 0,
            "success_rate": 0,
        }

    total_found = sum(h.get("items_found", 0) for h in history)
    total_approved = sum(h.get("items_approved", 0) for h in history)
    total_rejected = sum(h.get("items_rejected", 0) for h in history)
    total_pending = sum(h.get("items_pending", 0) for h in history)
    durations = [h.get("duration_ms", 0) for h in history if h.get("duration_ms")]
    errors = sum(1 for h in history if h.get("error"))

    return {
        "total_runs": len(history),
        "total_items_found": total_found,
        "total_items_approved": total_approved,
        "total_items_rejected": total_rejected,
        "total_items_pending": total_pending,
        "avg_items_per_run": round(total_found / len(history), 1) if history else 0,
        "last_collection": history[-1].get("timestamp") if history else None,
        "first_collection": history[0].get("timestamp") if history else None,
        "avg_duration_ms": round(sum(durations) / len(durations), 1) if durations else 0,
        "success_rate": round((len(history) - errors) / len(history) * 100, 1) if history else 0,
    }
