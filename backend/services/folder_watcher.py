"""Linked-folder scanner — poll a directory, ingest what's new, never twice.

Polling, not filesystem events. A poll needs no new dependency, survives sleep
and wake without re-arming, cannot miss a file that arrived while the app was
closed, and matches how the Collector already works. Filesystem watching buys
latency we do not need: a 1:1 recorded at 2pm does not have to be searchable at
2:00:01pm.

The scan is deliberately two-tiered:

  TIER 1 (cheap, every file, every pass)  — `scandir` + `stat`. A file whose
    `(mtime, size)` matches the ledger is skipped without being opened. This is
    what keeps a 500-file folder a sub-second operation forever.

  TIER 2 (only for new/changed files)     — read, hash, dedup, ingest.

Ingestion goes through `document_processor.process`, the same entry point as a
drag-and-drop upload. That is the whole point: a recording dropped in a watched
folder is a first-class source — chunked, embedded, entity-extracted,
chattable, podcastable — not a lesser import.

READ-ONLY, always. This module opens files for reading and does nothing else to
them. It never writes, moves, renames or deletes inside a linked folder.
"""
from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import settings
from storage.folder_link_store import folder_link_store

logger = logging.getLogger(__name__)

SCHEDULE_ID = "folder-watch"
DEFAULT_LOOP_SECONDS = 300

# The cadence vocabulary, shared with the Collector so the user learns one set
# of words rather than two. Resolved lazily — importing collection_scheduler at
# module scope would drag the Collector agent in behind it. "manual" means the
# folder is only ever scanned when the user asks.
def _intervals() -> Dict[str, timedelta]:
    try:
        from services.collection_scheduler import CollectionScheduler
        return dict(CollectionScheduler.INTERVALS)
    except Exception:
        return {
            "hourly": timedelta(hours=1), "every_2_hours": timedelta(hours=2),
            "every_4_hours": timedelta(hours=4), "every_8_hours": timedelta(hours=8),
            "twice_daily": timedelta(hours=12), "daily": timedelta(days=1),
            "every_3_days": timedelta(days=3), "weekly": timedelta(weeks=1),
        }


@dataclass
class Candidate:
    """A file the scan would act on, and why."""
    path: str
    name: str
    size: int
    mtime: float
    action: str                      # "ingest" | "skip" | "too_large" | "changed"
    reason: str = ""
    content_hash: Optional[str] = None


@dataclass
class ScanReport:
    link_id: str
    path: str
    scanned: int = 0
    ingested: int = 0
    skipped: int = 0
    failed: int = 0
    pending: int = 0                 # matched, eligible, but over the batch limit
    pending_review: int = 0          # smart folder: analysed, awaiting a human
    error: Optional[str] = None
    files: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "link_id": self.link_id, "path": self.path, "scanned": self.scanned,
            "ingested": self.ingested, "skipped": self.skipped, "failed": self.failed,
            "pending": self.pending, "pending_review": self.pending_review,
            "error": self.error, "files": self.files,
        }


def _matches(name: str, patterns: List[str]) -> bool:
    lowered = name.lower()
    return any(fnmatch.fnmatch(lowered, p.lower()) for p in patterns)


def _iter_files(root: Path, patterns: List[str], recursive: bool):
    """Yield (Path, stat) for matching, readable, non-hidden regular files.

    Hidden files and dot-directories are skipped: `.DS_Store`, editor swap
    files and `.git` are noise, and a recorder app's in-progress write often
    lands as a dotfile first.
    """
    # Probe the root explicitly. os.walk swallows errors by default, so without
    # this a TCC denial would look identical to an empty folder — and a link
    # that can never read anything would report "nothing new" forever.
    with os.scandir(root) as probe:
        entries = list(probe)
    walker = os.walk(root) if recursive else [
        (str(root), [], [e.name for e in entries if not e.is_dir()])
    ]
    for dirpath, dirnames, filenames in walker:
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if fn.startswith("."):
                continue
            if not _matches(fn, patterns):
                continue
            fp = Path(dirpath) / fn
            try:
                st = fp.stat()
            except OSError:
                continue
            if not os.path.isfile(fp):
                continue
            yield fp, st


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FolderWatcher:
    """Scans linked folders on a cadence and ingests what is new."""

    def __init__(self) -> None:
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._scanning: set = set()          # link_ids with a scan in flight

    # ── discovery (no side effects — safe for dry runs) ──────────────────
    def discover(self, link: Dict[str, Any], *, limit: Optional[int] = None) -> List[Candidate]:
        """What a scan WOULD do, without doing any of it.

        This is the dry-run primitive. The Preview UI calls it before a folder
        is ever enabled, so the user sees the real decision on their real files
        at zero risk.
        """
        root = Path(link["path"])
        patterns = link.get("patterns") or ["*.md"]
        ledger = folder_link_store.seen_map(link["id"])
        max_bytes = int(getattr(settings, "folder_link_max_file_mb", 25)) * 1024 * 1024

        out: List[Candidate] = []
        for fp, st in _iter_files(root, patterns, bool(link.get("recursive"))):
            abs_path = str(fp)
            prior = ledger.get(abs_path)
            # TIER 1 — the fast skip. Unchanged since we last ingested it.
            if prior and prior.get("status") == "ingested" \
                    and abs(float(prior["mtime"]) - st.st_mtime) < 1e-6 \
                    and int(prior["size"]) == st.st_size:
                continue
            # "skipped" (a reason was recorded) and "baseline" (the user chose
            # to ingest only files added from now on) are both settled verdicts.
            # Re-deciding them every pass would re-ingest what the user declined.
            if prior and prior.get("status") in ("skipped", "baseline") \
                    and abs(float(prior["mtime"]) - st.st_mtime) < 1e-6 \
                    and int(prior["size"]) == st.st_size:
                continue
            if st.st_size == 0:
                out.append(Candidate(abs_path, fp.name, 0, st.st_mtime, "skip", "empty file"))
                continue
            if st.st_size > max_bytes:
                out.append(Candidate(abs_path, fp.name, st.st_size, st.st_mtime,
                                     "too_large",
                                     f"{st.st_size / 1024 / 1024:.0f} MB exceeds the "
                                     f"{max_bytes // 1024 // 1024} MB limit"))
                continue
            action = "changed" if prior else "ingest"
            reason = "re-ingest: file changed on disk" if prior else ""
            out.append(Candidate(abs_path, fp.name, st.st_size, st.st_mtime, action, reason))
            if limit and len([c for c in out if c.action in ("ingest", "changed")]) >= limit:
                break
        return out

    def mark_baseline(self, link: Dict[str, Any]) -> int:
        """Record every file currently in the folder as already-accounted-for,
        without reading or ingesting any of it.

        This is "only ingest recordings added from now on". It is a ledger
        write, not a scan: the existing files are never opened, so linking a
        folder with 400 old transcripts costs nothing and embeds nothing.
        """
        count = 0
        for c in self.discover(link):
            folder_link_store.record(
                link_id=link["id"], abs_path=c.path, mtime=c.mtime, size=c.size,
                status="baseline", error="present before the folder was linked",
            )
            count += 1
        return count

    # ── the scan ─────────────────────────────────────────────────────────
    async def scan_link(self, link_id: str, *, force: bool = False) -> ScanReport:
        link = folder_link_store.get_link(link_id)
        if not link:
            return ScanReport(link_id=link_id, path="", error="link not found")
        report = ScanReport(link_id=link_id, path=link["path"])

        if link_id in self._scanning:
            report.error = "a scan is already running for this folder"
            return report
        self._scanning.add(link_id)
        try:
            batch = int(getattr(settings, "folder_link_batch_limit", 25))
            try:
                candidates = await asyncio.to_thread(self.discover, link)
            except (OSError, PermissionError) as e:
                # macOS TCC denies with PermissionError even when the folder
                # exists — say exactly that rather than "0 files found", which
                # would read as "nothing new" and hide a broken link forever.
                msg = self._access_error(link["path"], e)
                report.error = msg
                folder_link_store.touch_scan(link_id, error=msg)
                logger.warning(f"[folder-watch] {link['path']}: {msg}")
                return report

            actionable = [c for c in candidates if c.action in ("ingest", "changed")]
            report.scanned = len(candidates)
            for c in candidates:
                if c.action in ("skip", "too_large"):
                    report.skipped += 1
                    folder_link_store.record(
                        link_id=link_id, abs_path=c.path, mtime=c.mtime, size=c.size,
                        status="skipped", error=c.reason,
                    )

            todo = actionable[:batch]
            report.pending = max(0, len(actionable) - len(todo))

            for c in todo:
                ok = await self._ingest_one(link, c, report)
                if not ok and report.error:
                    break

            folder_link_store.touch_scan(link_id, error=report.error,
                                         ingested=report.ingested)
            if report.ingested or report.failed or report.error:
                logger.info(
                    f"[folder-watch] {link['path']}: +{report.ingested} ingested, "
                    f"{report.skipped} skipped, {report.failed} failed, "
                    f"{report.pending} pending"
                )
            return report
        finally:
            self._scanning.discard(link_id)

    @staticmethod
    def _access_error(path: str, e: Exception) -> str:
        if isinstance(e, PermissionError):
            return (f"macOS denied access to {path}. Grant LocalBook Full Disk Access "
                    f"(System Settings → Privacy & Security → Full Disk Access), or move "
                    f"the folder somewhere LocalBook can read.")
        return f"Could not read {path}: {type(e).__name__}: {e}"

    async def _ingest_one(self, link: Dict[str, Any], c: Candidate,
                          report: ScanReport) -> bool:
        """Read → hash → dedup → ingest. Returns False on failure."""
        link_id = link["id"]
        notebook_id = link.get("notebook_id")
        try:
            data = await asyncio.to_thread(Path(c.path).read_bytes)
        except (OSError, PermissionError) as e:
            msg = self._access_error(c.path, e)
            report.failed += 1
            report.error = report.error or msg
            folder_link_store.record(link_id=link_id, abs_path=c.path, mtime=c.mtime,
                                     size=c.size, status="failed", error=msg)
            return False

        digest = _sha256(data)

        # DEDUP, two ways. The ledger catches a file renamed or moved between
        # watched folders. source_store catches content that reached the
        # notebook by some other road entirely.
        prior = folder_link_store.find_hash(digest)
        if prior and prior.get("abs_path") != c.path:
            report.skipped += 1
            folder_link_store.record(
                link_id=link_id, abs_path=c.path, mtime=c.mtime, size=c.size,
                content_hash=digest, source_id=prior.get("source_id"),
                notebook_id=prior.get("notebook_id"), status="skipped",
                error=f"identical content already ingested from {Path(prior['abs_path']).name}",
            )
            report.files.append({"name": c.name, "action": "skipped",
                                 "reason": "duplicate content"})
            return True
        if notebook_id:
            try:
                from storage.source_store import source_store
                dupe = await source_store.find_by_content_hash(digest)
            except Exception:
                dupe = None
            if dupe and dupe.get("notebook_id") == notebook_id:
                report.skipped += 1
                folder_link_store.record(
                    link_id=link_id, abs_path=c.path, mtime=c.mtime, size=c.size,
                    content_hash=digest, source_id=dupe.get("id"),
                    notebook_id=notebook_id, status="skipped",
                    error="already in this notebook",
                )
                report.files.append({"name": c.name, "action": "skipped",
                                     "reason": "already in this notebook"})
                return True

        if not notebook_id:
            # ── Smart Folder ────────────────────────────────────────────
            # Work out who and what this is, then either apply a rule the USER
            # wrote or put a card in front of them. There is no third branch,
            # and there must never be one: confidence does not authorise.
            routed = await self._triage_smart(link_id, c, data, digest, report)
            if not routed:
                return True          # queued for review, or unreadable — not ingested
            return await self._ingest_into(link_id, routed, c, data, digest,
                                           report, origin="smart_folder_rule")

        return await self._ingest_into(link_id, notebook_id, c, data, digest,
                                       report, origin="linked_folder")

    async def _ingest_into(self, link_id: str, notebook_id: str, c: Candidate,
                           data: bytes, digest: str, report: ScanReport,
                           *, origin: str) -> bool:
        """Create the source, stamp provenance, apply the post-ingest treatment,
        and write the ledger row. The one place a folder file becomes a source."""
        try:
            from services.document_processor import document_processor
            source = await document_processor.process(
                content=data, filename=c.name, notebook_id=notebook_id
            )
            # `process` returns {"source_id": ...} — NOT {"id": ...}. Reading the
            # wrong key silently yielded None, which then flowed into the
            # provenance stamp, the ledger and the tagger without one error:
            # everything "succeeded" on a source that did not exist.
            source_id = source.get("source_id") or source.get("id")
            if not source_id:
                raise ValueError(
                    f"document_processor.process returned no source id for {c.name} "
                    f"(keys: {sorted(source)})"
                )
            # Provenance + the hash, so a later drag-and-drop of the same file
            # is recognised as a duplicate by source_store too.
            try:
                from storage.source_store import source_store
                await source_store.update(notebook_id, source_id, {
                    "content_hash": digest,
                    "origin": origin,
                    "folder_link_id": link_id,
                    "source_path": c.path,
                })
            except Exception as e:
                logger.warning(f"[folder-watch] provenance stamp failed for {c.name}: {e}")

            # The full treatment — tags, timeline, image pass, capture event.
            # A folder-ingested file is a first-class source or it is nothing;
            # skipping this is what makes an import feel second-class.
            try:
                from services.post_ingest import finalize_source
                from storage.source_store import source_store as _ss
                stored = await _ss.get(source_id) or {}
                await finalize_source(
                    notebook_id, source_id, c.name,
                    stored.get("content") or "",
                    raw_bytes=data,
                    origin=origin,
                )
            except Exception as e:
                logger.warning(f"[folder-watch] post-ingest failed for {c.name}: {e}")

            folder_link_store.record(
                link_id=link_id, abs_path=c.path, mtime=c.mtime, size=c.size,
                content_hash=digest, source_id=source_id, notebook_id=notebook_id,
                status="ingested",
            )
            report.ingested += 1
            report.files.append({"name": c.name, "action": "ingested",
                                 "source_id": source_id,
                                 "chunks": source.get("chunks")})
            return True
        except Exception as e:
            msg = f"{type(e).__name__}: {e}"[:300]
            report.failed += 1
            folder_link_store.record(link_id=link_id, abs_path=c.path, mtime=c.mtime,
                                     size=c.size, content_hash=digest,
                                     status="failed", error=msg)
            report.files.append({"name": c.name, "action": "failed", "reason": msg})
            logger.warning(f"[folder-watch] ingest failed for {c.name}: {msg}")
            return False

    async def ingest_approved(self, *, link_id: str, abs_path: str,
                              notebook_id: str, origin: str = "smart_folder") -> dict:
        """Ingest one already-analysed file into the notebook a human chose.

        Separate from the scan so an approval is immediate and foreground —
        the user clicked, so they should see the source appear, not wait for
        the next idle window.
        """
        path = Path(abs_path)
        if not path.is_file():
            return {"ok": False, "error": f"{path.name} is no longer on disk"}
        try:
            data = await asyncio.to_thread(path.read_bytes)
        except (OSError, PermissionError) as e:
            return {"ok": False, "error": self._access_error(abs_path, e)}

        digest = _sha256(data)
        c = Candidate(abs_path, path.name, len(data), path.stat().st_mtime, "ingest")
        report = ScanReport(link_id=link_id, path=str(path.parent))
        ok = await self._ingest_into(link_id, notebook_id, c, data, digest,
                                     report, origin=origin)
        if not ok:
            return {"ok": False, "error": report.error or "ingest failed"}
        folder_link_store.touch_scan(link_id, ingested=report.ingested)
        return {"ok": True, "ingested": report.ingested,
                "source_id": next((f.get("source_id") for f in report.files), None)}

    async def _triage_smart(self, link_id: str, c: Candidate, data: bytes,
                            digest: str, report: ScanReport) -> Optional[str]:
        """Smart-folder destination decision. Returns a notebook id ONLY when a
        user-authored rule authorised it; otherwise queues and returns None."""
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            text = ""
        if not text.strip():
            folder_link_store.record(link_id=link_id, abs_path=c.path, mtime=c.mtime,
                                     size=c.size, content_hash=digest,
                                     status="skipped", error="no readable text")
            report.skipped += 1
            return None

        try:
            from services import smart_folder
            outcome = await smart_folder.triage(
                link_id=link_id, abs_path=c.path, filename=c.name, text=text,
                size=c.size, mtime=c.mtime, content_hash=digest,
            )
        except Exception as e:
            # Analysis failing must not silently drop a recording. Record it as
            # awaiting review with the reason, so it surfaces rather than
            # vanishing between a scan and a queue.
            msg = f"could not analyse: {type(e).__name__}: {e}"[:200]
            logger.warning(f"[folder-watch] {c.name}: {msg}")
            folder_link_store.record(link_id=link_id, abs_path=c.path, mtime=c.mtime,
                                     size=c.size, content_hash=digest,
                                     status="pending_route", error=msg)
            report.files.append({"name": c.name, "action": "pending_route", "reason": msg})
            return None

        if outcome.get("action") == "auto" and outcome.get("notebook_id"):
            report.files.append({"name": c.name, "action": "auto_routed",
                                 "rule_id": outcome.get("rule_id")})
            return outcome["notebook_id"]

        folder_link_store.record(link_id=link_id, abs_path=c.path, mtime=c.mtime,
                                 size=c.size, content_hash=digest,
                                 status="pending_route")
        report.pending_review = getattr(report, "pending_review", 0) + 1
        report.files.append({
            "name": c.name, "action": "pending_review",
            "suggested": outcome.get("suggested_name"),
            "confidence": outcome.get("confidence"),
        })
        return None

    # ── cadence ──────────────────────────────────────────────────────────
    @staticmethod
    def is_due(link: Dict[str, Any], now: Optional[datetime] = None) -> bool:
        if not link.get("enabled"):
            return False
        freq = link.get("frequency") or "hourly"
        if freq == "manual":
            return False
        last = link.get("last_scan_at")
        if not last:
            return True
        try:
            last_dt = datetime.fromisoformat(last)
        except Exception:
            return True
        interval = _intervals().get(freq, timedelta(hours=1))
        return (now or datetime.utcnow()) - last_dt >= interval

    async def scan_due(self) -> List[ScanReport]:
        reports = []
        for link in folder_link_store.list_links():
            if not self.is_due(link):
                continue
            reports.append(await self.scan_link(link["id"]))
        return reports

    # ── background loop ──────────────────────────────────────────────────
    def start_background_task(self) -> None:
        if self._running:
            return
        self._running = True
        from utils.tasks import safe_create_task
        self._task = safe_create_task(self._loop(), name="folder-watch")

    def stop(self) -> None:
        self._running = False

    async def _loop(self) -> None:
        """Wake on a cadence, enqueue due scans on the presence-gated worker.

        The loop itself never ingests. It hands work to `enrichment_worker`,
        which holds it until the user is idle — so a scan can never take the
        machine out from under a chat on a 16 GB Mac. Coalesced by key, so a
        slow drain cannot stack duplicate scans.
        """
        await asyncio.sleep(45)   # let startup settle
        from services.enrichment_worker import enrichment_worker
        from services.enrichment_jobs import EnrichmentJob, JobTier

        while self._running:
            try:
                from services.schedule_store import schedule_store
                if schedule_store.is_enabled(SCHEDULE_ID):
                    for link in folder_link_store.list_links():
                        if not self.is_due(link):
                            continue
                        lid = link["id"]
                        enrichment_worker.enqueue(EnrichmentJob(
                            key=f"folder-watch:{lid}",
                            tier=JobTier.DAYDREAM,
                            factory=self._job_factory(lid),
                            label=f"folder-watch:{Path(link['path']).name}",
                        ))
                interval = schedule_store.get_interval(
                    SCHEDULE_ID, getattr(settings, "folder_watch_interval_seconds",
                                         DEFAULT_LOOP_SECONDS))
            except Exception as e:
                # Never raise out of a background loop.
                logger.warning(f"[folder-watch] loop error (continuing): {e}")
                interval = DEFAULT_LOOP_SECONDS
            await asyncio.sleep(max(30, float(interval)))

    def _job_factory(self, link_id: str):
        async def _run():
            try:
                report = await self.scan_link(link_id)
            except Exception as e:
                logger.warning(f"[folder-watch] scan error for {link_id}: {e}")
                return
            # A first scan of a 100-file folder hits the batch limit. Waiting for
            # the next cadence tick would stretch that backfill over four hours
            # for no reason — the limit exists to keep any single pass small, not
            # to slow the whole job down. Re-enqueue instead: the worker is
            # presence-gated, so the remainder drains exactly as fast as the
            # machine is idle, and stops the moment the user comes back.
            if report.pending > 0 and not report.error:
                from services.enrichment_worker import enrichment_worker
                from services.enrichment_jobs import EnrichmentJob, JobTier
                enrichment_worker.enqueue(EnrichmentJob(
                    key=f"folder-watch:{link_id}",
                    tier=JobTier.DAYDREAM,
                    factory=self._job_factory(link_id),
                    label=f"folder-watch:backfill({report.pending} left)",
                ))
        return _run


folder_watcher = FolderWatcher()
