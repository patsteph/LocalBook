#!/usr/bin/env python3
"""Activity-ledger backfill — synthesize history for pre-Phase-B notebooks.

2026-05-23. The `activity_ledger` table (Phase B, 2026-05-22) only captures
events from the moment it landed. Notebooks created before that have real
history in source_store + collection_history.json but no rows in the
ledger — which means engagement_active(), collector_dry(), and friends
return misleading "no data" answers for them.

This script scans every notebook on disk and synthesizes back-dated
activity_events rows so the ledger views work uniformly across old + new
notebooks. Idempotent: re-running adds nothing if the same events are
already present.

How to invoke:
    cd backend
    python scripts/backfill_activity_ledger.py

What gets backfilled per notebook:
    1. KIND_SOURCE_ADDED — one row per source in source_store, ts =
       source.created_at, payload.via = source.type or 'backfill'
    2. KIND_COLLECTOR_RUN_SCHEDULED / _MANUAL — one row per entry in
       collection_history.json, with items_found + items_approved + trigger
    3. KIND_COLLECTOR_ITEM_APPROVED — one row per approved item (we
       synthesize items_approved rows since the original event log was
       per-run, not per-item)

The script reports a per-notebook summary (events synthesized, events
skipped as duplicates) and a grand total.
"""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

# Make backend modules importable when run as a script.
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from config import settings
from storage.notebook_store import notebook_store
from storage.source_store import source_store
from services import activity_ledger


def _safe_iso(ts: str | None) -> str | None:
    """Normalize a timestamp string to ISO. None on parse failure."""
    if not ts:
        return None
    try:
        # source_store stores varied formats; just round-trip through datetime.
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00").replace("+00:00", ""))
        return dt.isoformat()
    except Exception:
        return None


def _event_exists(conn, notebook_id: str, kind: str, ts: str, source_id: str | None = None) -> bool:
    """Check whether an event with the same (notebook, kind, ts, source_id) is
    already in the ledger. Avoids duplicates on re-runs."""
    if source_id:
        row = conn.execute(
            "SELECT 1 FROM activity_events "
            "WHERE notebook_id=? AND kind=? AND ts=? "
            "AND json_extract(payload_json, '$.source_id')=? LIMIT 1",
            (notebook_id, kind, ts, source_id),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM activity_events "
            "WHERE notebook_id=? AND kind=? AND ts=? LIMIT 1",
            (notebook_id, kind, ts),
        ).fetchone()
    return row is not None


def _record_if_new(conn, notebook_id: str, kind: str, actor: str,
                    payload: dict, ts: datetime) -> bool:
    """Insert event if not already present. Returns True if inserted."""
    ts_iso = ts.isoformat()
    source_id = payload.get("source_id")
    if _event_exists(conn, notebook_id, kind, ts_iso, source_id):
        return False
    activity_ledger.record_event(
        notebook_id=notebook_id,
        kind=kind,
        actor=actor,
        payload=payload,
        ts=ts,
    )
    return True


async def backfill_one_notebook(notebook_id: str, notebook_name: str) -> dict:
    """Synthesize ledger events for a single notebook. Returns stats dict."""
    stats = {
        "notebook_id": notebook_id,
        "notebook_name": notebook_name,
        "sources_added": 0,
        "sources_skipped": 0,
        "runs_added": 0,
        "runs_skipped": 0,
        "approvals_added": 0,
        "errors": [],
    }

    # Force schema init through the lazy connection helper.
    conn = activity_ledger._get_conn()

    # ── 1. Sources ────────────────────────────────────────────────────────
    try:
        sources = await source_store.list(notebook_id)
    except Exception as e:
        stats["errors"].append(f"source_store.list failed: {e}")
        sources = []

    for src in sources:
        ts_iso = _safe_iso(src.get("created_at"))
        if not ts_iso:
            stats["sources_skipped"] += 1
            continue
        try:
            ts_dt = datetime.fromisoformat(ts_iso)
            payload = {
                "source_id": src.get("id"),
                "filename": (src.get("filename") or src.get("title") or ""),
                "via": "backfill",
                "type": src.get("type") or src.get("format") or "unknown",
            }
            if _record_if_new(conn, notebook_id, activity_ledger.KIND_SOURCE_ADDED,
                              actor="system", payload=payload, ts=ts_dt):
                stats["sources_added"] += 1
            else:
                stats["sources_skipped"] += 1
        except Exception as e:
            stats["errors"].append(f"source {src.get('id')}: {e}")

    # ── 2. Collection runs ────────────────────────────────────────────────
    history_path = Path(settings.data_dir) / "notebooks" / notebook_id / "collection_history.json"
    if history_path.exists():
        try:
            with open(history_path) as f:
                history = json.load(f)
        except Exception as e:
            stats["errors"].append(f"collection_history.json: {e}")
            history = []

        for entry in (history or []):
            ts_iso = _safe_iso(entry.get("timestamp"))
            if not ts_iso:
                stats["runs_skipped"] += 1
                continue
            try:
                ts_dt = datetime.fromisoformat(ts_iso)
                trigger = entry.get("trigger") or "manual"
                kind = (
                    activity_ledger.KIND_COLLECTOR_RUN_SCHEDULED
                    if trigger in ("scheduled", "first_sweep")
                    else activity_ledger.KIND_COLLECTOR_RUN_MANUAL
                )
                items_found = int(entry.get("items_found") or 0)
                items_approved = int(entry.get("items_approved") or 0)
                items_rejected = int(entry.get("items_rejected") or 0)

                if _record_if_new(conn, notebook_id, kind, actor="@collector", payload={
                    "items_found": items_found,
                    "items_approved": items_approved,
                    "items_rejected": items_rejected,
                    "trigger": trigger,
                    "backfilled": True,
                }, ts=ts_dt):
                    stats["runs_added"] += 1
                else:
                    stats["runs_skipped"] += 1

                # Synthesize per-item approval events (used by collector_dry()
                # to detect growth). We don't have per-item timestamps so
                # we use the run's timestamp.
                for _ in range(items_approved):
                    activity_ledger.record_event(
                        notebook_id=notebook_id,
                        kind=activity_ledger.KIND_COLLECTOR_ITEM_APPROVED,
                        actor="@collector",
                        payload={"trigger": trigger, "backfilled": True},
                        ts=ts_dt,
                    )
                    stats["approvals_added"] += 1
            except Exception as e:
                stats["errors"].append(f"run {entry.get('timestamp')}: {e}")

    return stats


async def run_backfill(status_callback=None) -> dict:
    """Run the full backfill. Returns aggregate stats.

    `status_callback(status, message, progress)` is invoked per notebook
    so callers (e.g. the startup task) can stream progress to the splash
    screen. Optional — no-op when not provided. Progress is 0..100 across
    notebooks (caller can rescale).

    Importable from main.py; the `if __name__ == '__main__'` block below
    wraps this for command-line invocation.
    """
    notebooks = await notebook_store.list()
    grand = {
        "sources_added": 0, "sources_skipped": 0,
        "runs_added": 0, "runs_skipped": 0,
        "approvals_added": 0,
        "notebooks_processed": 0,
    }
    if not notebooks:
        return grand

    total = len(notebooks)
    for i, nb in enumerate(notebooks):
        nb_id = nb.get("id") or nb.get("notebook_id")
        nb_name = nb.get("title") or nb.get("name") or nb_id
        if not nb_id:
            continue
        if status_callback:
            try:
                status_callback(
                    "migrating",
                    f"Migrating notebook history... ({i+1}/{total})",
                    int((i / max(total, 1)) * 100),
                )
            except Exception:
                pass
        s = await backfill_one_notebook(nb_id, nb_name)
        for k in ("sources_added", "sources_skipped", "runs_added", "runs_skipped", "approvals_added"):
            grand[k] += s[k]
        grand["notebooks_processed"] += 1
    return grand


async def main():
    print(f"[backfill] data_dir = {settings.data_dir}")
    print(f"[backfill] ledger table will be created lazily on first event.\n")

    notebooks = await notebook_store.list()
    if not notebooks:
        print("[backfill] No notebooks found — nothing to do.")
        return

    print(f"[backfill] {len(notebooks)} notebooks to process.\n")

    def _cli_status(_status, message, progress):
        print(f"[backfill] {message} ({progress}%)")

    grand = await run_backfill(status_callback=_cli_status)

    print("\n[backfill] Totals:")
    for k, v in grand.items():
        print(f"  {k:<22} {v}")
    print("\n[backfill] Done. Re-run any time — duplicates are skipped.")


if __name__ == "__main__":
    asyncio.run(main())
