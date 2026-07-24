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
SIGNAL_TYPES = ("misroute", "fallback", "empty", "recovered", "degraded")
# severity:  info (recovered) < notable (misroute/empty) < warn (fallback/degraded)
_SEVERITY_RANK = {"info": 0, "notable": 1, "warn": 2}

_MAX_INPUT_CHARS = 300
_SAMPLES_PER_GROUP = 3


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
