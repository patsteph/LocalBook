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


# ── User Engagement Tracking ──
# When the user actively engages with a notebook (adds sources, updates
# collector config, runs manual collection), we record a timestamp.
# detect_stagnation() uses this to suppress stale-research warnings
# while the user is actively working on diversifying their collection.

ENGAGEMENT_GRACE_DAYS = 3  # Suppress stagnation for N days after user engagement


def _get_engagement_path(notebook_id: str) -> Path:
    notebooks_dir = Path(settings.data_dir) / "notebooks" / notebook_id
    notebooks_dir.mkdir(parents=True, exist_ok=True)
    return notebooks_dir / "last_engagement.json"


def record_engagement(notebook_id: str, action: str = "unknown") -> None:
    """Record that the user actively engaged with this notebook's collection.

    Actions: 'config_update', 'source_upload', 'source_add', 'manual_collect'
    This resets the stagnation grace period.
    """
    path = _get_engagement_path(notebook_id)
    data = {
        "timestamp": datetime.utcnow().isoformat(),
        "action": action,
    }
    try:
        with open(path, "w") as f:
            json.dump(data, f)
        logger.info(f"[Engagement] Recorded '{action}' for {notebook_id}")
    except Exception as e:
        logger.error(f"Failed to record engagement for {notebook_id}: {e}")


def _get_last_engagement(notebook_id: str) -> Optional[datetime]:
    """Get the timestamp of the user's last engagement, or None."""
    path = _get_engagement_path(notebook_id)
    if not path.exists():
        return None
    try:
        with open(path, "r") as f:
            data = json.load(f)
        ts = data.get("timestamp", "")
        return datetime.fromisoformat(ts.replace("Z", "+00:00").replace("+00:00", ""))
    except Exception:
        return None


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

    # ── Check user engagement grace period ──
    # If the user recently added sources, updated config, or ran a manual
    # collection, suppress stagnation to avoid nagging while they're
    # actively working on improving their collection.
    last_engagement = _get_last_engagement(notebook_id)
    if last_engagement:
        engagement_age_days = (now - last_engagement).days
        if engagement_age_days < ENGAGEMENT_GRACE_DAYS:
            logger.info(
                f"[Stagnation] {notebook_id}: suppressed — user engaged "
                f"{engagement_age_days}d ago (grace={ENGAGEMENT_GRACE_DAYS}d)"
            )
            return {
                "stagnating": False,
                "severity": None,
                "days_since_growth": days_since,
                "total_dry_runs": consecutive_dry,
                "dominant_rejection_reasons": dry_rejection_reasons,
            }

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


def record_query_outcomes(
    notebook_id: str,
    query_outcomes: Dict[str, Dict[str, int]],
) -> None:
    """Record per-query approval outcomes for adaptive learning.
    
    Args:
        query_outcomes: {query_string: {"approved": N, "rejected": M, "total": T}}
    """
    if not query_outcomes:
        return
    
    path = _get_query_outcomes_path(notebook_id)
    existing = _load_query_outcomes(notebook_id)
    
    # Merge new outcomes into existing
    for query, counts in query_outcomes.items():
        key = query.lower().strip()
        if key in existing:
            existing[key]["approved"] += counts.get("approved", 0)
            existing[key]["rejected"] += counts.get("rejected", 0)
            existing[key]["total"] += counts.get("total", 0)
            existing[key]["last_used"] = datetime.utcnow().isoformat()
        else:
            existing[key] = {
                "query": query,
                "approved": counts.get("approved", 0),
                "rejected": counts.get("rejected", 0),
                "total": counts.get("total", 0),
                "first_used": datetime.utcnow().isoformat(),
                "last_used": datetime.utcnow().isoformat(),
            }
    
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(existing, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"Failed to save query outcomes for {notebook_id}: {e}")


def get_successful_query_patterns(
    notebook_id: str,
    min_approval_rate: float = 0.3,
    limit: int = 10,
) -> List[Dict[str, Any]]:
    """Get queries that historically produced approved items.
    
    Returns queries sorted by approval rate, filtered to those with
    at least one approved item and above min_approval_rate.
    Used to bias future query generation toward patterns that work.
    """
    outcomes = _load_query_outcomes(notebook_id)
    if not outcomes:
        return []
    
    successful = []
    for key, data in outcomes.items():
        total = data.get("total", 0)
        approved = data.get("approved", 0)
        if total == 0 or approved == 0:
            continue
        rate = approved / total
        if rate >= min_approval_rate:
            successful.append({
                "query": data.get("query", key),
                "approved": approved,
                "rejected": data.get("rejected", 0),
                "total": total,
                "approval_rate": round(rate, 2),
                "last_used": data.get("last_used"),
            })
    
    successful.sort(key=lambda x: x["approval_rate"], reverse=True)
    return successful[:limit]


def get_failed_query_patterns(
    notebook_id: str,
    limit: int = 8,
) -> List[str]:
    """Get queries that consistently failed (0 approved items).
    Used to tell the LLM what NOT to search for.
    """
    outcomes = _load_query_outcomes(notebook_id)
    if not outcomes:
        return []
    
    failed = []
    for key, data in outcomes.items():
        total = data.get("total", 0)
        approved = data.get("approved", 0)
        if total >= 2 and approved == 0:
            failed.append(data.get("query", key))
    
    return failed[:limit]


def _get_query_outcomes_path(notebook_id: str) -> Path:
    notebooks_dir = Path(settings.data_dir) / "notebooks" / notebook_id
    notebooks_dir.mkdir(parents=True, exist_ok=True)
    return notebooks_dir / "query_outcomes.json"


def _load_query_outcomes(notebook_id: str) -> Dict[str, Any]:
    path = _get_query_outcomes_path(notebook_id)
    if not path.exists():
        return {}
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return {}


# =========================================================================
# Phase 4: Case-Based Reasoning — Store/Reuse Successful Collection Patterns
# =========================================================================

def record_collection_pattern(
    notebook_id: str,
    pattern: Dict[str, Any],
) -> None:
    """Store a successful collection pattern for future reuse.
    
    A pattern captures: strategy used, queries that worked, source types,
    approval rate, and item characteristics — everything needed to replicate
    a successful run.
    """
    path = _get_patterns_path(notebook_id)
    patterns = _load_patterns(notebook_id)
    
    pattern["recorded_at"] = datetime.utcnow().isoformat()
    patterns.append(pattern)
    
    # Keep only last 30 patterns
    if len(patterns) > 30:
        patterns = patterns[-30:]
    
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(patterns, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"Failed to save collection pattern for {notebook_id}: {e}")


def get_best_patterns(
    notebook_id: str,
    min_approval_rate: float = 0.4,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    """Retrieve the most successful collection patterns for a notebook.
    
    Used by Curator/Collector to inform strategy selection and query generation.
    Returns patterns sorted by approval rate descending.
    """
    patterns = _load_patterns(notebook_id)
    if not patterns:
        return []
    
    good = [
        p for p in patterns
        if p.get("approval_rate", 0) >= min_approval_rate
        and p.get("items_found", 0) >= 2
    ]
    good.sort(key=lambda p: p.get("approval_rate", 0), reverse=True)
    return good[:limit]


def get_recommended_strategy(notebook_id: str) -> Optional[str]:
    """Recommend a collection strategy based on historical pattern success.
    
    Returns: 'iterative', 'deep_dive', 'standard', or None (no data).
    """
    patterns = _load_patterns(notebook_id)
    if len(patterns) < 3:
        return None  # Not enough data to recommend
    
    strategy_scores: Dict[str, List[float]] = {}
    for p in patterns[-15:]:  # Last 15 runs
        strategy = p.get("strategy", "standard")
        rate = p.get("approval_rate", 0)
        strategy_scores.setdefault(strategy, []).append(rate)
    
    if not strategy_scores:
        return None
    
    # Pick strategy with highest average approval rate
    best = max(
        strategy_scores.items(),
        key=lambda kv: sum(kv[1]) / len(kv[1]) if kv[1] else 0,
    )
    avg = sum(best[1]) / len(best[1])
    if avg >= 0.3:
        return best[0]
    return None


def _get_patterns_path(notebook_id: str) -> Path:
    notebooks_dir = Path(settings.data_dir) / "notebooks" / notebook_id
    notebooks_dir.mkdir(parents=True, exist_ok=True)
    return notebooks_dir / "collection_patterns.json"


def _load_patterns(notebook_id: str) -> List[Dict[str, Any]]:
    path = _get_patterns_path(notebook_id)
    if not path.exists():
        return []
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return []


# =========================================================================
# Phase 4: Post-Run Synthesis — Brief summary of what was collected
# =========================================================================

def record_run_synthesis(
    notebook_id: str,
    synthesis: Dict[str, Any],
) -> None:
    """Store a post-collection run synthesis for the Curator/morning brief.
    
    Synthesis includes: what was found, key themes, gaps identified,
    and quality assessment. Stored separately from raw history for
    fast morning brief generation.
    """
    path = _get_synthesis_path(notebook_id)
    syntheses = _load_syntheses(notebook_id)
    
    synthesis["recorded_at"] = datetime.utcnow().isoformat()
    syntheses.append(synthesis)
    
    # Keep last 10 syntheses
    if len(syntheses) > 10:
        syntheses = syntheses[-10:]
    
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(syntheses, f, indent=2, default=str)
    except Exception as e:
        logger.error(f"Failed to save run synthesis for {notebook_id}: {e}")


def get_recent_syntheses(
    notebook_id: str,
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """Get recent collection run syntheses for the morning brief."""
    syntheses = _load_syntheses(notebook_id)
    return syntheses[-limit:] if syntheses else []


def _get_synthesis_path(notebook_id: str) -> Path:
    notebooks_dir = Path(settings.data_dir) / "notebooks" / notebook_id
    notebooks_dir.mkdir(parents=True, exist_ok=True)
    return notebooks_dir / "collection_syntheses.json"


def _load_syntheses(notebook_id: str) -> List[Dict[str, Any]]:
    path = _get_synthesis_path(notebook_id)
    if not path.exists():
        return []
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return []


# =========================================================================
# Phase 4: Collection Quality Metrics
# =========================================================================

def get_collection_quality_metrics(notebook_id: str) -> Dict[str, Any]:
    """Comprehensive quality metrics for a notebook's collection pipeline.
    
    Returns metrics useful for the morning brief and Profile dashboard:
    - Overall health score (0-100)
    - Source diversity rating
    - Query effectiveness trend
    - Stagnation risk level
    - Recommended actions
    """
    history = _load_history(notebook_id)
    patterns = _load_patterns(notebook_id)
    query_outcomes = _load_query_outcomes(notebook_id)
    
    if not history:
        return {"health_score": 0, "status": "no_data"}
    
    # Approval rate trend (last 5 runs vs previous 5)
    recent_runs = history[-5:]
    older_runs = history[-10:-5] if len(history) > 5 else []
    
    recent_approval = _calc_approval_rate(recent_runs)
    older_approval = _calc_approval_rate(older_runs) if older_runs else recent_approval
    trend = "improving" if recent_approval > older_approval + 0.05 else (
        "declining" if recent_approval < older_approval - 0.05 else "stable"
    )
    
    # Source diversity: count unique domains in recent approved items
    unique_domains = set()
    for run in recent_runs:
        for q in run.get("queries_used", []):
            if "site:" in q:
                unique_domains.add(q.split("site:")[-1].split()[0])
    
    # Query effectiveness
    total_queries = sum(len(r.get("queries_used", [])) for r in recent_runs)
    effective_queries = 0
    for key, data in query_outcomes.items():
        if data.get("approved", 0) > 0:
            effective_queries += 1
    query_effectiveness = effective_queries / max(total_queries, 1)
    
    # Health score (0-100)
    health = 0
    health += min(40, recent_approval * 40)  # Up to 40 points from approval rate
    health += min(20, len(unique_domains) * 5)  # Up to 20 from source diversity
    health += min(20, query_effectiveness * 20)  # Up to 20 from query effectiveness
    health += min(20, len(recent_runs) * 4)  # Up to 20 from activity level
    
    # Recommended actions
    actions = []
    if recent_approval < 0.2:
        actions.append("Low approval rate — consider refining focus areas or subject")
    if len(unique_domains) < 2:
        actions.append("Low source diversity — try adding more RSS feeds or web pages")
    if query_effectiveness < 0.2:
        actions.append("Queries not producing results — subject may need refinement")
    if trend == "declining":
        actions.append("Collection quality declining — review recent rejections")
    
    return {
        "health_score": round(health),
        "approval_rate": round(recent_approval, 2),
        "approval_trend": trend,
        "source_diversity": len(unique_domains),
        "query_effectiveness": round(query_effectiveness, 2),
        "total_patterns_stored": len(patterns),
        "recommended_actions": actions,
        "status": "healthy" if health >= 60 else ("warning" if health >= 30 else "needs_attention"),
    }


def _calc_approval_rate(runs: List[Dict]) -> float:
    total_found = sum(r.get("items_found", 0) for r in runs)
    total_approved = sum(r.get("items_approved", 0) for r in runs)
    return total_approved / max(total_found, 1)


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
