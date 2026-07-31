"""Quality Signals — a local-only ledger of SILENT near-misses.

The dangerous bug class in LocalBook isn't the crash (that leaves a stack trace) — it's the
*silent quality degradation*: a path runs, returns something plausible, and quietly does the
WRONG thing. The intent classifier picks the wrong intent, an MLX call falls back to Ollama, a
retrieval returns nothing — no exception, no trace, and the user never knows.

Most of these signals are ALREADY computed and thrown away (intent `confidence`, the
`low_confidence` flag, engine-fallback branches). This module is the single sink that records
them so daily-use edges become visible + actionable — and, later, promotable to Evaluator
regression cases. Modeled on `event_logger`: append-only JSONL, daily-rotated, fsync, singleton.

Design guarantees (do not regress):
  - `record_signal(...)` NEVER raises and NEVER blocks the user path (best-effort append).
  - Local-only. Nothing leaves the device (privacy is the product).
  - Emits ONE grep-able `[signal] …` INFO log line per record, so `tail | grep signal` works.
  - The rollup is ranked by RECURRENCE — a one-off is noise, a pattern is a real edge.

See READFIRST/in-progress/quality-signals-observability.md for the full spec + phasing.
"""
import json
import os
import threading
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import logging

from config import settings

logger = logging.getLogger(__name__)


# ── Taxonomy (kept deliberately tiny) ────────────────────────────────────────
# type:     what KIND of near-miss
#   misroute  — a classifier/router chose weakly (low confidence / fell to fallback)
#   fallback  — a preferred path degraded to a backup (MLX→Ollama, cache replay)
#   empty     — a step produced nothing useful (0 chunks, "no info found")
#   recovered — a repair salvaged a bad output (JSON repair, sanitizer) — info only
#   degraded  — an output quality gate tripped (degeneration guard, heavy drop)
#   user_connection — POSITIVE first-class teach signal: the user drew a connection on
#                     the Journey Canvas (2.2.0 Walk). Not a near-miss — recorded here so
#                     the feedback surface can promote it into Evaluator/QS Phase 2 later.
SIGNAL_TYPES = ("misroute", "fallback", "empty", "recovered", "degraded", "user_connection")
# severity:  info (recovered) < notable (misroute/empty) < warn (fallback/degraded)
_SEVERITY_RANK = {"info": 0, "notable": 1, "warn": 2}

_MAX_INPUT_CHARS = 300
_SAMPLES_PER_GROUP = 3

# ── Phase 2: incident promotion thresholds (Decision #6, 2026-07-31) ─────────
# A field-edge is "promotable" only when it RECURS across days, not in a single burst.
PROMOTE_MIN_COUNT = 5   # ≥5 occurrences within the recurrence window …
PROMOTE_MIN_DAYS = 2    # … spanning ≥2 distinct days (rejects a one-session burst) …
PROMOTE_MIN_SEVERITY = "notable"  # … and at least `notable` (excludes `info`/`recovered`).


def promotion_verdict(count: int, distinct_days: int, severity: str = "notable") -> Dict[str, Any]:
    """Pure threshold check → `{eligible, min_count, min_days, reason}`. Never raises.

    Encapsulates Decision #6 so the promoter and the manual "Open incident" button share
    ONE definition of "worth escalating".
    """
    try:
        c = int(count)
    except Exception:
        c = 0
    try:
        d = int(distinct_days)
    except Exception:
        d = 0
    sev_ok = _SEVERITY_RANK.get(severity, 0) >= _SEVERITY_RANK.get(PROMOTE_MIN_SEVERITY, 1)
    eligible = c >= PROMOTE_MIN_COUNT and d >= PROMOTE_MIN_DAYS and sev_ok
    if eligible:
        reason = f"count {c}≥{PROMOTE_MIN_COUNT} over {d}≥{PROMOTE_MIN_DAYS} days, severity {severity}"
    else:
        bits = []
        if c < PROMOTE_MIN_COUNT:
            bits.append(f"count {c}<{PROMOTE_MIN_COUNT}")
        if d < PROMOTE_MIN_DAYS:
            bits.append(f"days {d}<{PROMOTE_MIN_DAYS}")
        if not sev_ok:
            bits.append(f"severity {severity}<{PROMOTE_MIN_SEVERITY}")
        reason = "; ".join(bits) or "below threshold"
    return {
        "eligible": eligible,
        "min_count": PROMOTE_MIN_COUNT,
        "min_days": PROMOTE_MIN_DAYS,
        "reason": reason,
    }


class QualitySignals:
    """Append-only sink for silent near-misses. Singleton, thread-safe, crash-safe."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self.signals_dir = Path(settings.data_dir) / "signals"
        try:
            self.signals_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:  # never let init break import
            logger.warning(f"[quality-signals] could not create dir: {e}")
        self._file_lock = threading.Lock()
        self._initialized = True
        logger.info(f"[quality-signals] ledger at {self.signals_dir}")

    def _log_path(self) -> Path:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        return self.signals_dir / f"signals_{today}.jsonl"

    def record(
        self,
        sig_type: str,
        component: str,
        detail: str,
        *,
        input_text: str = "",
        severity: str = "notable",
        key: str = "",
        notebook_id: Optional[str] = None,
    ) -> None:
        """Record ONE near-miss. Best-effort: never raises, never blocks meaningfully.

        Args:
            sig_type:   one of SIGNAL_TYPES
            component:  where it happened (e.g. "intent_classifier", "llm_service")
            detail:     one-line description of what happened instead
            input_text: the triggering input (truncated) — the "what the user asked"
            severity:   "info" | "notable" | "warn"
            key:        optional grouping sub-key (e.g. the chosen intent) so the rollup
                        can cluster "misroutes to cross_notebook_search" together
            notebook_id: optional notebook context
        """
        try:
            rec = {
                "ts": datetime.utcnow().isoformat(),
                "type": sig_type,
                "severity": severity if severity in _SEVERITY_RANK else "notable",
                "component": component,
                "key": key or "",
                "detail": (detail or "")[:400],
                "input": (input_text or "")[:_MAX_INPUT_CHARS],
                "notebook_id": notebook_id,
            }
            line = json.dumps(rec)
            with self._file_lock:
                with open(self._log_path(), "a") as f:
                    f.write(line + "\n")
                    f.flush()
            # One grep-able line so `tail backend.log | grep signal` = today's rough edges.
            _k = f" key={key}" if key else ""
            logger.info(f"[signal] {sig_type}/{severity} {component}{_k}: {rec['detail']}")
        except Exception as e:
            # A telemetry sink must NEVER take down the caller.
            logger.debug(f"[quality-signals] record failed (non-fatal): {e}")

    def get_recent(self, days: int = 7) -> List[Dict[str, Any]]:
        """Aggregate recent signals by (type, component, key), ranked by RECURRENCE.

        Returns a list of groups (most frequent first), each:
            {type, component, key, count, severity, first_seen, last_seen,
             detail (latest), samples: [recent input strings]}
        A one-off is still returned (count=1) but sorts last — the UI leads with patterns.
        """
        cutoff = datetime.utcnow() - timedelta(days=max(1, days))
        groups: Dict[tuple, Dict[str, Any]] = {}
        try:
            for log_file in sorted(self.signals_dir.glob("signals_*.jsonl")):
                # Cheap date-prefix skip for whole files older than the window.
                try:
                    fdate = datetime.strptime(log_file.stem.replace("signals_", ""), "%Y-%m-%d")
                    if fdate.date() < cutoff.date():
                        continue
                except Exception:
                    pass
                try:
                    with open(log_file, "r") as f:
                        for line in f:
                            try:
                                r = json.loads(line)
                                ts = datetime.fromisoformat(r["ts"])
                                if ts < cutoff:
                                    continue
                                gk = (r.get("type", "?"), r.get("component", "?"), r.get("key", ""))
                                g = groups.get(gk)
                                if g is None:
                                    g = groups[gk] = {
                                        "type": gk[0], "component": gk[1], "key": gk[2],
                                        "count": 0, "severity": "info",
                                        "first_seen": r["ts"], "last_seen": r["ts"],
                                        "detail": r.get("detail", ""), "samples": [],
                                    }
                                g["count"] += 1
                                if _SEVERITY_RANK.get(r.get("severity", "info"), 0) > _SEVERITY_RANK.get(g["severity"], 0):
                                    g["severity"] = r.get("severity", "info")
                                if r["ts"] > g["last_seen"]:
                                    g["last_seen"] = r["ts"]
                                    g["detail"] = r.get("detail", g["detail"])
                                if r["ts"] < g["first_seen"]:
                                    g["first_seen"] = r["ts"]
                                inp = (r.get("input") or "").strip()
                                if inp and inp not in g["samples"] and len(g["samples"]) < _SAMPLES_PER_GROUP:
                                    g["samples"].append(inp)
                            except Exception:
                                continue
                except Exception:
                    continue
        except Exception as e:
            logger.debug(f"[quality-signals] get_recent failed (non-fatal): {e}")
            return []
        # Recurring first; break ties by severity then recency.
        out = sorted(
            groups.values(),
            key=lambda g: (g["count"], _SEVERITY_RANK.get(g["severity"], 0), g["last_seen"]),
            reverse=True,
        )
        return out

    def _incident_queue_path(self) -> Path:
        """Single (non-rotated) queue of incidents awaiting explicit review/send."""
        return self.signals_dir / "incident_queue.jsonl"

    def escalate_to_incident(
        self,
        signal: Optional[Dict[str, Any]] = None,
        *,
        signal_key: str = "",
        metrics: Optional[Dict[str, Any]] = None,
        promotion: Optional[Dict[str, Any]] = None,
        version_info: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Build + scrub an incident from a signal group and QUEUE it locally.

        This is the single seam shared by the manual "Open incident" button and the
        threshold promoter. For slice 2a it does NOT touch GitHub or the network — it
        appends the scrubbed incident to `signals/incident_queue.jsonl`, mirroring the
        signals sink. Sending is an explicit, later step (Decision #5 = queue, never send).

        Args:
            signal:      the QS group/context dict (preferred). If absent, a minimal
                         group is synthesized from `signal_key`.
            signal_key:  a "type|component|key" or bare component identifier used when
                         no `signal` dict is supplied.
            metrics:     optional `{count, distinct_days, window_days}` for the payload.
            promotion:   optional promotion verdict; if omitted and `metrics` carries
                         count/distinct_days, one is computed via `promotion_verdict`.
            version_info: override for build/env facts (tests inject a fixture).

        Returns:
            The scrubbed incident dict that was queued, or None on failure. NEVER raises.
        """
        try:
            # Lazy import keeps the sink import-light and avoids any import cycle.
            from services.incident_context import build_incident_context

            group = signal if isinstance(signal, dict) else None
            if group is None:
                sk = signal_key or ""
                parts = sk.split("|")
                group = {
                    "type": parts[0] if len(parts) > 0 and parts[0] else "unknown",
                    "component": parts[1] if len(parts) > 1 and parts[1] else (sk or "unknown"),
                    "key": parts[2] if len(parts) > 2 else "",
                }

            # Auto-derive a promotion verdict when metrics are present but none given.
            verdict = promotion
            if verdict is None and isinstance(metrics, dict) and "count" in metrics:
                verdict = promotion_verdict(
                    metrics.get("count", 0),
                    metrics.get("distinct_days", 0),
                    str(group.get("severity", "notable")),
                )

            incident = build_incident_context(
                group,
                version_info=version_info,
                metrics=metrics,
                promotion=verdict,
            )

            record = {"queued_at": datetime.utcnow().isoformat(), "incident": incident}
            line = json.dumps(record)
            with self._file_lock:
                with open(self._incident_queue_path(), "a") as f:
                    f.write(line + "\n")
                    f.flush()
            logger.info(
                f"[incident] queued {incident.get('incident_id', '?')} "
                f"{incident.get('title', '')}".strip()
            )
            return incident
        except Exception as e:
            # A telemetry/escalation seam must NEVER take down the caller.
            logger.debug(f"[quality-signals] escalate failed (non-fatal): {e}")
            return None

    def cleanup_old(self, days_to_keep: int = 30) -> int:
        """Remove ledger files older than the window. Returns count removed."""
        cutoff = datetime.utcnow() - timedelta(days=days_to_keep)
        removed = 0
        try:
            for log_file in self.signals_dir.glob("signals_*.jsonl"):
                try:
                    fdate = datetime.strptime(log_file.stem.replace("signals_", ""), "%Y-%m-%d")
                    if fdate < cutoff:
                        log_file.unlink()
                        removed += 1
                except Exception:
                    continue
        except Exception:
            pass
        return removed


# Singleton + module-level convenience (mirrors event_logger's shape).
quality_signals = QualitySignals()


def record_signal(
    sig_type: str,
    component: str,
    detail: str,
    *,
    input_text: str = "",
    severity: str = "notable",
    key: str = "",
    notebook_id: Optional[str] = None,
) -> None:
    """Convenience wrapper — see QualitySignals.record. Never raises."""
    quality_signals.record(
        sig_type, component, detail,
        input_text=input_text, severity=severity, key=key, notebook_id=notebook_id,
    )


def escalate_to_incident(
    signal: Optional[Dict[str, Any]] = None,
    *,
    signal_key: str = "",
    metrics: Optional[Dict[str, Any]] = None,
    promotion: Optional[Dict[str, Any]] = None,
    version_info: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Convenience wrapper — see QualitySignals.escalate_to_incident. Never raises."""
    return quality_signals.escalate_to_incident(
        signal,
        signal_key=signal_key,
        metrics=metrics,
        promotion=promotion,
        version_info=version_info,
    )
