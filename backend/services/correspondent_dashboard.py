"""correspondent_dashboard — Phase 4 Tier 2 / I (2026-06-10).

Compute the overall Correspondent effectiveness metrics for the
@correspondent score dashboard. Each metric handles missing data
gracefully — returns None when the underlying telemetry isn't there
yet, and the HTML composer renders "—" instead of a fake value.

Per design I (locked):
  - Time windows: 3d, 7d, 30d (I.2)
  - CSS bars only — strict HTML compat (I.1)
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

WINDOW_DAYS = (3, 7, 30)


def _iso_since(days: int) -> str:
    return (datetime.utcnow() - timedelta(days=days)).isoformat()


async def compute_dashboard() -> Dict[str, Any]:
    """Compute every metric for the dashboard. Caller renders via the
    HTML composer below."""

    metrics: Dict[str, Any] = {}

    # ── Sync uptime ───────────────────────────────────────────────────
    metrics["sync_uptime"] = await _sync_uptime()

    # ── Auto-route rate (per window) ──────────────────────────────────
    metrics["auto_route_rate"] = await _auto_route_rate()

    # ── Approval throughput (avg time queued → approved) ─────────────
    metrics["approval_throughput"] = await _approval_throughput()

    # ── Dedup hit rate ────────────────────────────────────────────────
    metrics["dedup_rate"] = await _dedup_rate()

    # ── Sender learning impact ───────────────────────────────────────
    metrics["sender_learning"] = await _sender_learning_impact()

    # ── IMAP delete success rate ──────────────────────────────────────
    metrics["imap_delete"] = await _imap_delete_rate()

    # ── Subscription proposal conversion ──────────────────────────────
    metrics["subscription_conversion"] = await _subscription_conversion()

    # ── Per-newsletter quality (avg grade) ───────────────────────────
    metrics["avg_grade"] = await _avg_grade()

    return metrics


async def _sync_uptime() -> Dict[str, Any]:
    """Was each enabled account polled in the expected window? 8h poll
    cadence → expected ~3 polls/24h. Compare against actual last_polled_at
    per account."""
    try:
        from agents.correspondent import correspondent_agent
        status = correspondent_agent.status() or {}
        accounts = status.get("accounts") or {}
        if not accounts:
            return {"value": None, "label": "no accounts"}
        now = datetime.utcnow()
        stale_count = 0
        for em, info in accounts.items():
            lp = info.get("last_polled_at")
            if not lp:
                stale_count += 1
                continue
            try:
                lp_dt = datetime.fromisoformat(lp.replace("Z", ""))
                # 8h cadence + 1h grace = 9h max. Anything older = stale.
                if (now - lp_dt) > timedelta(hours=9):
                    stale_count += 1
            except Exception:
                stale_count += 1
        total = len(accounts)
        healthy = total - stale_count
        return {
            "value": healthy / total if total else None,
            "label": f"{healthy}/{total} inboxes synced recently",
        }
    except Exception as e:
        logger.debug(f"[dashboard.sync_uptime] {e}")
        return {"value": None, "label": "—"}


async def _auto_route_rate() -> Dict[str, Any]:
    """auto-routed / (auto-routed + queued), windowed."""
    try:
        from storage.database import get_db
        conn = get_db().get_connection()
        out: Dict[str, Any] = {"label": "auto-route success"}
        for days in WINDOW_DAYS:
            since = _iso_since(days)
            row = conn.execute(
                """SELECT
                       SUM(CASE WHEN decision_verb = 'route' THEN 1 ELSE 0 END) AS auto,
                       SUM(CASE WHEN decision_verb != 'route' THEN 1 ELSE 0 END) AS queued
                   FROM routing_decisions
                   WHERE ts >= ?""",
                (since,),
            ).fetchone()
            auto = (row["auto"] or 0) if row else 0
            queued = (row["queued"] or 0) if row else 0
            total = auto + queued
            out[f"d{days}"] = (auto / total) if total else None
        return out
    except Exception as e:
        logger.debug(f"[dashboard.auto_route_rate] {e}")
        return {"d3": None, "d7": None, "d30": None, "label": "—"}


async def _approval_throughput() -> Dict[str, Any]:
    """Approval throughput = avg seconds queued → approved.

    P5 (2026-06-10) — wired to correspondent_events. Logged by
    approve_queued with duration_ms = approved_at - item.created_at.
    """
    from services.correspondent_telemetry import get_approval_throughput
    data = get_approval_throughput(days=7)
    avg = data.get("avg_seconds")
    n = data.get("n", 0)
    if not n:
        return {"avg_seconds": None, "label": "no approvals logged in last 7d"}
    # Render in friendly units
    if avg < 60:
        unit = f"{avg:.0f}s"
    elif avg < 3600:
        unit = f"{avg / 60:.1f}m"
    elif avg < 86400:
        unit = f"{avg / 3600:.1f}h"
    else:
        unit = f"{avg / 86400:.1f}d"
    return {
        "avg_seconds": avg,
        "label": f"{unit} avg · {n} approvals (7d)",
        "display": unit,
    }


async def _dedup_rate() -> Dict[str, Any]:
    """Dedup hit rate = dedup_hits / (dedup_hits + inflows) over 30d.

    P5 (2026-06-10) — wired to correspondent_events. Logged by
    ingest_newsletter + ingest_forward.
    """
    from services.correspondent_telemetry import get_dedup_rate
    data = get_dedup_rate(days=30)
    rate = data.get("rate")
    d = data.get("dedup_hits", 0)
    i = data.get("inflows", 0)
    if rate is None:
        return {"value": None, "label": "no inflows logged yet"}
    return {
        "value": rate,
        "label": f"{d} caught · {i} inflows (30d)",
    }


async def _sender_learning_impact() -> Dict[str, Any]:
    """% of recent routes that applied sender bias. Read from
    routing_decisions.bias_applied where the column is non-empty."""
    try:
        from storage.database import get_db
        conn = get_db().get_connection()
        since = _iso_since(30)
        row = conn.execute(
            """SELECT
                   SUM(CASE WHEN bias_applied IS NOT NULL THEN 1 ELSE 0 END) AS biased,
                   COUNT(*) AS total
               FROM routing_decisions
               WHERE ts >= ?""",
            (since,),
        ).fetchone()
        if not row or not row["total"]:
            return {"value": None, "label": "—"}
        return {
            "value": row["biased"] / row["total"],
            "label": f"{row['biased']} of {row['total']} routes (30d) used learned bias",
        }
    except Exception as e:
        logger.debug(f"[dashboard.sender_learning] {e}")
        return {"value": None, "label": "—"}


async def _imap_delete_rate() -> Dict[str, Any]:
    """IMAP delete success rate over 30d.

    P5 (2026-06-10) — wired to correspondent_events. Logged inside the
    `_imap_delete` helper itself so every call site is covered.
    """
    from services.correspondent_telemetry import get_imap_delete_rate
    data = get_imap_delete_rate(days=30)
    rate = data.get("rate")
    total = data.get("total", 0)
    ok = data.get("ok", 0)
    if rate is None:
        return {"value": None, "label": "no delete attempts logged yet"}
    return {
        "value": rate,
        "label": f"{ok} of {total} succeeded (30d)",
    }


async def _subscription_conversion() -> Dict[str, Any]:
    """Subscription proposal acceptance rate. We have the live queue
    (pending) but don't log historical accept/dismiss decisions yet.
    Show pending count instead."""
    try:
        from agents.correspondent import correspondent_agent
        subs = correspondent_agent.list_subscription_queue()
        return {
            "value": None,
            "label": f"{len(subs)} pending proposal(s) right now",
        }
    except Exception:
        return {"value": None, "label": "—"}


async def _avg_grade() -> Dict[str, Any]:
    """Average scorecard grade across senders with ≥5 emails."""
    try:
        from storage.database import get_db
        rows = get_db().get_connection().execute(
            "SELECT grade, composite_score FROM newsletter_scorecards WHERE grade != '—'"
        ).fetchall()
        if not rows:
            return {"value": None, "label": "no graded senders yet"}
        scores = [float(r["composite_score"] or 0.0) for r in rows]
        avg = sum(scores) / len(scores)
        # Translate back to letter
        if avg >= 0.8:
            letter = "A"
        elif avg >= 0.6:
            letter = "B"
        elif avg >= 0.4:
            letter = "C"
        elif avg >= 0.2:
            letter = "D"
        else:
            letter = "F"
        return {
            "value": avg,
            "letter": letter,
            "label": f"avg across {len(rows)} graded sender(s)",
        }
    except Exception as e:
        logger.debug(f"[dashboard.avg_grade] {e}")
        return {"value": None, "label": "—"}


# ─────────────────────────────────────────────────────────────────────────
# HTML composer
# ─────────────────────────────────────────────────────────────────────────


def compose_dashboard_html(metrics: Dict[str, Any]) -> str:
    """Server-composed HTML (Tailwind subset). 3-column metric grid with
    number + label. Honest about missing data — shows "—" not zero."""
    import html as _html

    def esc(s: Any) -> str:
        return _html.escape(str(s or ""), quote=True)

    def fmt_pct(v: Optional[float]) -> str:
        if v is None:
            return "—"
        return f"{v * 100:.0f}%"

    parts: List[str] = []
    parts.append('<div class="lb-html-artifact p-4 max-w-3xl mx-auto">')
    parts.append(
        '<p class="text-xs uppercase tracking-wide text-gray-500 mb-1">📊 Correspondent score</p>'
        '<p class="text-lg font-semibold text-gray-900 mb-4">How effectively is Correspondent earning its keep?</p>'
    )

    # 3-column grid
    parts.append('<div class="grid grid-cols-3 gap-3 mb-4">')

    # Sync uptime
    su = metrics.get("sync_uptime") or {}
    parts.append(_metric_tile(
        "Sync uptime",
        fmt_pct(su.get("value")),
        su.get("label", ""),
        accent="emerald" if (su.get("value") or 0) >= 0.8 else ("amber" if (su.get("value") or 0) >= 0.5 else "red"),
    ))

    # Auto-route rate
    arr = metrics.get("auto_route_rate") or {}
    parts.append(_metric_tile(
        "Auto-route (7d)",
        fmt_pct(arr.get("d7")),
        f"3d {fmt_pct(arr.get('d3'))} · 30d {fmt_pct(arr.get('d30'))}",
        accent="emerald",
    ))

    # Sender learning
    sl = metrics.get("sender_learning") or {}
    parts.append(_metric_tile(
        "Sender learning (30d)",
        fmt_pct(sl.get("value")),
        sl.get("label", ""),
        accent="blue",
    ))

    # Avg grade
    ag = metrics.get("avg_grade") or {}
    grade_letter = ag.get("letter") or "—"
    parts.append(_metric_tile(
        "Avg quality grade",
        grade_letter,
        ag.get("label", ""),
        accent="emerald" if grade_letter in ("A", "B") else ("amber" if grade_letter == "C" else "red"),
    ))

    # Approval throughput
    ath = metrics.get("approval_throughput") or {}
    parts.append(_metric_tile(
        "Approval throughput",
        ath.get("display") or "—",
        ath.get("label", ""),
        accent="blue",
    ))

    # Dedup rate
    dr = metrics.get("dedup_rate") or {}
    parts.append(_metric_tile(
        "Dedup catch rate",
        fmt_pct(dr.get("value")),
        dr.get("label", ""),
        accent="emerald" if (dr.get("value") or 0) >= 0.05 else "gray",
    ))

    # IMAP delete rate
    idel = metrics.get("imap_delete") or {}
    parts.append(_metric_tile(
        "IMAP delete success",
        fmt_pct(idel.get("value")),
        idel.get("label", ""),
        accent="emerald" if (idel.get("value") or 0) >= 0.9 else ("amber" if (idel.get("value") or 0) >= 0.5 else "red"),
    ))

    parts.append('</div>')

    parts.append(
        '<p class="text-xs text-gray-500 italic">'
        'Metrics that show "—" haven\'t had data logged yet — populate as activity happens.'
        '</p>'
    )
    parts.append('</div>')
    return "".join(parts)


def _metric_tile(title: str, value: str, sub: str, *, accent: str = "blue") -> str:
    """Single metric card — uses the Tailwind subset that the strict
    HtmlArtifactRenderer ships with."""
    import html as _html
    palette = {
        "blue":    ("border-blue-200 bg-blue-50",       "text-blue-700"),
        "emerald": ("border-emerald-200 bg-emerald-50", "text-emerald-700"),
        "amber":   ("border-amber-200 bg-amber-50",     "text-amber-700"),
        "red":     ("border-red-200 bg-red-50",         "text-red-700"),
        "gray":    ("border-gray-200 bg-gray-50",       "text-gray-700"),
    }
    box_cls, label_cls = palette.get(accent, palette["blue"])
    return (
        f'<div class="rounded-lg border {box_cls} p-3 text-center">'
        f'<p class="text-xs uppercase tracking-wide {label_cls} mb-1">{_html.escape(title)}</p>'
        f'<p class="text-2xl font-semibold text-gray-900">{_html.escape(value)}</p>'
        f'<p class="text-xs text-gray-500 mt-1">{_html.escape(sub)}</p>'
        '</div>'
    )
