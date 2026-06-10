"""newsletter_scorecard — per-sender "earns its keep" grading.

Phase 2 Tier 2 (2026-06-09). Per E spec in CORRESPONDENT_TIER2_DESIGN.md.

Available signals in this slice:
  - **volume_per_week** — sources/week per sender (from source_store)
  - **highlight_rate** — highlights / sources per sender (from highlights_store)
  - **citation_rate** — DEFERRED to next session (needs RAG citation logging)
  - **action_conversion** — DEFERRED (needs in-chat CTA telemetry)
  - **read_through** — DEFERRED (needs source-viewer open event)

Composite reweights available signals to sum to 1.0 in the meantime so
the grade is meaningful from day one. Once deferred signals come online,
the formula reverts to the design defaults (40/30/20/10).

Configurable weights file: `~/Library/Application Support/LocalBook/correspondent/scorecard_weights.json`.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_WEIGHTS = {
    "highlight_rate": 0.40,
    "citation_rate": 0.30,
    "read_through": 0.20,
    "action_conversion": 0.10,
}

# Metrics whose signal pipelines are still being built. Their weights
# get redistributed across the available metrics so the composite always
# normalizes to 1.0 today and seamlessly absorbs the new metrics later.
_AVAILABLE_METRICS = {"highlight_rate"}
_DEFERRED_METRICS = {"citation_rate", "read_through", "action_conversion"}

MIN_VOLUME_FOR_GRADE = 5  # below this we show "Insufficient data" instead of F


def _weights_path() -> Path:
    base = Path(os.path.expanduser("~/Library/Application Support/LocalBook"))
    p = base / "correspondent"
    p.mkdir(parents=True, exist_ok=True)
    return p / "scorecard_weights.json"


def load_weights() -> Dict[str, float]:
    p = _weights_path()
    if p.exists():
        try:
            w = json.loads(p.read_text()) or {}
            # Merge with defaults (preserves any new metrics added later)
            out = {**DEFAULT_WEIGHTS, **{k: float(v) for k, v in w.items() if k in DEFAULT_WEIGHTS}}
            return out
        except Exception:
            pass
    return dict(DEFAULT_WEIGHTS)


def _effective_weights(raw: Dict[str, float]) -> Dict[str, float]:
    """Redistribute weight from deferred-but-not-yet-collected metrics to
    available ones so the composite normalizes to 1.0 today."""
    available_sum = sum(raw[k] for k in _AVAILABLE_METRICS)
    if available_sum <= 0:
        # Edge case: user zeroed available metrics. Just use uniform.
        return {k: (1.0 / len(_AVAILABLE_METRICS)) for k in _AVAILABLE_METRICS} | {
            k: 0.0 for k in _DEFERRED_METRICS
        }
    return {k: (raw[k] / available_sum) for k in _AVAILABLE_METRICS} | {
        k: 0.0 for k in _DEFERRED_METRICS
    }


def _score_to_grade(score: float, has_minimum_volume: bool) -> str:
    if not has_minimum_volume:
        return "—"  # insufficient data
    if score >= 0.8:
        return "A"
    if score >= 0.6:
        return "B"
    if score >= 0.4:
        return "C"
    if score >= 0.2:
        return "D"
    return "F"


async def _gather_sender_data() -> Dict[str, Dict[str, Any]]:
    """Pull raw signals per sender from existing stores.

    Returns: { sender_email: { sources: [...], highlights: int } }
    """
    from storage.source_store import source_store
    from storage.highlights_store import highlights_store

    out: Dict[str, Dict[str, Any]] = {}
    try:
        all_by_nb = await source_store.list_all() or {}
    except Exception:
        return out

    # Collect sources by sender
    for nb_id, sources in all_by_nb.items():
        for s in sources or []:
            fmt = (s.get("format") or "").lower()
            if fmt not in ("email", "forward"):
                continue
            sender = s.get("sender") or s.get("original_sender") or ""
            if not sender:
                continue
            bucket = out.setdefault(sender, {"sources": [], "highlights": 0, "notebooks": set()})
            bucket["sources"].append({
                "id": s.get("id"),
                "notebook_id": nb_id,
                "created_at": s.get("created_at") or "",
            })
            bucket["notebooks"].add(nb_id)

    # Add highlight counts per sender's sources
    # (highlights_store.list_by_notebook is the cheapest pivot today)
    seen_nbs = set()
    for sender, b in out.items():
        for nb_id in b["notebooks"]:
            if nb_id in seen_nbs:
                continue
            seen_nbs.add(nb_id)
    # Walk highlights per notebook once
    for nb_id in seen_nbs:
        try:
            highlights = await highlights_store.list_by_notebook(nb_id)
        except Exception:
            continue
        # Index by source_id
        by_source: Dict[str, int] = {}
        for h in highlights or []:
            sid = h.get("source_id")
            if sid:
                by_source[sid] = by_source.get(sid, 0) + 1
        for sender, b in out.items():
            for s in b["sources"]:
                if s.get("notebook_id") == nb_id:
                    b["highlights"] += by_source.get(s.get("id"), 0)
    # Convert notebooks set → list (for JSON serialization downstream)
    for sender in out:
        out[sender]["notebooks"] = list(out[sender]["notebooks"])
    return out


def _compute_metrics(sources: List[Dict[str, Any]], highlights: int, *, window_days: int = 30) -> Dict[str, float]:
    """Compute per-metric values from raw signals.

    window_days = rolling window for volume calculation. Default 30.
    """
    cutoff = (datetime.utcnow() - timedelta(days=window_days)).isoformat()
    recent_sources = [s for s in sources if s.get("created_at", "") >= cutoff]
    n_recent = len(recent_sources)
    volume_per_week = (n_recent / window_days) * 7 if window_days > 0 else 0.0

    # Highlight rate: highlights per source, normalized to 0-1 via tanh-ish curve.
    # 0 highlights/source = 0; 0.5/source = ~0.5; 2/source = ~0.95.
    if n_recent > 0:
        raw_rate = highlights / n_recent
        import math as _m
        highlight_rate = _m.tanh(raw_rate * 0.8)  # smooth saturating
    else:
        highlight_rate = 0.0

    return {
        "volume_per_week": round(volume_per_week, 2),
        "highlight_rate": round(highlight_rate, 3),
        "citation_rate": 0.0,
        "read_through": 0.0,
        "action_conversion": 0.0,
    }


def _composite(metrics: Dict[str, float], weights: Dict[str, float]) -> float:
    score = 0.0
    for k, w in weights.items():
        score += w * float(metrics.get(k, 0.0))
    return round(min(1.0, max(0.0, score)), 3)


async def recompute_all() -> int:
    """Rebuild the newsletter_scorecards table. Returns count of senders scored."""
    from storage.database import get_db

    data = await _gather_sender_data()
    if not data:
        return 0

    raw_weights = load_weights()
    eff_weights = _effective_weights(raw_weights)
    now_iso = datetime.utcnow().isoformat()
    conn = get_db().get_connection()
    conn.execute("DELETE FROM newsletter_scorecards")

    persisted = 0
    for sender, bucket in data.items():
        metrics = _compute_metrics(bucket["sources"], bucket["highlights"])
        n_sources = len(bucket["sources"])
        composite = _composite(metrics, eff_weights)
        grade = _score_to_grade(composite, has_minimum_volume=(n_sources >= MIN_VOLUME_FOR_GRADE))
        conn.execute(
            """INSERT INTO newsletter_scorecards
               (sender_email, volume_per_week, read_through, highlight_rate,
                citation_rate, action_conversion, composite_score, grade,
                last_built_at, trend_data)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                sender,
                metrics["volume_per_week"],
                metrics["read_through"],
                metrics["highlight_rate"],
                metrics["citation_rate"],
                metrics["action_conversion"],
                composite,
                grade,
                now_iso,
                json.dumps({}),
            ),
        )
        persisted += 1
    conn.commit()
    logger.info(f"[newsletter_scorecard] persisted {persisted} scorecards")
    return persisted


async def get_scorecard(sender_query: str) -> Optional[Dict[str, Any]]:
    """Get one scorecard. Case-insensitive sender LIKE match — first hit."""
    from storage.database import get_db
    pattern = f"%{sender_query.strip().lower()}%"
    row = get_db().get_connection().execute(
        "SELECT * FROM newsletter_scorecards WHERE LOWER(sender_email) LIKE ? "
        "ORDER BY composite_score DESC LIMIT 1",
        (pattern,),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


async def list_scorecards(*, limit: int = 30) -> List[Dict[str, Any]]:
    from storage.database import get_db
    rows = get_db().get_connection().execute(
        """SELECT * FROM newsletter_scorecards
           ORDER BY composite_score DESC LIMIT ?""",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


def grade_color(grade: str) -> str:
    """Used by the chat reply to color the grade letter."""
    return {
        "A": "🟢",
        "B": "🟢",
        "C": "🟡",
        "D": "🟠",
        "F": "🔴",
        "—": "⚪",
    }.get(grade, "⚪")
