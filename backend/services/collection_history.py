"""
Collection History Service - Track and persist collection run history

Stores a rolling log of collection runs per notebook for the Profile view.
"""
import json
import logging
from datetime import datetime
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
